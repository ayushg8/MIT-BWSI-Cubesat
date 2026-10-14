"""8x8 coverage grid tracker.

Grid cell assignment comes exclusively from operator terminal input
("cell R C"), not from elapsed time or any trajectory map.

Novelty scoring drives the priority queue:
    1.0  cell has never been captured
    0.5  cell has a capture, but quality could still be improved
    0.1  cell has a good-quality capture; further shots are redundant

A best quality score of 0.7 or above counts as well-covered.
"""

import json
import os
from datetime import UTC, datetime

from config import DATA_ROOT, GRID_COLS, GRID_ROWS

COVERAGE_FILE = os.path.join(DATA_ROOT, "coverage.json")

# Quality score above which a cell is considered well-covered (redundant).
_GOOD_QUALITY_THRESHOLD = 0.7


class CoverageTracker:
    def __init__(self) -> None:
        # Keyed by "R,C" string for JSON compatibility. Each value holds covered,
        # best_quality_score, best_image_filename, pass_number, timestamp.
        self._grid: dict[str, dict] = {}

    def get_novelty(self, cell: tuple[int, int]) -> float:
        """Return 1.0 if never captured, 0.5 if quality can improve, 0.1 if redundant."""
        entry = self._grid.get(self._key(cell))
        if entry is None or not entry["covered"]:
            return 1.0
        if entry["best_quality_score"] < _GOOD_QUALITY_THRESHOLD:
            return 0.5
        return 0.1

    def update(self, cell: tuple[int, int], score: float, filename: str) -> None:
        """Record a capture; keeps it only if it beats the cell's best quality score."""
        key = self._key(cell)
        existing = self._grid.get(key)

        if existing is None or score > existing["best_quality_score"]:
            self._grid[key] = {
                "covered": True,
                "best_quality_score": round(score, 4),
                "best_image_filename": filename,
                "pass_number": None,
                "timestamp": datetime.now(UTC).isoformat(),
                "row": cell[0],
                "col": cell[1],
            }
        elif not existing["covered"]:
            # Entry loaded from disk but marked not covered.
            existing["covered"] = True
            existing["best_quality_score"] = round(score, 4)
            existing["best_image_filename"] = filename
            existing["timestamp"] = datetime.now(UTC).isoformat()

    def get_coverage_pct(self) -> float:
        """Return percentage of grid cells that have at least one capture."""
        total = GRID_ROWS * GRID_COLS
        covered = sum(1 for e in self._grid.values() if e.get("covered", False))
        return round(100.0 * covered / total, 1)

    def get_summary(self) -> dict:
        """Return cells_filled, cells_total, pct and the full grid for telemetry."""
        total = GRID_ROWS * GRID_COLS
        covered = sum(1 for e in self._grid.values() if e.get("covered", False))
        return {
            "cells_filled": covered,
            "cells_total": total,
            "pct": round(100.0 * covered / total, 1),
            "grid": dict(self._grid),
        }

    def save(self) -> None:
        """Persist current grid state to COVERAGE_FILE (JSON)."""
        os.makedirs(os.path.dirname(COVERAGE_FILE), exist_ok=True)
        with open(COVERAGE_FILE, "w") as f:
            json.dump(self._grid, f, indent=2)

    def load(self) -> None:
        """Load grid state from COVERAGE_FILE; no-op if the file does not exist yet."""
        if not os.path.exists(COVERAGE_FILE):
            return
        with open(COVERAGE_FILE) as f:
            self._grid = json.load(f)

    @staticmethod
    def _key(cell: tuple[int, int]) -> str:
        """Convert (row, col) tuple to the string key used in the grid dict."""
        return f"{cell[0]},{cell[1]}"
