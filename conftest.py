"""Root conftest: ensures the project root is importable for contract tests.

The kernel package is literally named ``platform``, which shadows the stdlib
``platform`` module once the repo root lands on sys.path. If the stdlib module
was already imported and cached by the test runner's own startup (recent pytest
versions import uuid -> platform.system() during bootstrap), evict it so that
imports inside the test session resolve to THIS project's package instead.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_cached = sys.modules.get("platform")
if _cached is not None and not hasattr(_cached, "__path__"):
    # cached stdlib module (a plain module, not a package) - evict it so the
    # project package wins after ROOT is inserted below. Modules that already
    # hold a reference (e.g. uuid) keep their own binding and stay unaffected.
    del sys.modules["platform"]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
