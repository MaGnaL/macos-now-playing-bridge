#!/usr/bin/env python3
"""Self-check for the state merge and the SMTC payload mapping.

Run: python3 test_bridge.py   (pytest also picks up the test_* functions)
"""

from now_playing_bridge import (
    AutoRepeatMode,
    MediaRemoteWatcher,
    PlaybackStatus,
    PlaybackType,
    build_payload,
    build_session,
)


def _watcher_after(*messages: dict) -> dict:
    watcher = MediaRemoteWatcher()
    for message in messages:
        watcher._apply(message)
    return watcher.snapshot()


def test_full_message_replaces_state_and_drops_nulls() -> None:
    state = _watcher_after(
        {"payload": {"title": "Old", "artist": "A"}},
        {"payload": {"title": "New", "album": None}},
    )
    assert state == {"title": "New"}


def test_diff_merges_and_null_removes_key() -> None:
    state = _watcher_after(
        {"payload": {"title": "T", "artist": "A", "playing": True}},
        {"diff": True, "payload": {"playing": False, "artist": None}},
    )
    assert state == {"title": "T", "playing": False}


def test_empty_payload_clears_state() -> None:
    assert _watcher_after({"payload": {"title": "T"}}, {"payload": {}}) == {}


def test_no_title_means_no_session() -> None:
    assert build_session({}) is None
    assert build_session({"artist": "A", "playing": True}) is None
    payload = build_payload({})
    assert payload["sessions"] == []
    assert payload["current_session_id"] is None


def test_session_mapping() -> None:
    session = build_session(
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "bundleIdentifier": "com.spotify.client",
            "playing": True,
            "duration": 200.5,
            "elapsedTime": 12.25,
            "shuffleMode": 2,
            "repeatMode": 3,
            "artworkData": "QUJD",
            "artworkMimeType": "image/png",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    assert session["source_app_id"] == "com.spotify.client"
    assert session["media_properties"]["Thumbnail"] == "data:image/png;base64,QUJD"
    assert session["playback_info"]["PlaybackStatus"] == PlaybackStatus.PLAYING
    assert session["playback_info"]["PlaybackType"] == PlaybackType.MUSIC
    assert session["playback_info"]["IsShuffleActive"] is True
    assert session["playback_info"]["AutoRepeatMode"] == AutoRepeatMode.LIST
    assert session["timeline_properties"]["Position"] == 12250
    assert session["timeline_properties"]["EndTime"] == 200500
    assert session["timeline_properties"]["LastUpdatedTime"] == "2026-01-01T00:00:00Z"
    assert build_payload({"title": "Song", "bundleIdentifier": "x"})["current_session_id"] == "x"


def test_missing_fields_fall_back_to_defaults() -> None:
    session = build_session({"title": "Song"})
    assert session["source_app_id"] == "unknown"
    assert session["media_properties"]["Artist"] == "Unknown"
    assert session["media_properties"]["Thumbnail"] is None
    assert session["playback_info"]["PlaybackStatus"] == PlaybackStatus.PAUSED
    assert session["playback_info"]["PlaybackType"] == PlaybackType.UNKNOWN
    assert session["playback_info"]["IsShuffleActive"] is False
    assert session["playback_info"]["AutoRepeatMode"] == AutoRepeatMode.NONE


if __name__ == "__main__":
    tests = [fn for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for fn in tests:
        fn()
    print(f"{len(tests)} checks passed")
