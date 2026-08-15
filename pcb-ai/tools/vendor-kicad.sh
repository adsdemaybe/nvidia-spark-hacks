#!/usr/bin/env bash
# Install kicad-cli into .tools/kicad without root. Defaults to **KiCad 9**.
#
# Finding the right build took three wrong turns worth recording, because each looks
# like a dead end:
#
#   1. `apt` on this box (noble/24.04, aarch64) offers only KiCad 7.0.11 — too old.
#   2. The KiCad **9.0 PPA is amd64-only**, which reads as "no KiCad 9 on ARM".
#   3. But Ubuntu's **own universe archive** ships `kicad_9.0.3+dfsg-1_arm64.deb`
#      (and 9.0.8). The PPA is not the only source, and it is the wrong one here.
#
# So KiCad 9 does run on aarch64. 9.0.3 rather than 9.0.8 because 9.0.8 wants
# libpython3.14 and OCCT 7.9 — a 25.10 userland — while 9.0.3 needs only OCCT 7.8,
# libgit2-1.9 and libpython3.13, all fetchable beside a noble system.
#
# Set KICAD_MAJOR=8 for KiCad 8.0.9 from the PPA instead. That build exists for arm64
# too and is the exact version Microsoft SchGen pins (plan §7), so path B wants it — but
# it cannot read the KiCad 9 files this pipeline emits.
#
# Two traps in the dependency closure, both of which look like "package not available":
#   - **ESM shadowing.** `apt-get download` resolves to Ubuntu Pro builds for OpenEXR and
#     mbedtls and gets 401 Unauthorized, even though the public archive has the same
#     library. Those are fetched from the public pool by URL instead.
#   - **The .kiface plugins** pull a second wave of libraries that the top-level Depends
#     never mentions, so the resolver loop below runs the binary and follows what it asks
#     for.
#
# kicad-cli loads its real work through `.kiface` plugins, and each one pulls further
# shared libraries. Rather than hard-coding a list that will rot, this runs the binary,
# reads whichever soname it complains about, guesses the providing package, fetches it,
# and repeats until it stops complaining.
#
#   ./tools/vendor-kicad.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:-$root/.tools/kicad}"
work="$dest/.debs"
mkdir -p "$work" "$dest"

PPA=https://ppa.launchpadcontent.net/kicad/kicad-8.0-releases/ubuntu/pool/main
POOL=http://ports.ubuntu.com/ubuntu-ports/pool
PORTS=$POOL/universe/o/opencascade
SERIES=24.10
KICAD_MAJOR="${KICAD_MAJOR:-9}"

fetch() { # url
  local f="$work/$(basename "$1")"
  [ -s "$f" ] || curl -sL --fail -m 900 -o "$f" "$1" || { echo "  ! failed: $1" >&2; return 1; }
  echo "$f"
}

if [ "$KICAD_MAJOR" = "9" ]; then
  echo "fetching KiCad 9.0.3 (arm64, Ubuntu universe) …"
  fetch "$POOL/universe/k/kicad/kicad_9.0.3+dfsg-1_arm64.deb" >/dev/null
  # 9.0.3 links a newer git2 and python than noble carries.
  fetch "$POOL/main/libg/libgit2/libgit2-1.9_1.9.1+ds-1ubuntu1_arm64.deb" >/dev/null
  fetch "$POOL/main/p/python3.13/libpython3.13_3.13.3-1ubuntu0.5_arm64.deb" >/dev/null
  ( cd "$work" && apt-get download libnng1 libngspice0 >/dev/null 2>&1 ) || true
else
  echo "fetching KiCad 8.0.9 (arm64, ${SERIES} PPA build) …"
  fetch "$PPA/k/kicad/kicad_8.0.9-0~ubuntu${SERIES}.1_arm64.deb" >/dev/null
  fetch "$PPA/n/ngspice-kicad/libngspice-kicad_0.1-43~202409291906+2af390f0b~ubuntu${SERIES}.1_arm64.deb" >/dev/null
fi

echo "fetching OCCT 7.8 (noble ships 7.6) …"
for m in data-exchange foundation modeling-algorithms modeling-data ocaf visualization; do
  fetch "$PORTS/libocct-${m}-7.8_7.8.1+dfsg1-3_arm64.deb" >/dev/null
done

echo "fetching the rest from noble …"
# The full closure, determined empirically by running kicad-cli until it stopped
# complaining. The resolver loop below still exists for when this list drifts, but a
# known-good list means a fresh machine does not have to rediscover it one round-trip at
# a time.
NOBLE_DEPS="libwxbase3.2-1t64 libwxgtk3.2-1t64 libwxgtk-gl3.2-1t64
            libgit2-1.7 libglew2.2 libodbc2 libtbb12 libtbbmalloc2
            libhttp-parser2.9 libssh2-1t64 libfreeimage3 libminizip1t64 libjxr0t64 libraw23t64 libopenjp2-7 libwebpmux3
            libimath-3-1-29t64"

