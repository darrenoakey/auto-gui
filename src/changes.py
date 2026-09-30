"""Dashboard change feed.

The browser asks about once a minute whether anything it renders has changed.
Almost every answer is no. A yes carries typed messages so the client refreshes
only after something it shows actually moved.
"""

import threading

_lock = threading.Lock()
_revision = 0
_events: list[dict] = []
_MAX_EVENTS = 64


def revision() -> int:
    """Current change-feed revision. The rendered page starts the client here."""
    with _lock:
        return _revision


def note(change_type: str, **detail) -> int:
    """Record one change. Returns the new revision."""
    global _revision
    with _lock:
        _revision += 1
        event = {"type": change_type, "seq": _revision}
        event.update(detail)
        _events.append(event)
        del _events[:-_MAX_EVENTS]
        return _revision


def poll(since: int) -> dict:
    """Return no when since is current, otherwise the typed changes since then.

    A client that is ahead of this process, or that missed events which have
    rolled off, gets a single resync message rather than a partial feed.
    """
    with _lock:
        if since == _revision:
            return {"changed": False, "revision": _revision}
        pending = [event for event in _events if event["seq"] > since]
        gap = (
            since > _revision
            or not pending
            or pending[0]["seq"] != since + 1
        )
        if gap:
            return {
                "changed": True,
                "revision": _revision,
                "changes": [{"type": "resync", "seq": _revision}],
            }
        seen: dict[str, dict] = {}
        for event in pending:
            seen[event["type"]] = {"type": event["type"], "seq": event["seq"]}
        return {
            "changed": True,
            "revision": _revision,
            "changes": list(seen.values()),
        }


def reset() -> None:
    """Clear the feed. Tests only."""
    global _revision, _events
    with _lock:
        _revision = 0
        _events = []
