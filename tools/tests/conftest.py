import sys
from pathlib import Path

# The tools are scripts, not an installed package, so make them importable by name.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
