"""Shared paths, palette and the 640 x 480 flight re-encode used by every figure."""

import json
import os
import sys
import tempfile

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLIGHT = os.path.join(ROOT, "cubesat_flight")
IMAGES = os.path.join(ROOT, "Images")
OUT = os.path.join(ROOT, "docs", "media", "figures")

# config.py reads this at import time, so set it before the first flight import.
os.environ.setdefault("CUBESAT_DATA_ROOT", tempfile.mkdtemp(prefix="muraltz-"))
if FLIGHT not in sys.path:
    sys.path.insert(0, FLIGHT)

PAPER, INK, RULE, MUTED = "#F6F4EF", "#16150F", "#D8D3C7", "#6E6A60"
ORANGE, BLUE = "#D9480F", "#2F5D8A"


def flight_jpegs(dest):
    """Re-encode Images/ the way the flight camera would: 640 x 480, INTER_AREA, JPEG q70 (q60 if over 40 kB)."""
    import config

    os.makedirs(dest, exist_ok=True)
    paths = {}
    for name in sorted(os.listdir(IMAGES)):
        if not name.lower().endswith(".jpg"):
            continue
        img = cv2.imread(os.path.join(IMAGES, name))
        small = cv2.resize(img, (config.IMAGE_WIDTH, config.IMAGE_HEIGHT), interpolation=cv2.INTER_AREA)
        out = os.path.join(dest, name)
        cv2.imwrite(out, small, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
        if os.path.getsize(out) > config.JPEG_MAX_SIZE_BYTES:
            cv2.imwrite(out, small, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY - 10])
        paths[name] = out
    return paths


def score_all(paths):
    """Run the repo's QualityGate on each file; gyro 0.0 and novelty 1.0, as in docs/data/captures.json."""
    from processing.quality import QualityGate

    gate = QualityGate()
    rows = {}
    for name, path in paths.items():
        score, status, d = gate.score_image(path, 0.0, 1.0)
        rows[name] = {"score": score, "status": status, "variance": d["blur_variance"], "bytes": os.path.getsize(path)}
    return rows


def fullres():
    """Full-resolution variances recorded in docs/data/captures.json."""
    with open(os.path.join(ROOT, "docs", "data", "captures.json")) as f:
        return {c["file"]: c["fullres"]["blur_variance"] for c in json.load(f)["captures"]}
