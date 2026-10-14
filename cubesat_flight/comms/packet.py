"""Transfer header builder.

Every TCP transfer is a newline-terminated JSON header, then the raw payload bytes,
then a single ACK/NACK byte from the GCS.
"""

import hashlib
import json
import os


def build_image_header(filepath: str, meta_dict: dict) -> dict:
    """Build the transfer header for the JPEG at filepath, embedding its metadata."""
    return {
        "type": "image",
        "filename": os.path.basename(filepath),
        "file_size": os.path.getsize(filepath),
        "md5": md5_file(filepath),
        "metadata": meta_dict,
    }


def build_telemetry_header(telem_dict: dict) -> tuple[dict, bytes]:
    """Return (header, payload) for a telemetry packet; payload is the dict as UTF-8 JSON."""
    payload = json.dumps(telem_dict, default=str).encode("utf-8")
    header = {
        "type": "telemetry",
        "filename": "telemetry.json",
        "file_size": len(payload),
        "md5": hashlib.md5(payload).hexdigest(),
        "metadata": {},
    }
    return header, payload


def md5_file(filepath: str) -> str:
    """Return the MD5 hex digest of a file, read in 64 KB chunks."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
