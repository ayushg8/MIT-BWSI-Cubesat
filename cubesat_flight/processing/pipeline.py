"""Per-image processing pipeline running on the Pi 4 between captures.

Steps: shadow detection (brightness threshold), pixel segmentation (shadow + YOLO
detections -> label map), cost grid update on the fine grid. Route planning is
exposed separately via plan_route(). The quality gate has already run in
imaging.py. Segmentation adds about 500 ms per image and needs no GPU, well
inside the 30 s budget between captures.
"""

import os
import time

import cv2
import numpy as np

import config
from processing.mosaic_grid import MosaicGrid
from processing.pixel_segmenter import PixelSegmenter
from processing.route_planner import RoutePlanner
from utils.logger import log

# Pixels darker than this (0-255 grayscale) count as shadow.
_SHADOW_BRIGHTNESS_THRESHOLD = 60


class ProcessingPipeline:
    """Orchestrates per-image processing from capture to route planning."""

    def __init__(self) -> None:
        self._mosaic_grid = MosaicGrid()
        self._pixel_segmenter = PixelSegmenter()
        self._route_planner = RoutePlanner()

        self._mosaic_w = 0
        self._mosaic_h = 0

        self._last_route = None

    @property
    def mosaic_grid(self) -> MosaicGrid:
        return self._mosaic_grid

    @property
    def route_planner(self) -> RoutePlanner:
        return self._route_planner

    @property
    def last_route(self) -> dict | None:
        return self._last_route

    def process_image(
        self, image_path: str, mosaic_bbox: tuple[int, int, int, int], yolo_detections: list[dict] | None = None
    ) -> dict:
        """Process one captured JPEG placed at mosaic_bbox (x, y, w, h in mosaic pixels).

        yolo_detections is an optional list of dicts with "class", "bbox" [x1, y1, x2, y2]
        and "confidence"; without it only shadow detection is used. Returns shadow_pct,
        label_map (None if SEG_ENABLED is off), seg_time_ms and grid_updated.
        """
        t0 = time.monotonic()
        result = {
            "shadow_pct": 0.0,
            "label_map": None,
            "seg_time_ms": 0.0,
            "grid_updated": False,
        }

        img = cv2.imread(image_path)
        if img is None:
            log(f"Pipeline: cannot read {image_path}", level="WARN")
            return result

        mx, my, mw, mh = mosaic_bbox
        new_mosaic_w = max(self._mosaic_w, mx + mw)
        new_mosaic_h = max(self._mosaic_h, my + mh)
        if new_mosaic_w != self._mosaic_w or new_mosaic_h != self._mosaic_h:
            self._mosaic_w = new_mosaic_w
            self._mosaic_h = new_mosaic_h
            self._mosaic_grid.update_from_mosaic(self._mosaic_w, self._mosaic_h)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        shadow_mask = (gray < _SHADOW_BRIGHTNESS_THRESHOLD).astype(np.uint8) * 255
        shadow_pct = 100.0 * np.count_nonzero(shadow_mask) / shadow_mask.size
        result["shadow_pct"] = round(shadow_pct, 1)

        if config.SEG_ENABLED:
            t_seg = time.monotonic()
            label_map = self._pixel_segmenter.segment(image_path, shadow_mask, yolo_detections)
            seg_ms = (time.monotonic() - t_seg) * 1000
            result["label_map"] = label_map
            result["seg_time_ms"] = round(seg_ms, 1)

            self._mosaic_grid.apply_segmentation_mask(mosaic_bbox, label_map, config.SEG_COST_MAP, confidence=1.0)
            result["grid_updated"] = True

            log(f"Pipeline: segmented {os.path.basename(image_path)} shadow={shadow_pct:.0f}% seg={seg_ms:.0f}ms")
        else:
            hazard_class = "shadow" if shadow_pct > 30 else "safe"
            cost = 15.0 if shadow_pct > 30 else 1.0
            self._mosaic_grid.apply_classification(mosaic_bbox, hazard_class, cost)
            result["grid_updated"] = True
            log(f"Pipeline: classified {os.path.basename(image_path)} as {hazard_class} (shadow={shadow_pct:.0f}%)")

        total_ms = (time.monotonic() - t0) * 1000
        log(f"Pipeline: total processing {total_ms:.0f}ms")

        return result

    def plan_route(self, start_px: tuple[int, int], goal_px: tuple[int, int]) -> dict:
        """Plan a route between two mosaic pixel coordinates (x, y) and remember it as last_route."""
        route = self._route_planner.plan_route(self._mosaic_grid, start_px, goal_px)
        self._last_route = route

        if route["success"]:
            log(
                f"Route planned: {len(route['path_grid'])} steps, "
                f"{route['distance_cm']:.1f}cm, cost={route['cost']:.1f}"
            )
        else:
            log("Route planning failed — no path found", level="WARN")

        return route

    def get_segmentation_overlay(self) -> np.ndarray | None:
        """Return a (mosaic_h, mosaic_w, 4) BGRA overlay colored by fine-grid label, or None if no data."""
        hazard_grid = self._mosaic_grid.get_fine_hazard_grid()
        if hazard_grid is None:
            return None

        from processing.pixel_segmenter import LABEL_COLORS

        rows, cols = hazard_grid.shape
        grid_img = np.zeros((rows, cols, 4), dtype=np.uint8)

        for label_val, bgr_color in LABEL_COLORS.items():
            mask = hazard_grid == label_val
            grid_img[mask, 0] = bgr_color[0]
            grid_img[mask, 1] = bgr_color[1]
            grid_img[mask, 2] = bgr_color[2]
            grid_img[mask, 3] = 160 if label_val > 0 else 0  # transparent where unsurveyed

        overlay = cv2.resize(grid_img, (self._mosaic_w, self._mosaic_h), interpolation=cv2.INTER_NEAREST)
        return overlay
