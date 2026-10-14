"""Builds and writes the _meta.json sidecar that accompanies each captured JPEG.

Schema follows docs/ARCHITECTURE.md section 7.5. Yaw is omitted because the
LSM6DSO32 has no magnetometer.
"""

import hashlib
import json
import os
from datetime import UTC, datetime


class MetadataBuilder:
    def build(
        self,
        filename: str,
        pass_number: int,
        image_sequence: int,
        grid_cell: tuple[int, int],
        orientation: dict,
        angular_rate: float,
        nadir_locked: bool,
        nadir_angle_deg: float,
        cam_meta: dict,
        jpeg_quality: int,
        quality_details: dict,
        quality_score: float,
        filepath: str,
        angular_velocity: list[float] | None = None,
    ) -> dict:
        """Assemble the metadata dict for one captured image.

        The caller later sets priority_tier and downlink_status, then calls save().
        """
        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        md5 = self._md5(filepath) if os.path.exists(filepath) else ""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Field names match the GCS protocol contract (protocol.py).
        imu_block = {
            "roll_deg": orientation.get("roll"),
            "pitch_deg": orientation.get("pitch"),
            "yaw_deg": None,  # no magnetometer
            "angular_velocity": angular_velocity,  # [rx, ry, rz] deg/s or None
            "accel_magnitude": orientation.get("accel_mag"),
            "angular_rate": round(angular_rate, 4),
            "stable": angular_rate < 1.0,  # mirrors is_stable() logic
            "nadir_locked": nadir_locked,
            "nadir_angle_deg": round(nadir_angle_deg, 2),
        }

        # picamera2 keys: ExposureTime (us), AnalogueGain, Lux
        cam_block = {
            "exposure_us": cam_meta.get("ExposureTime"),
            "analog_gain": cam_meta.get("AnalogueGain"),
            "lux": cam_meta.get("Lux"),
            "jpeg_quality": jpeg_quality,
            "recompressed": jpeg_quality < 70,
        }

        quality_block = {
            "blur_variance": quality_details.get("blur_variance"),
            "blur_score": quality_details.get("blur_score"),
            "exposure_mean": quality_details.get("exposure_mean"),
            "exposure_score": quality_details.get("exposure_score"),
            "motion_score": quality_details.get("motion_score"),
            "novelty_score": quality_details.get("novelty_score"),
            "combined_score": quality_score,
            "passed_gate": quality_details.get("passed_gate", False),
            "rejection_reason": quality_details.get("rejection_reason"),
        }

        return {
            "filename": filename,
            "pass_number": pass_number,
            "image_sequence": image_sequence,
            "timestamp": timestamp,
            "grid_cell": list(grid_cell),
            "imu": imu_block,
            "camera": cam_block,
            "quality": quality_block,
            "priority_tier": None,
            "file_size_bytes": file_size,
            "md5": md5,
            "downlink_status": "pending",
        }

    def save(self, meta_dict: dict, filepath: str) -> None:
        """Write the metadata dict to a JSON sidecar at filepath."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(meta_dict, f, indent=2)

    @staticmethod
    def _md5(filepath: str) -> str:
        """Compute MD5 hex digest of a file for transfer integrity checking."""
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
