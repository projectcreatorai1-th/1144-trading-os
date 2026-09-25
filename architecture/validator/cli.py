"""Validator CLI: python -m architecture.validator"""
from __future__ import annotations

import sys

from architecture.validator import run_architecture_validation


def main(argv: list[str] | None = None) -> int:
    root = None
    args = argv if argv is not None else sys.argv[1:]
    if args:
        root = args[0]
    result = run_architecture_validation(root)
    print(result.render())
    status = result.status
    if status == "FAIL":
        return 1
    if status == "WARNING":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
