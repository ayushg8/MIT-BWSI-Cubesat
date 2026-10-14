"""Telemetry packet builders.

build_telemetry() assembles the full packet from live hardware and software state. It is
sent as the first packet of each downlink window and on demand for a "status_request"
ground command. build_state_telemetry() is the lightweight variant for state transitions.
"""

import time
from datetime import UTC, datetime

from config import CUBESAT_ID, DOWNLINK_WINDOW_SEC, THROTTLE_BYTES_PER_SEC
from utils.logger import get_recent

_boot_time = time.monotonic()


def build_telemetry(
    imu,
    camera,
    thermal,
    storage,
    coverage,
    queue,
    pass_number: int,
    state: str,
    rejections: dict,
    captured_this_pass: int,
    captured_total: int,
    rejected_total: int,
    bytes_sent_this_pass: int,
    images_sent_total: int,
    gcs_reachable: bool,
    nadir_locked: bool,
    errors: list[str] | None = None,
) -> dict:
    """Build the full JSON-serialisable telemetry packet.

    queue is the downlink queue (metadata dicts); rejections maps reason -> count.
    """
    accel = imu.get_acceleration()
    gyro = imu.get_gyro()
    angular_rate = imu.get_angular_rate()
    nadir_angle = imu.get_nadir_angle()

    cam_meta = camera.get_metadata()

    cpu_temp = thermal.get_cpu_temp()

    capacity = storage.check_capacity()

    cov_summary = coverage.get_summary()

    data_budget = THROTTLE_BYTES_PER_SEC * DOWNLINK_WINDOW_SEC
    budget_remaining = max(0, data_budget - bytes_sent_this_pass)

    uptime_sec = int(time.monotonic() - _boot_time)

    return {
        "type": "telemetry",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cubesat_id": CUBESAT_ID,
        "pass_number": pass_number,
        "state": state,
        "uptime_sec": uptime_sec,
        "imu": {
            "accel": [round(v, 4) for v in accel],
            "gyro": [round(v, 4) for v in gyro],
            "angular_rate": round(angular_rate, 4),
            "stable": imu.is_stable(),
            "nadir_locked": nadir_locked,
            "nadir_angle_deg": round(nadir_angle, 2),
        },
        "camera": {
            "exposure_us": cam_meta.get("ExposureTime"),
            "analog_gain": cam_meta.get("AnalogueGain"),
            "lux": cam_meta.get("Lux"),
            "mode": "auto" if cam_meta.get("AeEnable", True) else "manual",
        },
        "thermal": {
            "cpu_temp_c": round(cpu_temp, 1),
            "throttled": thermal.is_warning(),
        },
        "storage": {
            "used_pct": capacity["used_pct"],
            "free_mb": capacity["free_mb"],
        },
        "imaging": {
            "captured_this_pass": captured_this_pass,
            "captured_total": captured_total,
            "rejected_total": rejected_total,
            "rejection_breakdown": rejections,
        },
        "downlink": {
            "queued": len(queue),
            "sent_total": images_sent_total,
            "bytes_this_pass": bytes_sent_this_pass,
            "budget_remaining": budget_remaining,
            "gcs_reachable": gcs_reachable,
        },
        "coverage": {
            "cells_filled": cov_summary["cells_filled"],
            "cells_total": cov_summary["cells_total"],
            "pct": cov_summary["pct"],
        },
        "errors": errors or [],
        "recent_log": get_recent(),
    }


def build_state_telemetry(state: str, imu, thermal, storage, pass_number: int) -> dict:
    """Build a lightweight packet for state transitions (no camera, coverage or queue data).

    The GCS dashboard uses it to update the state indicator in real time.
    """
    accel = imu.get_acceleration()
    gyro = imu.get_gyro()
    capacity = storage.check_capacity()
    cpu_temp = thermal.get_cpu_temp()
    uptime_sec = int(time.monotonic() - _boot_time)

    return {
        "type": "telemetry",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cubesat_id": CUBESAT_ID,
        "pass_number": pass_number,
        "state": state,
        "uptime_sec": uptime_sec,
        "imu": {
            "accel": [round(v, 4) for v in accel],
            "gyro": [round(v, 4) for v in gyro],
            "angular_rate": round(imu.get_angular_rate(), 4),
            "stable": imu.is_stable(),
            "nadir_locked": False,
            "nadir_angle_deg": round(imu.get_nadir_angle(), 2),
        },
        "thermal": {
            "cpu_temp_c": round(cpu_temp, 1),
            "throttled": thermal.is_warning(),
        },
        "storage": {
            "used_pct": capacity["used_pct"],
            "free_mb": capacity["free_mb"],
        },
        "errors": [],
        "recent_log": get_recent(),
    }
