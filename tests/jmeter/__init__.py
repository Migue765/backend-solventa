"""Apache JMeter integration tests against the application's real HTTP services."""

import sys
from pathlib import Path

# Standalone CLIs must use this checkout, not another editable service installation.
_root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(source) for source in sorted((_root / "services").glob("*/src"))]
