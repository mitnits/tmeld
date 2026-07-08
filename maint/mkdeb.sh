#!/bin/sh
# Build an installable .deb, stamped so repeated builds upgrade cleanly.
#
#   maint/mkdeb.sh            -> ../tmeld_<version>+<date>.<sha>_all.deb
#
# The upstream version alone (0.4.0) never changes between releases, so dpkg
# would refuse to "upgrade" a rebuilt package. Stamping with the commit date
# and short sha gives a monotonically increasing version that still sorts
# below the next real release:
#
#   0.4.0  <  0.4.0+20260708.abc1234  <  0.5.0
#
# Building needs network: the bundled Textual is fetched from PyPI.
set -eu
cd "$(dirname "$0")/.."

# The stamp goes into debian/changelog, which is tracked. Put it back
# afterwards, or the next build sees a dirty tree and stamps ".dirty".
restore() { [ -n "${in_git:-}" ] && git checkout -- debian/changelog 2>/dev/null || true; }
trap restore EXIT INT TERM

# read __version__ the same way the package does, not the first quoted string
base=$(python3 -c 'import re; print(re.search(r"^__version__\s*=\s*[\"\x27]([^\"\x27]+)", open("tmeld/__init__.py").read(), re.M).group(1))')
# Stamp from git when we have it; a source tarball has no history, so fall
# back to today's date. Either way the version rises with each build.
if git rev-parse --git-dir >/dev/null 2>&1; then
    in_git=1
    stamp=$(git log -1 --format=%cd --date=format:%Y%m%d)
    sha=$(git rev-parse --short=7 HEAD)
    git diff --quiet && dirty= || dirty=".dirty"
    version="${base}+${stamp}.${sha}${dirty}"
else
    version="${base}+$(date -u +%Y%m%d%H%M)"
fi

echo "==> building tmeld ${version}"
python3 - "$version" <<'PY'
import re, sys
version = sys.argv[1]
path = "debian/changelog"
text = open(path).read()
head, rest = text.split("\n", 1)
open(path, "w").write(re.sub(r"^tmeld \([^)]*\)", f"tmeld ({version})", head) + "\n" + rest)
PY

dpkg-buildpackage -us -uc -b
deb="../tmeld_${version}_all.deb"

echo
echo "==> $deb"
echo "    install:  sudo apt install $deb     # pulls Recommends (python3-aiohttp)"
echo "    or:       sudo dpkg -i $deb         # bmeld will need aiohttp separately"
echo "    copy to another machine and repeat; dpkg upgrades in place."
