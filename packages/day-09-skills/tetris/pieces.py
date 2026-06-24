"""Tetromino definitions with SRS (Super Rotation System)."""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import ClassVar

from tetris.constants import (
    COLOR_I,
    COLOR_J,
    COLOR_L,
    COLOR_O,
    COLOR_S,
    COLOR_T,
    COLOR_Z,
)

# ── Shape matrices (each piece has 4 rotation states) ──────────────────────

# Each rotation state is a list of (row_offset, col_offset) relative to
# the piece's origin. Offsets are (row, col) where row 0 is top.

# I piece
_SHAPES_I: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (0, 1), (0, 2), (0, 3)),
    ((0, 2), (1, 2), (2, 2), (3, 2)),
    ((2, 0), (2, 1), (2, 2), (2, 3)),
    ((0, 1), (1, 1), (2, 1), (3, 1)),
)

# O piece (all rotations identical)
_SHAPES_O: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (0, 1), (1, 0), (1, 1)),
    ((0, 0), (0, 1), (1, 0), (1, 1)),
    ((0, 0), (0, 1), (1, 0), (1, 1)),
    ((0, 0), (0, 1), (1, 0), (1, 1)),
)

# T piece
_SHAPES_T: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (0, 1), (0, 2), (1, 1)),
    ((0, 1), (1, 0), (1, 1), (2, 1)),
    ((1, 0), (1, 1), (1, 2), (0, 1)),
    ((0, 0), (1, 0), (1, 1), (2, 0)),
)

# S piece
_SHAPES_S: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 1), (0, 2), (1, 0), (1, 1)),
    ((0, 0), (1, 0), (1, 1), (2, 1)),
    ((1, 1), (1, 2), (0, 0), (0, 1)),
    ((0, 0), (1, 0), (1, 1), (2, 1)),
)

# Z piece
_SHAPES_Z: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (0, 1), (1, 1), (1, 2)),
    ((0, 1), (1, 0), (1, 1), (2, 0)),
    ((1, 0), (1, 1), (0, 1), (0, 2)),
    ((0, 1), (1, 0), (1, 1), (2, 0)),
)

# J piece
_SHAPES_J: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (1, 0), (1, 1), (1, 2)),
    ((0, 0), (0, 1), (1, 0), (2, 0)),
    ((0, 0), (0, 1), (0, 2), (1, 2)),
    ((0, 0), (1, 0), (2, 0), (2, -1)),
)

# L piece
_SHAPES_L: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 2), (1, 0), (1, 1), (1, 2)),
    ((0, 0), (1, 0), (2, 0), (2, 1)),
    ((0, 0), (0, 1), (0, 2), (1, 0)),
    ((0, 0), (0, 1), (1, 1), (2, 1)),
)

# ── SRS Wall Kick data ────────────────────────────────────────────────────

# Standard wall kick offsets for J, L, S, T, Z pieces
# Format: kick_data[from_state][to_state] = list of (col_offset, row_offset) tries
# Note: row_offset positive = down (because row index increases downward)

