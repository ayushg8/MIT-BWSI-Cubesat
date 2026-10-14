"""Safe mode: error recovery state.

Entered when the IMU or camera fails the boot self-test, CPU temperature exceeds
CPU_TEMP_CRITICAL_C (80 C), an unrecoverable runtime error occurs, or the ground
sends "enter_safe_mode".

While in safe mode the camera is stopped, there is no imaging or downlink, the
command listener and thermal monitor keep running (GCS can still send commands),
and the watchdog is petted so the process stays alive.

Exit: ground command "resume_normal" or operator terminal "resume".
"""

import time

from utils.logger import log

_POLL_INTERVAL_SEC = 2.0


def safe_mode(camera, command_listener, watchdog) -> None:
    """Stop the camera and block, petting the watchdog, until GCS sends "resume_normal"; then restart the camera."""
    log("=== ENTERING SAFE MODE ===", level="ERROR")

    # Stopping the camera reduces power and heat.
    try:
        camera.close()
        log("Safe mode: camera stopped")
    except Exception as exc:
        log(f"Safe mode: camera close failed (ignored): {exc}", level="WARN")

    print("\n!!! SAFE MODE ACTIVE !!!")
    print("Type 'resume' to exit safe mode, or send GCS command 'resume_normal'.")

    while True:
        watchdog.pet()

        for cmd in command_listener.get_pending():
            if cmd.get("cmd") == "resume_normal":
                log("Safe mode: 'resume_normal' from GCS — exiting safe mode")
                _restart_camera(camera)
                return

        # Only GCS resumes are polled here. The operator "resume" is checked by main.py
        # (via get_operator_input) before it calls safe_mode().

        time.sleep(_POLL_INTERVAL_SEC)


def _restart_camera(camera) -> None:
    """Reinitialise the camera after safe mode; on failure just log, the caller decides what next."""
    from config import IMAGE_HEIGHT, IMAGE_WIDTH
    from sensors.camera import Camera

    try:
        new_cam = Camera(IMAGE_WIDTH, IMAGE_HEIGHT)
        # Swap internals so the caller's reference stays valid.
        camera._cam = new_cam._cam
        camera._last_metadata = {}
        log("Safe mode: camera restarted successfully")
    except Exception as exc:
        log(f"Safe mode: camera restart failed: {exc}", level="ERROR")
