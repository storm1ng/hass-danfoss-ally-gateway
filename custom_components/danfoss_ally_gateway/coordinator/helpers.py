"""Shared helpers for coordinator delegates."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


async def async_for_each_trv(
    trv_ids: list[str],
    func: Any,
    *args: Any,
    room_name: str = "",
    action: str = "",
) -> None:
    """Call an async backend method for each TRV, logging failures.

    Args:
        trv_ids: List of TRV identifiers.
        func: Async callable, invoked as ``func(trv_id, *args)``.
        *args: Additional positional arguments passed after trv_id.
        room_name: Room name for log messages.
        action: Human-readable action description for log messages.

    """
    for trv_id in trv_ids:
        try:
            await func(trv_id, *args)
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "Failed to %s on TRV %s in room '%s'",
                action or "perform action",
                trv_id,
                room_name,
            )