_WALL_KICKS_JLSTZ: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {
    (0, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (1, 0): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (1, 2): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (2, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (2, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
    (3, 2): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (3, 0): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (0, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
}

# Wall kick offsets for I piece
_WALL_KICKS_I: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {
    (0, 1): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (1, 0): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (1, 2): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
    (2, 1): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (2, 3): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (3, 2): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (3, 0): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (0, 3): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
}


@dataclass(frozen=True)
class PieceDef:
    """Definition of a tetromino type (immutable)."""

    name: str
    color: tuple[int, int, int]
    shapes: tuple[tuple[tuple[int, int], ...], ...]
    wall_kicks: dict[tuple[int, int], tuple[tuple[int, int], ...]]


# The 7 standard pieces
PIECE_I: ClassVar[PieceDef] = PieceDef("I", COLOR_I, _SHAPES_I, _WALL_KICKS_I)
PIECE_O: ClassVar[PieceDef] = PieceDef("O", COLOR_O, _SHAPES_O, {})
PIECE_T: ClassVar[PieceDef] = PieceDef("T", COLOR_T, _SHAPES_T, _WALL_KICKS_JLSTZ)
PIECE_S: ClassVar[PieceDef] = PieceDef("S", COLOR_S, _SHAPES_S, _WALL_KICKS_JLSTZ)
PIECE_Z: ClassVar[PieceDef] = PieceDef("Z", COLOR_Z, _SHAPES_Z, _WALL_KICKS_JLSTZ)
PIECE_J: ClassVar[PieceDef] = PieceDef("J", COLOR_J, _SHAPES_J, _WALL_KICKS_JLSTZ)
PIECE_L: ClassVar[PieceDef] = PieceDef("L", COLOR_L, _SHAPES_L, _WALL_KICKS_JLSTZ)

ALL_PIECES: tuple[PieceDef, ...] = (
    PIECE_I, PIECE_O, PIECE_T, PIECE_S, PIECE_Z, PIECE_J, PIECE_L,
)

# Fix: Python dataclass ClassVar doesn't work well for module-level constants,
# so we rebind without the annotation.
del PIECE_I, PIECE_O, PIECE_T, PIECE_S, PIECE_Z, PIECE_J, PIECE_L

PIECE_I = PieceDef("I", COLOR_I, _SHAPES_I, _WALL_KICKS_I)
PIECE_O = PieceDef("O", COLOR_O, _SHAPES_O, {})
PIECE_T = PieceDef("T", COLOR_T, _SHAPES_T, _WALL_KICKS_JLSTZ)
PIECE_S = PieceDef("S", COLOR_S, _SHAPES_S, _WALL_KICKS_JLSTZ)
PIECE_Z = PieceDef("Z", COLOR_Z, _SHAPES_Z, _WALL_KICKS_JLSTZ)
PIECE_J = PieceDef("J", COLOR_J, _SHAPES_J, _WALL_KICKS_JLSTZ)
PIECE_L = PieceDef("L", COLOR_L, _SHAPES_L, _WALL_KICKS_JLSTZ)

ALL_PIECES = (PIECE_I, PIECE_O, PIECE_T, PIECE_S, PIECE_Z, PIECE_J, PIECE_L)


class Tetromino:
    """A tetromino instance with position and rotation state."""

    def __init__(self, piece_def: PieceDef, row: int = 0, col: int = 0) -> None:
        self.piece_def: PieceDef = piece_def
        self.row: int = row  # top-left of bounding box
        self.col: int = col
        self.rotation: int = 0  # 0-3

    @property
    def name(self) -> str:
        return self.piece_def.name

    @property
    def color(self) -> tuple[int, int, int]:
        return self.piece_def.color

    def cells(self) -> list[tuple[int, int]]:
        """Return absolute (row, col) positions of all cells."""
        offsets = self.piece_def.shapes[self.rotation]
        return [(self.row + r, self.col + c) for r, c in offsets]

    def rotated_cells(self, direction: int = 1) -> list[tuple[int, int]]:
        """Return cells for a rotation (1=clockwise, -1=counter-clockwise)."""
        new_rot = (self.rotation + direction) % 4
        offsets = self.piece_def.shapes[new_rot]
        return [(self.row + r, self.col + c) for r, c in offsets]

    def clone(self) -> Tetromino:
        """Return a deep copy."""
        return copy.deepcopy(self)

    def __repr__(self) -> str:
        return f"Tetromino({self.name}, row={self.row}, col={self.col}, rot={self.rotation})"


class PieceBag:
    """7-bag randomizer: each bag contains one of each piece, shuffled."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng: random.Random = random.Random(seed)
        self._bag: list[PieceDef] = []
        self._refill()

    def _refill(self) -> None:
        self._bag = list(ALL_PIECES)
        self._rng.shuffle(self._bag)

    def next(self) -> PieceDef:
        if not self._bag:
            self._refill()
        return self._bag.pop()
