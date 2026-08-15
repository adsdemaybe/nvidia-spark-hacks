#!/usr/bin/env bash
# Install ngspice into .tools/ngspice without root.
#
# L7 needs a simulator. `sudo apt install ngspice` is the right answer on a machine
# where you have root; on the Spark the agent does not, and a stage that cannot run is
# a stage that silently verifies nothing. `apt-get download` needs no privileges, and
# `dpkg -x` unpacks wherever you point it, so the whole toolchain lands in a gitignored
# directory that `locateNgspice()` looks in after PATH.
#
# The one thing that needs fixing after extraction: ngspice's spinit hardcodes absolute
# paths to its code-model libraries (/usr/lib/.../analog.cm). Relocated, every one of
# those fails to load and every run prints six errors. The analyses still work — they
# are XSPICE extensions we do not use — but a log full of "Error:" lines is a log
# nobody reads, and eventually one real error hides in it.
#
#   ./tools/vendor-ngspice.sh          # into ./.tools/ngspice
set -euo pipefail

dest="${1:-$(cd "$(dirname "$0")/.." && pwd)/.tools/ngspice}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "downloading ngspice (no root required) …"
(cd "$work" && apt-get download ngspice libngspice0 >/dev/null)

echo "extracting to $dest …"
rm -rf "$dest"
mkdir -p "$dest"
for deb in "$work"/*.deb; do dpkg -x "$deb" "$dest"; done

bin="$dest/usr/bin/ngspice"
[ -x "$bin" ] || { echo "ngspice binary not found at $bin" >&2; exit 1; }

# Repoint the code-model paths at the relocated tree.
libdir="$(dirname "$(find "$dest/usr/lib" -name 'libngspice.so.0' | head -1)")"
spinit="$dest/usr/share/ngspice/scripts/spinit"
if [ -f "$spinit" ]; then
  sed -i "s|codemodel /usr/lib/[^/]*/ngspice/|codemodel $libdir/ngspice/|g" "$spinit"
  echo "repointed codemodel paths in spinit -> $libdir/ngspice/"
fi

echo -n "verifying: "
LD_LIBRARY_PATH="$libdir" SPICE_LIB_DIR="$dest/usr/share/ngspice" "$bin" --version 2>&1 |
  grep -m1 ngspice- || { echo "version check failed" >&2; exit 1; }

# A real solve, not just --version: 7.4 V across two equal resistors must read 3.7 V.
cat > "$work/selftest.cir" <<'EOF'
* vendor self-test
V1 vin 0 DC 7.4
R1 vin vout 1k
R2 vout 0 1k
.op
.control
run
print v(vout)
.endc
.end
EOF
out=$(LD_LIBRARY_PATH="$libdir" SPICE_LIB_DIR="$dest/usr/share/ngspice" \
  "$bin" -b "$work/selftest.cir" 2>&1 | grep -m1 'v(vout)' || true)
echo "self-test: $out"
case "$out" in
  *3.7*) echo "ngspice vendored OK -> $dest" ;;
  *) echo "self-test did not produce 3.7 V — refusing to declare this working" >&2; exit 1 ;;
esac
