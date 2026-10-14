"""Boot self-test run at startup, before the main imaging loop.

Tests the IMU, camera, storage and GCS reachability.

Failure policy:
    IMU or camera failure  return failure (caller enters safe mode)
    GCS unreachable        log "autonomous mode" and continue
    Storage above critical return the storage_only flag (caller goes downlink-only)
"""

import math
import os
import socket
import tempfile
import time

import cv2

from config import (
    BOOT_TIMEOUT,
    DATA_PORT,
    GROUND_STATION_IP,
    STORAGE_CRITICAL_PCT,
)
from utils.logger import log


def boot(imu, camera, storage, gcs_ip: str = GROUND_STATION_IP) -> dict:
    """Run the boot self-test and return a result dict.

    Keys: success (False means hardware failure, enter safe mode), reason, recovery_state
    (recovery file contents or None), gcs_reachable and storage_only.
    """
    start = time.monotonic()
    result = {
        "success": True,
        "reason": "Boot OK",
        "recovery_state": None,
        "gcs_reachable": False,
        "storage_only": False,
    }

    log("Boot: testing IMU...")
    try:
        ax, ay, az = imu.get_acceleration()
        accel_mag = math.sqrt(ax * ax + ay * ay + az * az)
        if not (8.0 < accel_mag < 35.0):
            result["success"] = False
            result["reason"] = (
                f"IMU self-test failed: |accel|={accel_mag:.2f} m/s2 "
                f"(expected 8.0-35.0, sensor unresponsive or disconnected)"
            )
            log(result["reason"], level="ERROR")
            return result
        log(f"Boot: IMU OK - accel=({ax:.2f}, {ay:.2f}, {az:.2f}) m/s2, |a|={accel_mag:.2f}")
    except Exception as exc:
        result["success"] = False
        result["reason"] = f"IMU exception during boot: {exc}"
        log(result["reason"], level="ERROR")
        return result

    if time.monotonic() - start > BOOT_TIMEOUT:
        result["success"] = False
        result["reason"] = "Boot timeout exceeded during IMU test"
        return result

    log("Boot: testing camera...")
    test_path = os.path.join(tempfile.gettempdir(), "cubesat_boot_test.jpg")
    try:
        camera.capture(test_path)
        size = os.path.getsize(test_path)
        if size < 1_000:
            result["success"] = False
            result["reason"] = f"Camera test image too small: {size} bytes (need > 1 KB)"
            log(result["reason"], level="ERROR")
            return result

        img = cv2.imread(test_path)
        if img is None:
            result["success"] = False
            result["reason"] = "Camera test image is not decodable by OpenCV"
            log(result["reason"], level="ERROR")
            return result

        log(f"Boot: camera OK — test image {size} bytes, shape {img.shape}")
    except Exception as exc:
        result["success"] = False
        result["reason"] = f"Camera exception during boot: {exc}"
        log(result["reason"], level="ERROR")
        return result
    finally:
        try:
            os.remove(test_path)
        except OSError:
            pass

    if time.monotonic() - start > BOOT_TIMEOUT:
        result["success"] = False
        result["reason"] = "Boot timeout exceeded during camera test"
        return result

    log("Boot: checking storage...")
    try:
        capacity = storage.check_capacity()
        log(f"Boot: storage — {capacity['used_pct']:.1f}% used, {capacity['free_mb']:.0f} MB free")
        if capacity["used_pct"] > STORAGE_CRITICAL_PCT:
            result["storage_only"] = True
            log(
                f"Storage critical ({capacity['used_pct']:.1f}%) — entering downlink-only mode",
                level="WARN",
            )
    except Exception as exc:
        log(f"Boot: storage check failed: {exc}", level="WARN")

    log(f"Boot: pinging GCS at {gcs_ip}...")
    result["gcs_reachable"] = _ping_gcs(gcs_ip)
    if result["gcs_reachable"]:
        log("Boot: GCS reachable")
    else:
        log("Boot: GCS unreachable — autonomous mode (non-fatal)", level="WARN")

    try:
        recovery = storage.load_recovery_state()
        if recovery:
            log(f"Boot: recovery file found — resuming from pass {recovery.get('pass_number', 0)}")
            result["recovery_state"] = recovery
        else:
            log("Boot: no recovery file — clean start")
    except Exception as exc:
        log(f"Boot: could not load recovery state: {exc}", level="WARN")

    elapsed = time.monotonic() - start
    log(f"Boot complete in {elapsed:.1f}s. Result: {result['reason']}")
    return result


def _ping_gcs(ip: str, timeout_sec: float = 3) -> bool:
    """Return True if a TCP connection to the GCS DATA_PORT succeeds."""
    try:
        with socket.create_connection((ip, DATA_PORT), timeout=timeout_sec):
            return True
    except OSError:
        return False
