#!/usr/bin/env python3
"""Hook launcher: `python3 run.py <event>`. Fails open: any error, even an import error, exits 0."""
from __future__ import annotations

import os
import sys


def _utf8_stdio() -> None:
    """Hook payloads are UTF-8; don't let a legacy console encoding (e.g. cp1252) mangle them."""
    for stream, kw in ((sys.stdin, {"encoding": "utf-8"}),
                       (sys.stdout, {"encoding": "utf-8", "errors": "replace"}),
                       (sys.stderr, {"encoding": "utf-8", "errors": "replace"})):
        try:
            stream.reconfigure(**kw)
        except (AttributeError, ValueError, OSError):
            pass


def _run() -> int:
    _utf8_stdio()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from compactor.hooks import dispatch

    return dispatch(sys.argv[1] if len(sys.argv) > 1 else "")


if __name__ == "__main__":
    try:
        code = _run()
    except Exception:
        code = 0
    sys.exit(code)
