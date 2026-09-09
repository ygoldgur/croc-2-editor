"""Canonical locations, so nothing depends on the CD image being mounted.

PRISTINE  untouched copy of the disc; the reference for original bytes
GAME      the copy the game actually reads, exposed to Wine as drive D:
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRISTINE = ROOT / "game" / "pristine"
GAME = ROOT / "game" / "cd"

PRISTINE_WADS = PRISTINE / "Wads"
GAME_WADS = GAME / "Wads"
