"""Queue persistence, image index, capacity checks and aging.

Two JSON files are kept:
  QUEUE_FILE        ordered list of images pending downlink
  IMAGE_INDEX_FILE  record of every image ever captured

Priority tiers are P1 (highest) > P2 > P3. build_priority_queue() sorts by tier then
score descending; age_images() demotes old P2 images to P3; cleanup_p3() deletes the
oldest P3 images from disk until storage is below the warning level.
"""

import json
import os
import shutil

from config import (
    IMAGE_DIR,
    IMAGE_INDEX_FILE,
    P2_AGING_PASSES,
    QUEUE_FILE,
    RECOVERY_FILE,
    STORAGE_CRITICAL_PCT,
    STORAGE_WARNING_PCT,
)

_TIER_RANK = {"P1": 0, "P2": 1, "P3": 2}


class StorageManager:
    """Owns the on-disk image store, downlink queue and image index."""

    def __init__(self) -> None:
        self._image_index = {}  # filename → metadata dict
        os.makedirs(IMAGE_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)

    def check_capacity(self) -> dict:
        """Return used_pct, free_mb and total_mb for the IMAGE_DIR partition."""
        usage = shutil.disk_usage(IMAGE_DIR)
        used_pct = 100.0 * usage.used / usage.total
        return {
            "used_pct": round(used_pct, 1),
            "free_mb": round(usage.free / (1024 * 1024), 1),
            "total_mb": round(usage.total / (1024 * 1024), 1),
        }

    def is_warning(self) -> bool:
        return self.check_capacity()["used_pct"] > STORAGE_WARNING_PCT

    def is_critical(self) -> bool:
        return self.check_capacity()["used_pct"] > STORAGE_CRITICAL_PCT

    def save_queue(self, queue: list[dict]) -> None:
        """Persist the downlink queue to QUEUE_FILE."""
        with open(QUEUE_FILE, "w") as f:
            json.dump(queue, f, indent=2, default=str)

    def load_queue(self) -> list[dict]:
        """Load the downlink queue; empty if QUEUE_FILE does not exist."""
        if not os.path.exists(QUEUE_FILE):
            return []
        with open(QUEUE_FILE) as f:
            return json.load(f)

    def save_image_index(self) -> None:
        """Write the in-memory image index to IMAGE_INDEX_FILE."""
        with open(IMAGE_INDEX_FILE, "w") as f:
            json.dump(self._image_index, f, indent=2, default=str)

    def load_image_index(self) -> None:
        """Load IMAGE_INDEX_FILE into memory; empty index if it does not exist yet."""
        if not os.path.exists(IMAGE_INDEX_FILE):
            self._image_index = {}
            return
        with open(IMAGE_INDEX_FILE) as f:
            self._image_index = json.load(f)

    def update_image_index(self, filename: str, meta: dict) -> None:
        """Add or overwrite the entry for a bare filename, e.g. "pass3_img07_20260315_144500.jpg"."""
        self._image_index[filename] = meta

    def mark_sent(self, filename: str) -> None:
        """Set downlink_status to 'sent'."""
        if filename in self._image_index:
            self._image_index[filename]["downlink_status"] = "sent"

    def mark_failed(self, filename: str) -> None:
        """Set downlink_status to 'failed' and increment retry_count."""
        if filename in self._image_index:
            entry = self._image_index[filename]
            entry["downlink_status"] = "failed"
            entry["retry_count"] = entry.get("retry_count", 0) + 1

    def build_priority_queue(self, current_pass: int) -> list[dict]:
        """Return pending images ordered by tier (P1 first) then combined_score descending.

        current_pass is unused here; it is kept so callers pass the same arguments as age_images().
        """
        pending = [
            meta
            for meta in self._image_index.values()
            if meta.get("downlink_status") == "pending" and meta.get("priority_tier") is not None
        ]

        def sort_key(m):
            tier = _TIER_RANK.get(m.get("priority_tier", "P3"), 2)
            score = m.get("quality", {}).get("combined_score", 0.0)
            return (tier, -score)

        pending.sort(key=sort_key)
        return pending

    def age_images(self, current_pass: int) -> list[str]:
        """Demote P2 images older than P2_AGING_PASSES passes to P3; return the demoted filenames.

        With P2_AGING_PASSES=2, pass 1's P2 images are demoted during pass 4's IDLE phase.
        """
        demoted = []
        for filename, meta in self._image_index.items():
            if meta.get("priority_tier") != "P2":
                continue
            image_pass = meta.get("pass_number", current_pass)
            if (current_pass - image_pass) > P2_AGING_PASSES:
                meta["priority_tier"] = "P3"
                demoted.append(filename)
        return demoted

    def cleanup_p3(self) -> int:
        """Delete the oldest pending P3 images (and sidecars) until storage drops below warning.

        Oldest is by timestamp, falling back to filename. Deleted entries are marked 'deleted'
        in the index. Returns the number deleted.
        """
        p3_images = [
            (fname, meta)
            for fname, meta in self._image_index.items()
            if meta.get("priority_tier") == "P3" and meta.get("downlink_status") == "pending"
        ]

        # ISO timestamps sort lexicographically.
        p3_images.sort(key=lambda x: x[1].get("timestamp", x[0]))

        deleted = 0
        for filename, meta in p3_images:
            if not self.is_warning():
                break
            filepath = os.path.join(IMAGE_DIR, filename)
            sidecar = filepath.replace(".jpg", "_meta.json")
            for path in (filepath, sidecar):
                if os.path.exists(path):
                    os.remove(path)
            meta["downlink_status"] = "deleted"
            deleted += 1

        return deleted

    def save_recovery_state(self, state_dict: dict) -> None:
        """Persist watchdog recovery state (at least {"pass_number": N}) to RECOVERY_FILE."""
        with open(RECOVERY_FILE, "w") as f:
            json.dump(state_dict, f, indent=2, default=str)

    def load_recovery_state(self) -> dict | None:
        """Load RECOVERY_FILE; None on a clean boot."""
        if not os.path.exists(RECOVERY_FILE):
            return None
        with open(RECOVERY_FILE) as f:
            return json.load(f)
