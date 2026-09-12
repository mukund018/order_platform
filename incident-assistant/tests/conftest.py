import sys
from pathlib import Path

# incident-assistant is a scripts folder, not an installed package, so make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