# OpenEXR's runtime is ESM-only in noble (Ubuntu Pro), so `apt-get download` cannot
# fetch it without a subscription. The same binary is in the public ports pool, which
# needs no credentials. It arrives here through OCCT's visualisation module, which
# _pcbnew.kiface links whether or not the command is going to render anything.
EXR=$POOL/universe/o/openexr
fetch "$EXR/libopenexr-3-1-30_3.1.5-5.1build3_arm64.deb" >/dev/null || true

# mbedtls is ESM-shadowed the same way, and libgit2 needs it.
MBED=$POOL/universe/m/mbedtls
for f in libmbedtls14t64 libmbedcrypto7t64 libmbedx509-1t64; do
  fetch "$MBED/${f}_2.28.8-1_arm64.deb" >/dev/null || true
done
for pkg in $NOBLE_DEPS; do
  ( cd "$work" && apt-get download "$pkg" >/dev/null 2>&1 ) || echo "  ! could not fetch $pkg" >&2
done

extract_all() { for d in "$work"/*.deb; do dpkg -x "$d" "$dest" 2>/dev/null || true; done; }
extract_all

bin="$dest/usr/bin/kicad-cli"
libdir="$dest/usr/lib/aarch64-linux-gnu"
[ -x "$bin" ] || { echo "kicad-cli not found at $bin" >&2; exit 1; }

# Resolve missing shared libraries by asking the binary what it is missing.
#
# A soname does not name its package, so this guesses the common Debian spellings —
# libhttp_parser.so.2.9 -> libhttp-parser2.9, libfoo.so.5 -> libfoo5 / libfoo5t64 — and
# keeps whichever apt actually has. Anything unguessable is reported rather than
# silently skipped.
guess_packages() { # soname -> candidate package names
  local so="$1" stem ver
  stem="${so%%.so*}"; stem="${stem#lib}"
  ver="${so#*.so.}"
  local dashed="${stem//_/-}"
  echo "lib${dashed}${ver}" "lib${dashed}${ver%%.*}" "lib${dashed}${ver}t64" \
       "lib${dashed}${ver%%.*}t64" "lib${dashed}" "lib${stem}${ver%%.*}" \
       "lib${dashed}-${ver%%.*}" "lib${dashed}${ver//./-}" \
       "lib${dashed}-${ver%%.*}t64" "lib${dashed}-${ver}"
}

# The probe has to be a *real* DRC run. `--help` never loads _pcbnew.kiface, so probing
# with it reports success while the plugin is still missing half its libraries — which
# is exactly how this script first "passed" and then failed on first use.
probe_pcb="$work/probe.kicad_pcb"
cat > "$probe_pcb" <<'PROBE'
(kicad_pcb (version 20240108) (generator "vendor-probe")
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
  (setup)
)
PROBE

for attempt in $(seq 1 20); do
  missing=$(LD_LIBRARY_PATH="$libdir" "$bin" pcb drc -o /dev/null "$probe_pcb" 2>&1 |
    grep -oE '[a-zA-Z0-9_.+-]+\.so\.[0-9.]+: cannot open' | head -1 | cut -d: -f1 || true)
  [ -z "$missing" ] && break
  echo "  missing $missing"
  got=""
  for cand in $(guess_packages "$missing"); do
    if apt-cache policy "$cand" 2>/dev/null | grep -q 'Candidate: [0-9]'; then
      ( cd "$work" && apt-get download "$cand" >/dev/null 2>&1 ) && got="$cand" && break
    fi
  done
  if [ -z "$got" ]; then
    echo "  ! no package found providing $missing — install it manually and re-run" >&2
    break
  fi
  echo "  + $got"
  extract_all
done

# KiCad resolves its schemas and libraries from an absolute /usr/share prefix, which
# does not exist in a relocated tree. Harmless for DRC/ERC, noisy otherwise.
export KICAD_DATA="$dest/usr/share/kicad"
export KICAD9_DATA="$dest/usr/share/kicad"

echo -n "verifying: kicad-cli "
LD_LIBRARY_PATH="$libdir" "$bin" --version || { echo "version check failed" >&2; exit 1; }

# A real check, not just --version: DRC must actually load pcbnew and run.
probe=$(LD_LIBRARY_PATH="$libdir" "$bin" pcb drc -o /dev/null "$probe_pcb" 2>&1 || true)
if echo "$probe" | grep -q "Failed to load"; then
  echo "kicad-cli runs but cannot load its pcbnew plugin:" >&2
  echo "$probe" | grep "Failed to load" | head -2 >&2
  exit 1
fi

rm -rf "$work"
echo "KiCad vendored OK -> $dest"
echo "  major: $KICAD_MAJOR"
echo "  kicad-cli: $bin"
echo "  set LD_LIBRARY_PATH=$libdir"
