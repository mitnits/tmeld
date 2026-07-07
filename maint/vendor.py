#!/usr/bin/env python3
"""Vendor Meld engine files from upstream/ into tmeld/_vendor/meld/.

Files are copied verbatim except for two mechanical rewrites:
  * meld-internal imports are repointed into the vendored package
  * gi.repository imports are repointed to our shim (tmeld/_vendor/gi_shim.py)

Records the upstream commit in tmeld/_vendor/UPSTREAM. Run from the repo
root: python maint/vendor.py
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UPSTREAM = REPO / "upstream"
VENDOR = REPO / "tmeld" / "_vendor"

# Upstream-relative paths to vendor. Extend as later phases need more.
FILES = [
    "meld/matchers/__init__.py",
    "meld/matchers/myers.py",
    "meld/matchers/diffutil.py",
    "meld/matchers/merge.py",
    "meld/matchers/helpers.py",
    "meld/filters.py",
    "meld/vc/__init__.py",
    "meld/vc/_vc.py",
    "meld/vc/git.py",
    "meld/vc/bzr.py",
    "meld/vc/cvs.py",
    "meld/vc/darcs.py",
    "meld/vc/mercurial.py",
    "meld/vc/svn.py",
]

REWRITES = [
    (re.compile(r"^(\s*)from meld\."), r"\1from tmeld._vendor.meld."),
    (re.compile(r"^(\s*)import meld\."), r"\1import tmeld._vendor.meld."),
    (
        re.compile(r"^from gi\.repository import (.+)$"),
        r"from tmeld._vendor.gi_shim import \1",
    ),
]


def main() -> int:
    if not UPSTREAM.is_dir():
        print("upstream/ checkout missing; clone Meld there first", file=sys.stderr)
        return 1

    commit = subprocess.run(
        ["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    for rel in FILES:
        src = UPSTREAM / rel
        dst = VENDOR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
        out = []
        for line in lines:
            for pattern, repl in REWRITES:
                new = pattern.sub(repl, line)
                if new != line:
                    line = new
                    break
            out.append(line)
        dst.write_text("".join(out), encoding="utf-8", newline="\n")
        print(f"vendored {rel}")

    # Package inits for the vendored tree
    for init in [VENDOR / "meld" / "__init__.py"]:
        if not init.exists():
            init.write_text("", encoding="utf-8")

    (VENDOR / "UPSTREAM").write_text(
        f"repository: https://gitlab.gnome.org/GNOME/meld.git\n"
        f"commit: {commit}\n"
        f"files:\n" + "".join(f"  - {f}\n" for f in FILES),
        encoding="utf-8", newline="\n",
    )
    print(f"pinned upstream commit {commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
