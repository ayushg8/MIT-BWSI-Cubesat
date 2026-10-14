"""Pixel-level terrain segmentation.

Combines shadow masks and YOLO bounding-box detections into a per-pixel label
map. No extra ML model: adaptive thresholding and morphology extract hazard
shapes inside each YOLO bbox.

Labels:
    0 UNSURVEYED     not yet imaged
    1 SAND           safe traversal
    2 PLAIN_SURFACE  safe, featureless
    3 SHADOW         uncertain, high traversal cost
    4 CRATER         hazardous
    5 BOULDER        impassable
"""

import os

import cv2
import numpy as np

from config import (
    PROCESSED_DIR,
    SEG_FALLBACK_ELLIPSE_PCT,
    SEG_MIN_CONTOUR_AREA_PCT,
    SEG_SAFETY_DILATION_PX,
)

UNSURVEYED = 0
SAND = 1
PLAIN_SURFACE = 2
SHADOW = 3
CRATER = 4
BOULDER = 5

# Color map for visualization (BGR)
LABEL_COLORS = {
    UNSURVEYED: (80, 80, 80),  # dark gray
    SAND: (200, 220, 240),  # light sandy
    PLAIN_SURFACE: (180, 200, 180),  # pale green
    SHADOW: (100, 50, 50),  # dark blue-ish
    CRATER: (0, 100, 255),  # orange
    BOULDER: (0, 0, 200),  # red
}

SEG_VIS_DIR = os.path.join(PROCESSED_DIR, "segmentation_maps")


class PixelSegmenter:
    """Produces a per-pixel label map from shadow masks and YOLO detections."""

    def segment(
        self, image_path: str, shadow_mask: np.ndarray | None = None, yolo_detections: list[dict] | None = None
    ) -> np.ndarray:
        """Return an (H, W) uint8 label map (values 0-5) for the JPEG at image_path.

        shadow_mask is an optional (H, W) array where >0 means shadow. yolo_detections
        is an optional list of dicts with "class" (crater, boulder or plain),
        "bbox" [x1, y1, x2, y2] and "confidence". Raises FileNotFoundError if the
        image is unreadable.
        """
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot read image: {image_path}")

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        label_map = np.full((h, w), SAND, dtype=np.uint8)

        if shadow_mask is not None:
            if shadow_mask.shape[:2] == (h, w):
                label_map[shadow_mask > 0] = SHADOW

        if yolo_detections:
            for det in yolo_detections:
                cls = det.get("class", "").lower()
                bbox = det.get("bbox", [])
                if len(bbox) != 4:
                    continue

                x1, y1, x2, y2 = [int(v) for v in bbox]
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(w, x2)
                y2 = min(h, y2)

                if x2 <= x1 or y2 <= y1:
                    continue

                if cls == "plain":
                    roi = label_map[y1:y2, x1:x2]
                    safe_mask = roi == SAND
                    roi[safe_mask] = PLAIN_SURFACE
                elif cls in ("crater", "boulder"):
                    label = CRATER if cls == "crater" else BOULDER
                    self._segment_object_in_bbox(gray, label_map, x1, y1, x2, y2, label)

        if SEG_SAFETY_DILATION_PX > 0:
            label_map = self._dilate_hazards(label_map)

        self._save_visualization(label_map, image_path)

        return label_map

    def _segment_object_in_bbox(
        self, gray: np.ndarray, label_map: np.ndarray, x1: int, y1: int, x2: int, y2: int, label: int
    ) -> None:
        """Label the object contour inside a YOLO bbox; falls back to an ellipse if no contour is large enough."""
        roi_gray = gray[y1:y2, x1:x2]
        bh, bw = roi_gray.shape

        # Rocks and craters are darker than the sandy background.
        block_size = max(3, (min(bh, bw) // 4) | 1)  # ensure odd
        thresh = cv2.adaptiveThreshold(
            roi_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, 10
        )

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        bbox_area = bw * bh
        min_area = bbox_area * (SEG_MIN_CONTOUR_AREA_PCT / 100.0)

        good_contours = [c for c in contours if cv2.contourArea(c) >= min_area]

        if good_contours:
            mask = np.zeros((bh, bw), dtype=np.uint8)
            cv2.drawContours(mask, good_contours, -1, 255, cv2.FILLED)
            label_map[y1:y2, x1:x2][mask > 0] = label
        else:
            self._fill_fallback_ellipse(label_map, x1, y1, x2, y2, label)

    def _fill_fallback_ellipse(self, label_map: np.ndarray, x1: int, y1: int, x2: int, y2: int, label: int) -> None:
        """Fill an ellipse covering SEG_FALLBACK_ELLIPSE_PCT of the bbox as a conservative fallback."""
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        scale = (SEG_FALLBACK_ELLIPSE_PCT / 100.0) ** 0.5
        ax = int((x2 - x1) * scale / 2)
        ay = int((y2 - y1) * scale / 2)
        if ax < 1 or ay < 1:
            return
        cv2.ellipse(label_map, (cx, cy), (ax, ay), 0, 0, 360, int(label), cv2.FILLED)

    def _dilate_hazards(self, label_map: np.ndarray) -> np.ndarray:
        """Dilate SHADOW, CRATER and BOULDER pixels by the safety margin; worse labels overwrite."""
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * SEG_SAFETY_DILATION_PX + 1, 2 * SEG_SAFETY_DILATION_PX + 1)
        )

        result = label_map.copy()

        for hazard_label in (SHADOW, CRATER, BOULDER):
            mask = (label_map == hazard_label).astype(np.uint8)
            dilated = cv2.dilate(mask, kernel, iterations=1)
            result[dilated > 0] = np.maximum(result[dilated > 0], hazard_label)

        return result

    def _save_visualization(self, label_map: np.ndarray, image_path: str) -> str:
        """Save a color-coded segmentation map as a PNG and return its path."""
        os.makedirs(SEG_VIS_DIR, exist_ok=True)

        h, w = label_map.shape
        vis = np.zeros((h, w, 3), dtype=np.uint8)
        for label_val, color in LABEL_COLORS.items():
            vis[label_map == label_val] = color

        base = os.path.splitext(os.path.basename(image_path))[0]
        out_path = os.path.join(SEG_VIS_DIR, f"{base}_seg.png")
        cv2.imwrite(out_path, vis)
        return out_path
