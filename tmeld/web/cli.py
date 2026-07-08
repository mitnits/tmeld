"""bmeld: `tmeld --web` under its own name.

Kept as a separate console script -- muscle memory, the manpage, the Debian
package and the `.gitconfig` mergetool stanzas all name it -- but it is a thin
alias now. Everything lives in tmeld/cli.py, so a flag added there appears in
both front-ends at once.
"""

from typing import Optional, Sequence

from tmeld.cli import main as _main
from tmeld.cli import osc8  # noqa: F401  (re-exported; probes import it)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return _main(argv, prog="bmeld", default_web=True)


if __name__ == "__main__":
    raise SystemExit(main())
