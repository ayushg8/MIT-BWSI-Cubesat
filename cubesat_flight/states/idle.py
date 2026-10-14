"""IDLE state: post-imaging queue build, aging, cleanup and command handling.

idle() runs for IDLE_DURATION_SEC after each imaging pass. It builds the
priority queue from the image index, ages old P2 images to P3, deletes the
oldest P3 images when storage is above the warning threshold, processes pending
GCS commands, and saves the queue, image index and coverage grid to disk.
"""

import time

from config import DOWNLINK_WINDOW_SEC, IDLE_DURATION_SEC, THROTTLE_BYTES_PER_SEC
from utils.logger import log


def idle(storage, coverage, command_listener, pass_number: int, camera, watchdog=None) -> list[dict]:
    """Run the IDLE state after one imaging pass and return the priority-sorted downlink queue."""
    queue = storage.build_priority_queue(pass_number)
    log(f"IDLE: priority queue built — {len(queue)} images pending downlink")

    demoted = storage.age_images(pass_number)
    if demoted:
        log(f"IDLE: aged {len(demoted)} P2 image(s) to P3: {demoted}")

    if storage.is_warning():
        deleted = storage.cleanup_p3()
        cap = storage.check_capacity()
        log(
            f"IDLE: storage warning — deleted {deleted} P3 image(s). Now at {cap['used_pct']:.1f}%",
            level="WARN",
        )

    for cmd in command_listener.get_pending():
        _handle_idle_command(cmd, queue, storage, camera)

    storage.save_queue(queue)
    storage.save_image_index()
    coverage.save()

    data_budget = THROTTLE_BYTES_PER_SEC * DOWNLINK_WINDOW_SEC
    log(
        f"IDLE: saved. Queue={len(queue)} images, "
        f"downlink budget={data_budget} bytes "
        f"(~{data_budget // 28_000} images at 28 KB avg)"
    )

    # Sleep in short chunks so the watchdog stays petted.
    _t0 = time.monotonic()
    while (time.monotonic() - _t0) < IDLE_DURATION_SEC:
        if watchdog is not None:
            watchdog.pet()
        time.sleep(min(5.0, IDLE_DURATION_SEC - (time.monotonic() - _t0)))

    return queue


def _handle_idle_command(cmd: dict, queue: list[dict], storage, camera) -> None:
    """Handle GCS commands that are meaningful during IDLE."""
    name = cmd.get("cmd")

    if name == "retransmit":
        image_id = cmd.get("image_id", "")
        for i, item in enumerate(queue):
            if image_id in item.get("filename", ""):
                queue.insert(0, queue.pop(i))
                log(f"IDLE: retransmit — moved {image_id} to top of queue")
                return
        log(f"IDLE: retransmit — image {image_id!r} not found in queue", level="WARN")

    elif name == "priority_cell":
        row, col = cmd.get("row"), cmd.get("col")
        boosted = [i for i, m in enumerate(queue) if m.get("grid_cell") == [row, col]]
        for i in reversed(boosted):
            queue.insert(0, queue.pop(i))
        log(f"IDLE: boosted {len(boosted)} image(s) from cell ({row},{col}) to queue front")

    elif name == "adjust_exposure":
        exposure_us = cmd.get("exposure_us")
        if exposure_us:
            camera.set_manual_exposure(exposure_us)
            log(f"IDLE: exposure set to {exposure_us} µs by GCS command")

    elif name in ("enter_safe_mode", "resume_normal", "status_request", "retry_downlink", "set_cell"):
        # Handled by the main state machine loop, not here.
        log(f"IDLE: command '{name}' deferred to main loop")

    else:
        log(f"IDLE: unrecognised command '{name}'", level="WARN")
