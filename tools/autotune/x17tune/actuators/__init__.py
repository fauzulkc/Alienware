"""Platform back-ends that turn Decisions into hardware settings."""

from __future__ import annotations

import sys

from .base import Actuator, DryRunner, Runner


def for_platform(apply: bool, platform: str | None = None, **kw) -> Actuator:
    platform = platform or sys.platform
    runner: Runner = Runner() if apply else DryRunner()
    if platform.startswith("win"):
        from .windows import WindowsActuator

        return WindowsActuator(runner, **kw)
    from .linux import LinuxActuator

    return LinuxActuator(runner, **kw)


__all__ = ["Actuator", "DryRunner", "Runner", "for_platform"]
