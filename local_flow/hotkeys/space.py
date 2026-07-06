"""Space as push-to-talk: hold to dictate, tap to type a normal space.

The state machine is pure and timer-agnostic: the platform glue schedules
``hold_elapsed(generation)`` after the hold threshold. Generations make a
timer that fires after the key was already released a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass

_IDLE, _PENDING, _RECORDING, _CANCELLED = "idle", "pending", "recording", "cancelled"


@dataclass
class SpaceActions:
    start: bool = False  # begin recording
    stop: bool = False  # finish recording and insert
    cancel: bool = False  # discard the recording
    replay_space: bool = False  # synthesize the swallowed space (it was a tap)
    start_timer: bool = False  # schedule hold_elapsed(machine.generation)


class SpaceStateMachine:
    def __init__(self) -> None:
        self.state = _IDLE
        self.generation = 0

    def space_down(self) -> SpaceActions:
        if self.state == _IDLE:
            self.state = _PENDING
            self.generation += 1
            return SpaceActions(start_timer=True)
        return SpaceActions()  # OS auto-repeat while pending/recording/cancelled

    def space_up(self) -> SpaceActions:
        if self.state == _PENDING:
            self.state = _IDLE
            self.generation += 1  # invalidate the in-flight hold timer
            return SpaceActions(replay_space=True)
        if self.state == _RECORDING:
            self.state = _IDLE
            return SpaceActions(stop=True)
        if self.state == _CANCELLED:
            self.state = _IDLE  # physical release after a cancel: swallow silently
        return SpaceActions()

    def hold_elapsed(self, generation: int) -> SpaceActions:
        if self.state == _PENDING and generation == self.generation:
            self.state = _RECORDING
            return SpaceActions(start=True)
        return SpaceActions()

    def cancel_down(self) -> SpaceActions:
        if self.state == _RECORDING:
            self.state = _CANCELLED  # stay parked until the physical space release
            return SpaceActions(cancel=True)
        return SpaceActions()
