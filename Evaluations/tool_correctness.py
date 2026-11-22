"""Run the MAS and evaluate Tool Correctness directly."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Evaluations.runner import main

if __name__ == "__main__":
    main('Tool Correctness')
