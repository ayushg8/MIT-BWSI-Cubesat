"""Throttled TCP downlink to the ground station.

Sends a telemetry packet first, then pops images from the priority queue,
highest priority first. Transfer is throttled to THROTTLE_BYTES_PER_SEC
(1200 B/s) to simulate the UHF flight link, so a 28 KB image takes about 23 s.
Data budget per window is THROTTLE_BYTES_PER_SEC * DOWNLINK_WINDOW_SEC = 72,000
bytes, roughly 2 images.

Error handling:
    NACK                 retry up to MAX_RETRIES_PER_IMAGE, then mark corrupt and skip
    socket error         log and stop this window; the image stays queued
    GCSUnreachableError  reported to the caller via gcs_suspended
"""

import os
import time

from comms.transfer import GCSUnreachableError
from config import (
    DOWNLINK_WINDOW_SEC,
    IMAGE_DIR,
    MAX_RETRIES_PER_IMAGE,
    THROTTLE_BYTES_PER_SEC,
)
from utils.logger import log


def downlink(transfer, storage, queue: list[dict], telemetry_dict: dict, watchdog) -> dict:
    """Run one downlink window, petting the watchdog between image sends.

    queue is the priority-sorted list of image metadata dicts. Returns images_sent,
    bytes_sent (telemetry plus images), remaining_queue and gcs_suspended.
    """
    data_budget = THROTTLE_BYTES_PER_SEC * DOWNLINK_WINDOW_SEC
    bytes_sent = 0
    images_sent = 0

    try:
        transfer.connect()
    except GCSUnreachableError as exc:
        log(str(exc), level="ERROR")
        return {
            "images_sent": 0,
            "bytes_sent": 0,
            "remaining_queue": queue,
            "gcs_suspended": True,
        }
    except OSError as exc:
        log(f"GCS connect failed: {exc}", level="WARN")
        return {
            "images_sent": 0,
            "bytes_sent": 0,
            "remaining_queue": queue,
            "gcs_suspended": False,
        }

    transfer.bytes_sent = 0

    log("Downlink: sending telemetry packet")
    telem_ok = transfer.send_telemetry(telemetry_dict)
    if telem_ok:
        bytes_sent += transfer.bytes_sent
        log(f"Downlink: telemetry sent ({transfer.bytes_sent} bytes)")
    else:
        log(f"Downlink: telemetry send failed ({transfer.last_error})", level="WARN")

    transfer.bytes_sent = 0
    window_start = time.monotonic()

    sent_indices = []  # queue indices that are done (sent or dropped)

    i = 0
    while i < len(queue):
        watchdog.pet()

        if time.monotonic() - window_start >= DOWNLINK_WINDOW_SEC:
            log(f"Downlink: window time exhausted. {len(queue) - i} images remain.")
            break

        item = queue[i]
        file_size = item.get("file_size_bytes", 0)

        if bytes_sent + file_size > data_budget:
            log(
                f"Downlink: budget exhausted ({bytes_sent}/{data_budget} bytes). {len(queue) - i} images remain queued."
            )
            break

        filepath = os.path.join(IMAGE_DIR, item["filename"])
        if not os.path.exists(filepath):
            log(f"Downlink: image file missing on disk: {item['filename']}", level="WARN")
            storage.mark_failed(item["filename"])
            sent_indices.append(i)
            i += 1
            continue

        ok = transfer.send_file(filepath, item, watchdog=watchdog)

        if ok:
            log(f"  SENT {item['filename']} ({file_size} B)")
            bytes_sent += file_size
            images_sent += 1
            storage.mark_sent(item["filename"])
            sent_indices.append(i)
            i += 1

        elif transfer.last_error == "NACK":
            item["retry_count"] = item.get("retry_count", 0) + 1
            if item["retry_count"] >= MAX_RETRIES_PER_IMAGE:
                log(
                    f"  CORRUPT {item['filename']} — {MAX_RETRIES_PER_IMAGE} NACKs, marking failed and skipping",
                    level="WARN",
                )
                storage.mark_failed(item["filename"])
                sent_indices.append(i)
                i += 1
            else:
                log(f"  NACK {item['filename']} — retry {item['retry_count']}/{MAX_RETRIES_PER_IMAGE}")

        else:
            log(f"  LINK FAIL ({transfer.last_error}) — stopping downlink", level="WARN")
            break

    sent_set = set(sent_indices)
    remaining = [item for j, item in enumerate(queue) if j not in sent_set]

    try:
        transfer.disconnect()
    except OSError:
        pass

    log(f"Downlink: {images_sent} sent, {bytes_sent} bytes, {len(remaining)} images still queued")

    return {
        "images_sent": images_sent,
        "bytes_sent": bytes_sent,
        "remaining_queue": remaining,
        "gcs_suspended": False,
    }
