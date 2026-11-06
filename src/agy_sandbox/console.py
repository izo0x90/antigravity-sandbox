"""
Every line the tool shows the user goes through here, so engine code never
prints and tests can capture messages by patching these functions.
"""
import sys
from typing import Sequence


def step(message: str) -> None:
    """Progress: something is about to happen or just happened."""
    print(message, flush=True)


def info(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"⚠️  {message}", file=sys.stderr, flush=True)


def hint(message: str) -> None:
    print(f"💡 {message}", flush=True)


def command(argv: Sequence[str]) -> None:
    print(f"Executing: {' '.join(argv)}", flush=True)


def banner(lines: Sequence[str], width: int = 78) -> None:
    print("\n" + "!" * width, flush=True)
    for line in lines:
        print(line, flush=True)
    print("!" * width, flush=True)
