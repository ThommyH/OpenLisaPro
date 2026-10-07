"""PID controller with output clamping."""

from __future__ import annotations

from typing import Optional


class PIDController:
    def __init__(
        self,
        *,
        kp: float = 35.0,
        ki: float = 0.12,
        kd: float = 6.0,
        integral_limit: float = 40.0,
        output_min: float = -100.0,
        output_max: float = 100.0,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.integral_limit = abs(integral_limit)
        self.output_min = output_min
        self.output_max = output_max
        self._integral = 0.0
        self._prev_error: Optional[float] = None
        self._prev_time: Optional[float] = None

    def configure(self, **kwargs: float) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, float(value))
        self.integral_limit = abs(self.integral_limit)

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = None
        self._prev_time = None

    def update(self, error: float, now: float, *, deadband: float = 0.0) -> float:
        if abs(error) <= deadband:
            # Hold integral; derivative settles toward zero.
            derivative = 0.0
            if self._prev_error is not None and self._prev_time is not None:
                dt = max(1e-3, now - self._prev_time)
                derivative = (error - self._prev_error) / dt
            self._prev_error = error
            self._prev_time = now
            return self._clamp(self.kp * error + self.ki * self._integral + self.kd * derivative)

        if self._prev_time is None:
            dt = 0.0
        else:
            dt = max(0.0, now - self._prev_time)

        if dt > 0:
            self._integral += error * dt
            self._integral = max(-self.integral_limit, min(self.integral_limit, self._integral))
            derivative = (error - (self._prev_error or 0.0)) / dt
        else:
            derivative = 0.0

        self._prev_error = error
        self._prev_time = now
        return self._clamp(self.kp * error + self.ki * self._integral + self.kd * derivative)

    def _clamp(self, value: float) -> float:
        return max(self.output_min, min(self.output_max, value))

    @property
    def integral(self) -> float:
        return self._integral
