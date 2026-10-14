"""LSM6DSO32 IMU wrapper at I2C address 0x6A.

Every call reads the sensor directly; nothing is cached. There is no
magnetometer, so yaw is unavailable and only roll and pitch (from the
accelerometer) are computed.
"""

import math

import board
import busio
from adafruit_lsm6ds import AccelRange
from adafruit_lsm6ds.lsm6dso32 import LSM6DSO32 as LSM6DS

from config import ANGULAR_RATE_THRESHOLD


class IMU:
    def __init__(self) -> None:
        i2c = busio.I2C(board.SCL, board.SDA)
        # 0x6A is the default with SDO/SA0 pulled low.
        self._imu = LSM6DS(i2c, address=0x6A)
        self._imu.accelerometer_range = AccelRange.RANGE_4G

    def get_acceleration(self) -> tuple[float, float, float]:
        """Return (x, y, z) acceleration in m/s^2."""
        return self._imu.acceleration

    def get_gyro(self) -> tuple[float, float, float]:
        """Return (x, y, z) angular velocity in rad/s."""
        return self._imu.gyro

    def get_angular_rate(self) -> float:
        """Return the gyro magnitude in rad/s."""
        gx, gy, gz = self.get_gyro()
        return math.sqrt(gx * gx + gy * gy + gz * gz)

    def is_stable(self) -> bool:
        """Return True if the angular rate is below ANGULAR_RATE_THRESHOLD."""
        return self.get_angular_rate() < ANGULAR_RATE_THRESHOLD

    def get_nadir_angle(self) -> float:
        """Return the angle in degrees between the camera boresight and gravity (0 = pointing straight down)."""
        ax, ay, az = self.get_acceleration()
        accel_mag = math.sqrt(ax * ax + ay * ay + az * az)
        if accel_mag < 1e-6:
            return 90.0

        # The camera faces +Z on this Pi 4 mount (lens on the bottom of the board),
        # so pointing at the ground aligns gravity with +Z:
        # nadir = arccos(az / |accel|)
        cos_angle = az / accel_mag
        cos_angle = max(-1.0, min(1.0, cos_angle))
        return math.degrees(math.acos(cos_angle))

    def get_angular_velocity(self) -> list[float]:
        """Return [rx, ry, rz] body-frame angular velocity in deg/s (used by the GCS mosaic stitcher)."""
        gx, gy, gz = self.get_gyro()
        return [
            round(math.degrees(gx), 4),
            round(math.degrees(gy), 4),
            round(math.degrees(gz), 4),
        ]

    def get_orientation(self) -> dict:
        """Return roll/pitch (deg, from gravity), yaw (None), accel_mag (m/s^2) and angular_rate (rad/s)."""
        ax, ay, az = self.get_acceleration()
        accel_mag = math.sqrt(ax * ax + ay * ay + az * az)

        roll = math.degrees(math.atan2(ay, az))
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))

        return {
            "roll": round(roll, 2),
            "pitch": round(pitch, 2),
            "yaw": None,
            "accel_mag": round(accel_mag, 4),
            "angular_rate": round(self.get_angular_rate(), 4),
        }
