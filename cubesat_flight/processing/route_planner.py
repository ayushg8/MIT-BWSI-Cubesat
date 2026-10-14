"""A* route planner on the mosaic cost grid.

Plans the shortest safe path on the coarse or fine cost grid. With SEG_ENABLED it
uses the fine grid (20 px cells) so routes curve around individual hazards.
"""

import heapq
import math
import os

import cv2
import numpy as np

import config
from processing.mosaic_grid import MosaicGrid

ROUTE_VIS_DIR = os.path.join(config.PROCESSED_DIR, "route_maps")

# Cells at or above this cost are impassable.
_IMPASSABLE_COST = 500


class RoutePlanner:
    """A* pathfinder on the mosaic cost grid."""

    def plan_route(self, mosaic_grid: MosaicGrid, start_px: tuple[int, int], goal_px: tuple[int, int]) -> dict:
        """Find the cheapest route between two mosaic pixel coordinates (x, y).

        Returns a dict with path_grid ((row, col) cells), path_mosaic ((x, y) cell
        centers), distance_cm, cost, cell_px (grid cell size) and success.
        """
        if config.SEG_ENABLED:
            cost_grid = mosaic_grid.get_fine_cost_grid()
            cell_px = config.SEG_GRID_CELL_PX
            start_rc = mosaic_grid.mosaic_px_to_fine_grid(*start_px)
            goal_rc = mosaic_grid.mosaic_px_to_fine_grid(*goal_px)
        else:
            cost_grid = mosaic_grid.get_coarse_cost_grid()
            cell_px = config.MOSAIC_GRID_CELL_PX
            start_rc = mosaic_grid.mosaic_px_to_coarse_grid(*start_px)
            goal_rc = mosaic_grid.mosaic_px_to_coarse_grid(*goal_px)

        if cost_grid is None:
            return {
                "path_grid": [],
                "path_mosaic": [],
                "distance_cm": 0,
                "cost": 0,
                "cell_px": cell_px,
                "success": False,
            }

        rows, cols = cost_grid.shape

        start_rc = (max(0, min(rows - 1, start_rc[0])), max(0, min(cols - 1, start_rc[1])))
        goal_rc = (max(0, min(rows - 1, goal_rc[0])), max(0, min(cols - 1, goal_rc[1])))

        path = self._astar(cost_grid, start_rc, goal_rc)

        if path is None:
            return {
                "path_grid": [],
                "path_mosaic": [],
                "distance_cm": 0,
                "cost": 0,
                "cell_px": cell_px,
                "success": False,
            }

        path_mosaic = []
        for r, c in path:
            cx = c * cell_px + cell_px // 2
            cy = r * cell_px + cell_px // 2
            path_mosaic.append((cx, cy))

        cell_cm = config.GRID_CELL_SIZE_CM * (cell_px / config.MOSAIC_GRID_CELL_PX)
        distance_cm = self._path_length(path) * cell_cm

        total_cost = sum(float(cost_grid[r, c]) for r, c in path)

        return {
            "path_grid": path,
            "path_mosaic": path_mosaic,
            "distance_cm": round(distance_cm, 2),
            "cost": round(total_cost, 2),
            "cell_px": cell_px,
            "success": True,
        }

    def visualize_route(
        self,
        mosaic_grid: MosaicGrid,
        route_result: dict,
        mosaic_image: np.ndarray | None = None,
        output_path: str | None = None,
    ) -> np.ndarray:
        """Draw the route on mosaic_image if given, else on a cost-colored grid; optionally save to output_path."""
        if config.SEG_ENABLED:
            cost_grid = mosaic_grid.get_fine_cost_grid()
            rows, cols = mosaic_grid.fine_rows, mosaic_grid.fine_cols
        else:
            cost_grid = mosaic_grid.get_coarse_cost_grid()
            rows, cols = mosaic_grid.coarse_rows, mosaic_grid.coarse_cols

        if cost_grid is None:
            return np.zeros((100, 100, 3), dtype=np.uint8)

        cell_vis = max(4, min(64, 512 // max(rows, cols)))

        if mosaic_image is not None:
            vis = mosaic_image.copy()
            path_mosaic = route_result.get("path_mosaic", [])
            for i in range(len(path_mosaic) - 1):
                pt1 = path_mosaic[i]
                pt2 = path_mosaic[i + 1]
                cv2.line(vis, pt1, pt2, (0, 255, 0), 2)
            if path_mosaic:
                cv2.circle(vis, path_mosaic[0], 6, (0, 255, 255), -1)
                cv2.circle(vis, path_mosaic[-1], 6, (255, 0, 255), -1)
        else:
            vis_h = rows * cell_vis
            vis_w = cols * cell_vis
            vis = np.zeros((vis_h, vis_w, 3), dtype=np.uint8)

            for r in range(rows):
                for c in range(cols):
                    cost = float(cost_grid[r, c])
                    if cost >= _IMPASSABLE_COST:
                        color = (0, 0, 180)
                    elif cost > 10:
                        color = (0, 140, 255)
                    elif cost > 1:
                        color = (0, 200, 255)
                    else:
                        color = (180, 200, 180)
                    y0 = r * cell_vis
                    x0 = c * cell_vis
                    vis[y0 : y0 + cell_vis, x0 : x0 + cell_vis] = color

            path_grid = route_result.get("path_grid", [])
            for i in range(len(path_grid) - 1):
                r1, c1 = path_grid[i]
                r2, c2 = path_grid[i + 1]
                pt1 = (c1 * cell_vis + cell_vis // 2, r1 * cell_vis + cell_vis // 2)
                pt2 = (c2 * cell_vis + cell_vis // 2, r2 * cell_vis + cell_vis // 2)
                cv2.line(vis, pt1, pt2, (0, 255, 0), max(1, cell_vis // 4))
            if path_grid:
                sr, sc = path_grid[0]
                er, ec = path_grid[-1]
                cv2.circle(
                    vis,
                    (sc * cell_vis + cell_vis // 2, sr * cell_vis + cell_vis // 2),
                    cell_vis // 2,
                    (0, 255, 255),
                    -1,
                )
                cv2.circle(
                    vis,
                    (ec * cell_vis + cell_vis // 2, er * cell_vis + cell_vis // 2),
                    cell_vis // 2,
                    (255, 0, 255),
                    -1,
                )

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            cv2.imwrite(output_path, vis)

        return vis

    def _astar(
        self, cost_grid: np.ndarray, start: tuple[int, int], goal: tuple[int, int]
    ) -> list[tuple[int, int]] | None:
        """8-connected A* over per-cell traversal costs; returns start-to-goal cells or None if unreachable."""
        rows, cols = cost_grid.shape

        # Heap entries are (f_score, tie-breaker counter, (row, col)).
        counter = 0
        open_set = [(0, counter, start)]
        came_from = {}
        g_score = {start: 0.0}

        neighbors = [
            (-1, 0, 1.0),
            (1, 0, 1.0),
            (0, -1, 1.0),
            (0, 1, 1.0),
            (-1, -1, 1.414),
            (-1, 1, 1.414),
            (1, -1, 1.414),
            (1, 1, 1.414),
        ]

        while open_set:
            _, _, current = heapq.heappop(open_set)

            if current == goal:
                return self._reconstruct_path(came_from, current)

            for dr, dc, step_cost in neighbors:
                nr, nc = current[0] + dr, current[1] + dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    continue

                cell_cost = float(cost_grid[nr, nc])
                if cell_cost >= _IMPASSABLE_COST:
                    continue

                tentative_g = g_score[current] + step_cost * cell_cost

                neighbor = (nr, nc)
                if tentative_g < g_score.get(neighbor, float("inf")):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f = tentative_g + self._heuristic(neighbor, goal)
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbor))

        return None

    @staticmethod
    def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
        """Octile distance heuristic for 8-connected grid."""
        dr = abs(a[0] - b[0])
        dc = abs(a[1] - b[1])
        return max(dr, dc) + 0.414 * min(dr, dc)

    @staticmethod
    def _reconstruct_path(came_from: dict, current: tuple[int, int]) -> list[tuple[int, int]]:
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    @staticmethod
    def _path_length(path: list[tuple[int, int]]) -> float:
        """Sum of Euclidean step distances in grid-cell units."""
        total = 0.0
        for i in range(len(path) - 1):
            dr = path[i + 1][0] - path[i][0]
            dc = path[i + 1][1] - path[i][1]
            total += math.sqrt(dr * dr + dc * dc)
        return total
