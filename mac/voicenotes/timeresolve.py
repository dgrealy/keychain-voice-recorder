"""Work out when a note was recorded (``docs/PROTOCOL.md`` section 3).

The keychain has no battery-backed clock, so its wall clock is only right after it has
synced over NTP. Its monotonic clock, though, counts seconds since power-on and survives
deep sleep. As long as the device has not lost power since recording, the Mac can
subtract the note's age from its own clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

EARLIEST = datetime(2020, 1, 1, tzinfo=timezone.utc)
FUTURE_TOLERANCE = timedelta(minutes=5)


def resolve_recorded_at(meta: dict, received_at: datetime, boot_id: str | None,
                        device_mono: int | None) -> tuple[datetime, str]:
    """Pick the best available recording time.

    Parameters
    ----------
    meta : dict
        Device sidecar. Uses ``time_synced``, ``started_at``, ``boot_id`` and ``started_mono``.
    received_at : datetime
        Timezone-aware time the Mac received the upload.
    boot_id : str or None
        The device's current boot id (``X-VN-Boot-Id``).
    device_mono : int or None
        The device's current monotonic clock (``X-VN-Device-Mono``).

    Returns
    -------
    recorded_at : datetime
        Timezone-aware UTC recording time.
    source : {'device_clock', 'relative', 'received'}
        Which rule produced it.
    """
    if meta.get("time_synced"):
        t = datetime.fromtimestamp(meta["started_at"], timezone.utc)
        if EARLIEST <= t <= received_at + FUTURE_TOLERANCE:
            return t, "device_clock"
    if boot_id and boot_id == meta.get("boot_id") and device_mono is not None:
        age = device_mono - meta["started_mono"]
        if age >= 0:
            return received_at - timedelta(seconds=age), "relative"
    return received_at, "received"
