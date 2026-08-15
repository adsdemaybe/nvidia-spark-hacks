#!/usr/bin/env bash
# Install KiCad 8.0.9 (kicad-cli) into .tools/kicad without root.
#
# Why this exists, and why it says 8 rather than 9:
#
#   - `apt` on this box (noble/24.04, aarch64) offers only KiCad 7.0.11, which is too
#     old for the `sch erc --format json` and SPICE-netlist exports the ladder wants.
#   - The KiCad **9.0** PPA publishes **amd64 only** — there is no arm64 build at all.
#   - The KiCad **8.0** PPA does publish arm64, at exactly **8.0.9** — which is also the
#     version Microsoft's SchGen pins for its schematic API (plan §7).
#
# So 8.0.9 is not a compromise here; on aarch64 it is both the newest available and the
# one path B needs. The build is for oracular (24.10) because no noble arm64 build was
# ever published, so its dependency closure is assembled by hand: OCCT 7.8 from
# oracular's own archive (noble ships 7.6), libngspice-kicad from the KiCad PPA, and the
# rest from noble.
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
PORTS=http://ports.ubuntu.com/ubuntu-ports/pool/universe/o/opencascade
SERIES=24.10

fetch() { # url
  local f="$work/$(basename "$1")"
  [ -s "$f" ] || curl -sL --fail -m 900 -o "$f" "$1" || { echo "  ! failed: $1" >&2; return 1; }
  echo "$f"
}

echo "fetching KiCad 8.0.9 (arm64, ${SERIES} build) …"
fetch "$PPA/k/kicad/kicad_8.0.9-0~ubuntu${SERIES}.1_arm64.deb" >/dev/null
fetch "$PPA/n/ngspice-kicad/libngspice-kicad_0.1-43~202409291906+2af390f0b~ubuntu${SERIES}.1_arm64.deb" >/dev/null

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
EXR=http://ports.ubuntu.com/ubuntu-ports/pool/universe/o/openexr
fetch "$EXR/libopenexr-3-1-30_3.1.5-5.1build3_arm64.deb" >/dev/null || true
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
echo "  kicad-cli: $bin"
echo "  set LD_LIBRARY_PATH=$libdir"
