"""Between the game's frame and Blender's.

The game is right-handed with Y up - the frame render3d's camera was checked in against
the real game - and Blender is right-handed with Z up, so ``(x, y, z)`` maps to
``(x, -z, y)``: a proper rotation, so face winding survives it.

Turns convert exactly in both directions, so a tile or instance nobody touched writes
back the integer it was read as.
"""
from __future__ import annotations

import math

TURN = 0x1000       # binary angle units in a full turn
QUARTER = 0x400


def to_blender(x: float, y: float, z: float) -> tuple[float, float, float]:
    return x, -z, y


def to_game(x: float, y: float, z: float) -> tuple[float, float, float]:
    return x, z, -y


def angle(units: int) -> float:
    return units / TURN * math.tau


def units(radians: float) -> int:
    return round(radians / math.tau * TURN) % TURN


def tile_euler(rotation: int) -> tuple[float, float, float]:
    """A tile turns its mesh by ``-rotation`` about game Y (see ``Level.place``)."""
    return 0.0, 0.0, -angle(rotation)


def tile_rotation(euler) -> int:
    """Back to a tile rotation, snapped to the quarter turns every shipped tile uses."""
    quarters = round(-euler[2] / (math.tau / 4))
    return quarters % 4 * QUARTER


# Actors rotate about game X, then Y, then Z (``render_level._rotate_actor``). Game Y is
# Blender Z and game Z is Blender -Y, so that is Blender's XZY order.
ACTOR_ROTATION_MODE = "XZY"


def actor_euler(rotation: tuple[int, int, int]) -> tuple[float, float, float]:
    ax, ay, az = rotation
    return angle(ax), -angle(az), angle(ay)


def actor_rotation(euler) -> tuple[int, int, int]:
    return units(euler[0]), units(euler[2]), units(-euler[1])
