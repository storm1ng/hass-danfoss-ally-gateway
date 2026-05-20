"""Coordinator package for Danfoss Ally Gateway.

Re-exports the public API so that ``from .coordinator import RoomCoordinator``
continues to work unchanged across the codebase.
"""

from .coordinator import RoomCoordinator, RoomState, RoomStateCallback

__all__ = [
    "RoomCoordinator",
    "RoomState",
    "RoomStateCallback",
]
