from __future__ import annotations

import sys
from pathlib import Path

# The plugin ships from plugins/compactor/, so tests import its `compactor` package from there.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent / "plugins" / "compactor")
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)
