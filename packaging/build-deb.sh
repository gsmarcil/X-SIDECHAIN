#!/bin/sh
# Build a .deb from the current tree.
#
# The package carries pure Python and data files only, so it is architecture
# independent and its single dependency is the interpreter. Nothing is
# downloaded: the staging tree is assembled from this repository.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
outdir=${1:-$root/dist}

version=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/src/x_sidechain/__init__.py")
[ -n "$version" ] || { echo "cannot read the version" >&2; exit 1; }

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

sitedir=$stage/usr/lib/python3/dist-packages
mkdir -p "$sitedir" "$stage/usr/bin" "$stage/DEBIAN" \
         "$stage/usr/share/applications" \
         "$stage/usr/share/man/man1" \
         "$stage/usr/share/doc/x-sidechain"

# The importable package, without caches or test leftovers.
cp -r "$root/src/x_sidechain" "$sitedir/x_sidechain"
find "$sitedir" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$stage/usr/bin/x-sidechain" <<'LAUNCHER'
#!/usr/bin/python3
import sys
from x_sidechain.__main__ import main
sys.exit(main())
LAUNCHER
chmod 755 "$stage/usr/bin/x-sidechain"

install -m 644 "$root/packaging/x-sidechain.desktop" "$stage/usr/share/applications/"
gzip -9nc "$root/packaging/x-sidechain.1" > "$stage/usr/share/man/man1/x-sidechain.1.gz"
chmod 644 "$stage/usr/share/man/man1/x-sidechain.1.gz"

for size in 16 24 32 48 64 128 256; do
  dir=$stage/usr/share/icons/hicolor/${size}x${size}/apps
  mkdir -p "$dir"
  install -m 644 "$root/packaging/icons/x-sidechain-${size}.png" "$dir/x-sidechain.png"
done

install -m 644 "$root/packaging/copyright" "$stage/usr/share/doc/x-sidechain/copyright"
gzip -9nc "$root/README.md" > "$stage/usr/share/doc/x-sidechain/README.md.gz"
chmod 644 "$stage/usr/share/doc/x-sidechain/README.md.gz"
if [ -f "$root/CHANGELOG.md" ]; then
  gzip -9nc "$root/CHANGELOG.md" > "$stage/usr/share/doc/x-sidechain/changelog.gz"
  chmod 644 "$stage/usr/share/doc/x-sidechain/changelog.gz"
fi

installed_kb=$(du -sk "$stage" | cut -f1)
sed -e "s/@VERSION@/$version/" -e "s/@INSTALLED_SIZE@/$installed_kb/" \
    "$root/packaging/control.in" > "$stage/DEBIAN/control"
install -m 755 "$root/packaging/postinst" "$stage/DEBIAN/postinst"
install -m 755 "$root/packaging/postrm" "$stage/DEBIAN/postrm"

find "$stage" -type d -exec chmod 755 {} +
find "$sitedir" -type f -exec chmod 644 {} +

mkdir -p "$outdir"
deb=$outdir/x-sidechain_${version}_all.deb
dpkg-deb --root-owner-group --build "$stage" "$deb" >/dev/null
echo "$deb"
