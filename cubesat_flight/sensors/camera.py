"""Pi Camera Module 3 (IMX708) wrapper over picamera2, connected via CSI ribbon.

Uses picamera2 (Picamera2), not libcamera directly. The camera starts in
auto-exposure and stays there unless set_low_light_mode() switches to manual.
"""

import os
import time

import cv2
from picamera2 import Picamera2

from config import JPEG_QUALITY
from utils.logger import log


class Camera:
    def __init__(self, width: int, height: int) -> None:
        """Start the camera in auto-exposure still mode; blocks 2 s while AGC and AWB settle."""
        self._width = width
        self._height = height
        self._last_metadata = {}

        self._cam = Picamera2()
        config = self._cam.create_still_configuration(main={"size": (width, height), "format": "RGB888"})
        self._cam.configure(config)
        self._cam.start()
        time.sleep(2)

    def capture(self, filepath: str) -> str:
        """Capture a JPEG at JPEG_QUALITY to filepath and keep the camera metadata for get_metadata()."""
        request = self._cam.capture_request()
        self._last_metadata = request.get_metadata()
        frame = request.make_array("main")
        request.release()
        cv2.imwrite(filepath, frame[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return filepath

    def capture_with_recompress(self, filepath: str, max_bytes: int) -> int:
        """Capture at JPEG_QUALITY; if the file exceeds max_bytes, re-encode it in place at JPEG_QUALITY - 10.

        Returns the JPEG quality used.
        """
        request = self._cam.capture_request()
        self._last_metadata = request.get_metadata()
        frame = request.make_array("main")
        request.release()
        cv2.imwrite(filepath, frame[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

        file_size = os.path.getsize(filepath)
        if file_size <= max_bytes:
            return JPEG_QUALITY

        fallback_quality = JPEG_QUALITY - 10
        img = cv2.imread(filepath)
        cv2.imwrite(filepath, img, [cv2.IMWRITE_JPEG_QUALITY, fallback_quality])
        new_size = os.path.getsize(filepath)
        log(
            f"Recompressed {os.path.basename(filepath)} "
            f"Q{JPEG_QUALITY}→Q{fallback_quality} "
            f"({file_size // 1024}KB→{new_size // 1024}KB)",
            level="INFO",
        )
        return fallback_quality

    def set_low_light_mode(self) -> None:
        """Fixed manual exposure for the eclipse / low-light segment: 100 ms, gain 8.0."""
        self._cam.set_controls(
            {
                "AeEnable": False,
                "ExposureTime": 100_000,  # us
                "AnalogueGain": 8.0,
            }
        )

    def set_manual_exposure(self, exposure_us: int) -> None:
        """Disable auto-exposure and hold the given exposure time in microseconds."""
        self._cam.set_controls({"AeEnable": False, "ExposureTime": int(exposure_us)})

    def set_normal_mode(self) -> None:
        """Return to auto-exposure, reverting any manual exposure."""
        self._cam.set_controls(
            {
                "AeEnable": True,
            }
        )

    def get_metadata(self) -> dict:
        """Return picamera2 metadata (ExposureTime, AnalogueGain, Lux) from the last capture; empty before the first."""
        return self._last_metadata

    def close(self) -> None:
        """Stop and release the camera; call before process exit."""
        self._cam.stop()
        self._cam.close()
