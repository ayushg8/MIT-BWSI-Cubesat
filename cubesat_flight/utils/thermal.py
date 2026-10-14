"""CPU temperature monitoring via sysfs.

A background daemon thread reads /sys/class/thermal/thermal_zone0/temp every
THERMAL_CHECK_INTERVAL_SEC. The file holds millidegrees Celsius as an int, hence the /1000.

Warning (CPU_TEMP_WARNING_C, 70 C): the imaging loop doubles its interval.
Critical (CPU_TEMP_CRITICAL_C, 80 C): enter safe mode.
"""

import threading
import time

from config import CPU_TEMP_CRITICAL_C, CPU_TEMP_WARNING_C, THERMAL_CHECK_INTERVAL_SEC

_THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"


class Thermal:
    """Cached CPU temperature with warning and critical thresholds."""

    def __init__(self) -> None:
        self._temp_c = 0.0
        self._lock = threading.Lock()
        self._thread = None

    def start_monitoring(self) -> None:
        """Start the background daemon thread; call once at boot."""
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="ThermalMonitor")
        self._thread.start()

    def get_cpu_temp(self) -> float:
        """Return the most recently read CPU temperature in °C."""
        with self._lock:
            return self._temp_c

    def is_warning(self) -> bool:
        """True if CPU temp exceeds CPU_TEMP_WARNING_C."""
        return self.get_cpu_temp() > CPU_TEMP_WARNING_C

    def is_critical(self) -> bool:
        """True if CPU temp exceeds CPU_TEMP_CRITICAL_C."""
        return self.get_cpu_temp() > CPU_TEMP_CRITICAL_C

    def _monitor_loop(self) -> None:
        while True:
            try:
                with open(_THERMAL_PATH) as f:
                    raw = int(f.read().strip())
                temp = raw / 1000.0
                with self._lock:
                    self._temp_c = temp
            except OSError:
                pass  # sysfs temporarily unavailable — keep last reading
            time.sleep(THERMAL_CHECK_INTERVAL_SEC)
