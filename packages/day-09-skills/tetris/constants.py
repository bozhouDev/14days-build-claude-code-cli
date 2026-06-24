"""Tetris game constants."""

from typing import Final

# Board dimensions
BOARD_WIDTH: Final[int] = 10
BOARD_HEIGHT: Final[int] = 20
VISIBLE_HEIGHT: Final[int] = 20  # fully visible rows
HIDDEN_ROWS: Final[int] = 2  # extra rows above visible area for spawning
TOTAL_HEIGHT: Final[int] = BOARD_HEIGHT + HIDDEN_ROWS  # actual grid height

# Rendering
CELL_SIZE: Final[int] = 30  # pixels per cell
PREVIEW_CELL_SIZE: Final[int] = 20
SIDEBAR_WIDTH: Final[int] = 160
WINDOW_WIDTH: Final[int] = BOARD_WIDTH * CELL_SIZE + SIDEBAR_WIDTH
WINDOW_HEIGHT: Final[int] = BOARD_HEIGHT * CELL_SIZE

# Timing (milliseconds)
BASE_DROP_INTERVAL: Final[int] = 800  # starting drop speed
MIN_DROP_INTERVAL: Final[int] = 50
DROP_INTERVAL_DECREASE: Final[int] = 50  # speedup per level
LOCK_DELAY: Final[int] = 500  # time before piece locks after landing
MAX_LOCK_RESETS: Final[int] = 15  # max lock delay resets per piece

# Scoring (original Nintendo scoring)
POINTS_SINGLE: Final[int] = 100
POINTS_DOUBLE: Final[int] = 300
POINTS_TRIPLE: Final[int] = 500
POINTS_TETRIS: Final[int] = 800
POINTS_SOFT_DROP: Final[int] = 1  # per cell
POINTS_HARD_DROP: Final[int] = 2  # per cell

# Levels
LINES_PER_LEVEL: Final[int] = 10

# Colors (RGB tuples)
BLACK: Final[tuple[int, int, int]] = (0, 0, 0)
WHITE: Final[tuple[int, int, int]] = (255, 255, 255)
GRAY: Final[tuple[int, int, int]] = (60, 60, 60)
DARK_GRAY: Final[tuple[int, int, int]] = (30, 30, 30)
GHOST_ALPHA: Final[int] = 60

# Standard Tetris piece colors
COLOR_I: Final[tuple[int, int, int]] = (0, 240, 240)  # cyan
COLOR_O: Final[tuple[int, int, int]] = (240, 240, 0)  # yellow
COLOR_T: Final[tuple[int, int, int]] = (160, 0, 240)  # purple
COLOR_S: Final[tuple[int, int, int]] = (0, 240, 0)  # green
COLOR_Z: Final[tuple[int, int, int]] = (240, 0, 0)  # red
COLOR_J: Final[tuple[int, int, int]] = (0, 0, 240)  # blue
COLOR_L: Final[tuple[int, int, int]] = (240, 160, 0)  # orange
