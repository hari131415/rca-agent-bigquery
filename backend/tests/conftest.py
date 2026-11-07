"""Make the repo root importable so tests can import data.loaders."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
