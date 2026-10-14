"""Image quality gate.

QualityGate scores each captured image on four axes:
    blur      Laplacian variance (higher is sharper)
    exposure  mean pixel brightness (0-255, ideal ~128)
    motion    IMU angular rate at capture time (lower means less motion blur)
    novelty   passed in from CoverageTracker (new cell vs redundant)

Combined: Q = 0.3*blur + 0.25*exposure + 0.2*motion + 0.25*novelty.
Any hard fail gives score 0.0 and the image is rejected.
"""

import cv2
import numpy as np

from config import (
    ANGULAR_RATE_THRESHOLD,
    BLUR_THRESHOLD,
    EXPOSURE_MAX,
    EXPOSURE_MIN,
    QUALITY_WEIGHT_BLUR,
    QUALITY_WEIGHT_EXPOSURE,
    QUALITY_WEIGHT_MOTION,
    QUALITY_WEIGHT_NOVELTY,
)


class QualityGate:
    def score_image(self, filepath: str, angular_rate: float, novelty_score: float) -> tuple[float, str, dict]:
        """Score a JPEG given the IMU angular rate (rad/s) and CoverageTracker novelty.

        Returns (score, status, details). score is 0.0 if rejected; status is "passed"
        or a "rejected_*" label; details holds per-axis scores, raw values and rejection_reason.
        """
        details = {
            "blur_variance": None,
            "blur_score": 0.0,
            "exposure_mean": None,
            "exposure_score": 0.0,
            "motion_score": 0.0,
            "novelty_score": novelty_score,
            "combined_score": 0.0,
            "passed_gate": False,
            "rejection_reason": None,
        }

        img = cv2.imread(filepath)
        if img is None:
            details["rejection_reason"] = "unreadable_file"
            return 0.0, "rejected_unreadable", details

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        blur_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        details["blur_variance"] = round(blur_variance, 2)

        if blur_variance < BLUR_THRESHOLD:
            details["rejection_reason"] = "blur"
            return 0.0, "rejected_blur", details

        # Linear from 0.0 at the threshold to 1.0 at 4x the threshold.
        blur_score = min(1.0, (blur_variance - BLUR_THRESHOLD) / (BLUR_THRESHOLD * 3))
        details["blur_score"] = round(blur_score, 4)

        exposure_mean = float(np.mean(gray))
        details["exposure_mean"] = round(exposure_mean, 2)

        if exposure_mean < EXPOSURE_MIN:
            details["rejection_reason"] = "underexposed"
            return 0.0, "rejected_underexposed", details

        if exposure_mean > EXPOSURE_MAX:
            details["rejection_reason"] = "overexposed"
            return 0.0, "rejected_overexposed", details

        # Peaks at mid-grey, falls linearly to 0 at the extremes.
        exposure_score = 1.0 - abs(exposure_mean - 127.5) / 127.5
        exposure_score = max(0.0, min(1.0, exposure_score))
        details["exposure_score"] = round(exposure_score, 4)

        if angular_rate >= ANGULAR_RATE_THRESHOLD:
            details["rejection_reason"] = "motion_blur"
            return 0.0, "rejected_motion_blur", details

        # 1.0 at rest, falls linearly to 0 at the threshold.
        motion_score = 1.0 - (angular_rate / ANGULAR_RATE_THRESHOLD)
        motion_score = max(0.0, min(1.0, motion_score))
        details["motion_score"] = round(motion_score, 4)

        # Novelty never hard-fails; low novelty only lowers the score.
        combined = (
            QUALITY_WEIGHT_BLUR * blur_score
            + QUALITY_WEIGHT_EXPOSURE * exposure_score
            + QUALITY_WEIGHT_MOTION * motion_score
            + QUALITY_WEIGHT_NOVELTY * novelty_score
        )
        combined = round(max(0.0, min(1.0, combined)), 4)
        details["combined_score"] = combined
        details["passed_gate"] = True

        return combined, "passed", details
