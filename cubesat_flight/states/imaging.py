"""IMU-gated imaging pass.

imaging_loop() runs for up to IMAGING_WINDOW_SEC seconds, or until the operator
types "end_pass". It captures only while the IMU is stable (angular rate below
1.0 rad/s) and the nadir lock is engaged.

Nadir hysteresis lives here, not in the IMU module: the lock engages below
NADIR_TOLERANCE_DEG (45 deg) and releases above NADIR_EXIT_DEG (55 deg).

The grid cell is set by operator terminal input ("cell R C") or the GCS
"set_cell" command, and is returned so it persists across passes.
"""

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime

from config import (
    CAPTURE_INTERVAL_SEC,
    IMAGE_DIR,
    IMAGING_WINDOW_SEC,
    JPEG_MAX_SIZE_BYTES,
    MAX_IMAGES_PER_PASS,
    NADIR_EXIT_DEG,
    NADIR_TOLERANCE_DEG,
)
from utils.logger import log


def _apply_gcs_commands(command_listener, camera, grid_cell: tuple[int, int]) -> tuple[tuple[int, int], bool, bool]:
    """Handle pending GCS commands; returns (grid_cell, end_pass, enter_safe_mode)."""
    end_pass = False
    for cmd in command_listener.get_pending():
        cmd_name = cmd.get("cmd")
        if cmd_name in ("set_cell", "cell"):
            grid_cell = (cmd["row"], cmd["col"])
            log(f"Grid cell → {grid_cell} (GCS {cmd_name})")
        elif cmd_name == "end_pass":
            log("GCS end_pass command received")
            end_pass = True
        elif cmd_name == "enter_safe_mode":
            log("GCS enter_safe_mode during imaging", level="WARN")
            return grid_cell, end_pass, True
        elif cmd_name == "adjust_exposure":
            exposure_us = cmd.get("exposure_us")
            if exposure_us:
                camera.set_manual_exposure(exposure_us)
                log(f"Exposure adjusted to {exposure_us} µs by GCS command")
        # Other commands are handled by the main loop after imaging.
    return grid_cell, end_pass, False


