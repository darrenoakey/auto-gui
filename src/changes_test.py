"""Tests for the dashboard change feed."""

from changes import note, poll, reset


def setup_function():
    reset()


def test_unchanged_revision_is_a_no():
    assert poll(0) == {"changed": False, "revision": 0}


def test_notes_collapse_to_one_message_per_type():
    note("processes", name="a")
    note("icons", name="a")
    note("processes", name="b")
    body = poll(0)
    assert body["changed"] is True
    assert body["revision"] == 3
    assert body["changes"] == [
        {"type": "processes", "seq": 3},
        {"type": "icons", "seq": 2},
    ]
    assert poll(3) == {"changed": False, "revision": 3}


def test_a_client_ahead_of_this_process_is_told_to_resync():
    note("processes")
    body = poll(9)
    assert body == {
        "changed": True,
        "revision": 1,
        "changes": [{"type": "resync", "seq": 1}],
    }


def test_rolled_off_events_are_a_resync_not_a_partial_feed():
    for _ in range(70):
        note("icons")
    body = poll(0)
    assert body["changes"] == [{"type": "resync", "seq": body["revision"]}]
    assert body["revision"] == 70
