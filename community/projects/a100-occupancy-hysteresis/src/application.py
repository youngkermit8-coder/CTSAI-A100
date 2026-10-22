"""Application boundary: local receipt clock, source ordering and explicit epochs.

Point rows use the existing reader's [range_m, angle_radians, speed_mps].
This module schedules no background work; callers must poll on a monotonic clock.
"""

from dataclasses import dataclass

import numpy as np

from study import Occupancy


@dataclass(frozen=True)
class PolarROI:
    near: float = 2.5
    far: float = 3.1
    left_degrees: float = -20.0
    right_degrees: float = 5.0

    def __post_init__(self):
        values = (self.near, self.far, self.left_degrees, self.right_degrees)
        if not np.isfinite(values).all() or not (
            0 <= self.near < self.far
            and -180 <= self.left_degrees < self.right_degrees <= 180
        ):
            raise ValueError("Finite ordered range/angle bounds required")

    def count(self, points):
        points = np.asarray(points, dtype=float)
        if (
            points.ndim != 2
            or points.shape[1] != 3
            or not np.isfinite(points).all()
            or np.any(points[:, 0] < 0)
            or np.any(np.abs(points[:, 1]) > np.pi)
        ):
            raise ValueError("Expected finite Nx3 range/angle-radians/speed rows")
        return int(
            np.count_nonzero(
                (points[:, 0] >= self.near)
                & (points[:, 0] <= self.far)
                & (points[:, 1] >= np.deg2rad(self.left_degrees))
                & (points[:, 1] <= np.deg2rad(self.right_degrees))
            )
        )


class OccupancyApplication:
    """Reject old packets without renewing their lease or inferring a restart.

    Call start_epoch only after an externally confirmed source restart. A lower
    device timestamp alone cannot distinguish restart from delayed delivery.
    """

    def __init__(self, roi=None, **parameters):
        self.roi = roi if roi is not None else PolarROI()
        self.machine = Occupancy(**parameters)
        self.epoch = 0
        self.device_time = None
        self.clock = None
        self.events = []
        self._reported = None

    def _clock(self, now):
        if not np.isfinite(now) or (self.clock is not None and now < self.clock):
            self.machine.reset()
            raise ValueError("Local receipt/poll clock must be finite and monotonic")
        self.clock = float(now)

    def _result(self, now, reason, accepted=False, count=None, force=False):
        state = self.machine.state
        if force or state != self._reported:
            self.events.append(
                {
                    "received_at_s": float(now),
                    "epoch": self.epoch,
                    "state": state,
                    "reason": reason,
                }
            )
            self._reported = state
        return {
            "state": state,
            "reason": reason,
            "accepted": accepted,
            "count": count,
            "epoch": self.epoch,
        }

    def poll(self, now):
        self._clock(now)
        expired = (
            self.machine.last is not None and now - self.machine.last > self.machine.gap
        )
        self.machine.value_at(now)
        return self._result(now, "stale" if expired else "poll")

    def start_epoch(self, now):
        self._clock(now)
        self.epoch += 1
        self.device_time = None
        self.machine.reset()
        return self._result(now, "source_restart", force=True)

    def observe(self, now, device_time, points, *, epoch=0, complete=True):
        self.poll(now)
        if type(epoch) is not int or epoch != self.epoch:
            return self._result(now, "wrong_epoch")
        if device_time is None or not np.isfinite(device_time):
            self.machine.reset()
            return self._result(now, "invalid_device_time")
        if self.device_time is not None and device_time <= self.device_time:
            return self._result(now, "duplicate_or_out_of_order")
        self.device_time = float(device_time)
        if not complete:
            self.machine.reset()
            return self._result(now, "incomplete_frame")
        try:
            count = self.roi.count(points)
        except (TypeError, ValueError):
            self.machine.reset()
            return self._result(now, "invalid_points")
        self.machine.update(now, count)
        return self._result(now, "measurement", accepted=True, count=count)