def _apply_operator_input(op_line: str | None, grid_cell: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    """Handle one operator terminal line; returns (grid_cell, end_pass)."""
    if op_line:
        if op_line.startswith("cell"):
            parts = op_line.split()
            try:
                grid_cell = (int(parts[1]), int(parts[2]))
                log(f"Grid cell → {grid_cell} (operator input)")
            except (IndexError, ValueError):
                log("Bad cell command — format: cell R C", level="WARN")
        elif op_line == "end_pass":
            log("Operator end_pass — stopping imaging")
            return grid_cell, True
    return grid_cell, False


def _update_nadir_lock(nadir_locked: bool, nadir_angle: float) -> bool:
    """Apply nadir hysteresis: engage below NADIR_TOLERANCE_DEG, release above NADIR_EXIT_DEG."""
    if not nadir_locked:
        if nadir_angle < NADIR_TOLERANCE_DEG:
            nadir_locked = True
            log(f"Nadir locked (angle={nadir_angle:.1f}° < {NADIR_TOLERANCE_DEG}°)")
    else:
        if nadir_angle > NADIR_EXIT_DEG:
            nadir_locked = False
            log(f"Nadir lost (angle={nadir_angle:.1f}° > {NADIR_EXIT_DEG}°)")
    return nadir_locked


def _capture_and_score(
    imu,
    camera,
    quality_gate,
    coverage,
    storage,
    metadata_builder,
    pass_number: int,
    grid_cell: tuple[int, int],
    nadir_locked: bool,
    images_this_pass: list[dict],
    rejections: dict,
) -> None:
    """Capture one frame, score it, and record it as accepted or rejected."""
    seq = len(images_this_pass)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    fname = f"pass{pass_number}_img{seq:02d}_{ts}.jpg"
    fpath = os.path.join(IMAGE_DIR, fname)

    jpeg_quality = camera.capture_with_recompress(fpath, JPEG_MAX_SIZE_BYTES)

    # Read IMU and camera state immediately after capture for accurate metadata.
    ang_rate = imu.get_angular_rate()
    nadir_angle_post = imu.get_nadir_angle()
    orientation = imu.get_orientation()
    ang_vel = imu.get_angular_velocity()
    cam_meta = camera.get_metadata()
    novelty = coverage.get_novelty(grid_cell)

    score, status, details = quality_gate.score_image(fpath, ang_rate, novelty)

    meta = metadata_builder.build(
        filename=fname,
        pass_number=pass_number,
        image_sequence=seq,
        grid_cell=grid_cell,
        orientation=orientation,
        angular_rate=ang_rate,
        nadir_locked=nadir_locked,
        nadir_angle_deg=nadir_angle_post,
        cam_meta=cam_meta,
        jpeg_quality=jpeg_quality,
        quality_details=details,
        quality_score=score,
        filepath=fpath,
        angular_velocity=ang_vel,
    )
    sidecar_path = fpath.replace(".jpg", "_meta.json")
    metadata_builder.save(meta, sidecar_path)

    if score > 0:
        tier = "P1" if novelty >= 0.8 else ("P2" if novelty >= 0.3 else "P3")
        meta["priority_tier"] = tier
        meta["downlink_status"] = "pending"
        storage.update_image_index(fname, meta)
        coverage.update(grid_cell, score, fname)
        images_this_pass.append(meta)
        log(f"  PASS {fname} | score={score:.2f} tier={tier} cell={grid_cell} novelty={novelty:.1f}")
    else:
        meta["downlink_status"] = "rejected"
        storage.update_image_index(fname, meta)
        reason = details.get("rejection_reason", "unknown")
        if "blur" in reason:
            rejections["blur"] += 1
        elif "under" in reason:
            rejections["underexposed"] += 1
        elif "over" in reason:
            rejections["overexposed"] += 1
        elif "motion" in reason:
            rejections["motion_blur"] += 1
        log(f"  REJECT {fname} — {status} (reason={reason})")


def imaging_loop(
    imu,
    camera,
    quality_gate,
    coverage,
    storage,
    thermal,
    command_listener,
    metadata_builder,
    pass_number: int,
    current_grid_cell: tuple[int, int],
    get_operator_input: Callable[[], str | None],
    watchdog=None,
) -> tuple[list[dict], dict, tuple[int, int], bool, bool]:
    """Run one imaging pass.

    get_operator_input is a non-blocking stdin reader returning a line or None.
    Returns (images_this_pass, rejections, current_grid_cell, nadir_locked, enter_safe_mode):
    metadata dicts of accepted images, rejection counts by reason (blur, underexposed,
    overexposed, motion_blur), the grid cell as updated during the pass, the final nadir
    lock state, and True if the pass ended on a critical thermal reading or a GCS safe-mode command.
    """
    images_this_pass = []
    rejections = {"blur": 0, "underexposed": 0, "overexposed": 0, "motion_blur": 0}
    nadir_locked = False  # reset each pass; the operator starts by holding the CubeSat still

    img_start = time.monotonic()

    os.makedirs(IMAGE_DIR, exist_ok=True)

    while (time.monotonic() - img_start) < IMAGING_WINDOW_SEC:
        if watchdog is not None:
            watchdog.pet()

        current_grid_cell, end_pass, enter_safe_mode = _apply_gcs_commands(command_listener, camera, current_grid_cell)
        if enter_safe_mode:
            return images_this_pass, rejections, current_grid_cell, nadir_locked, True
        if end_pass:
            break

        current_grid_cell, end_pass = _apply_operator_input(get_operator_input(), current_grid_cell)
        if end_pass:
            break

        if len(images_this_pass) >= MAX_IMAGES_PER_PASS:
            log(f"Max images per pass ({MAX_IMAGES_PER_PASS}) reached — ending imaging")
            break

        if storage.is_critical():
            log("Storage critical — stopping imaging early", level="WARN")
            break

        if thermal.is_critical():
            log(f"THERMAL CRITICAL ({thermal.get_cpu_temp():.1f}°C) — entering safe mode", level="ERROR")
            return images_this_pass, rejections, current_grid_cell, nadir_locked, True

        interval = CAPTURE_INTERVAL_SEC
        if thermal.is_warning():
            interval *= 2
            log(
                f"THERMAL WARNING ({thermal.get_cpu_temp():.1f}°C) — capture interval doubled to {interval:.1f}s",
                level="WARN",
            )

        nadir_angle = imu.get_nadir_angle()
        nadir_locked = _update_nadir_lock(nadir_locked, nadir_angle)

        if not imu.is_stable():
            log(f"  Skip: not stable (rate={imu.get_angular_rate():.2f} rad/s)")
        elif not nadir_locked:
            log(f"  Skip: not nadir (angle={nadir_angle:.1f}°)")
        else:
            _capture_and_score(
                imu,
                camera,
                quality_gate,
                coverage,
                storage,
                metadata_builder,
                pass_number,
                current_grid_cell,
                nadir_locked,
                images_this_pass,
                rejections,
            )

        time.sleep(interval)

    return images_this_pass, rejections, current_grid_cell, nadir_locked, False
