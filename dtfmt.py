"""Cross-platform strftime for the no-pad flag.

Windows spells "no zero padding" as %#d / %#I; glibc spells it %-d / %-I, and
each platform errors or misformats on the other's spelling. The codebase writes
patterns in the Windows form because that is where it was born; this translates
them at call time so the same source runs on the Windows scheduled task and in a
Linux container.
"""

import sys

_NO_PAD = "#" if sys.platform.startswith("win") else "-"


def pfmt(dt, pattern: str) -> str:
    """strftime, with %#x rewritten to the local platform's no-pad flag."""
    return dt.strftime(pattern.replace("%#", "%" + _NO_PAD))
