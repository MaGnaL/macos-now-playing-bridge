#!/usr/bin/env python3
"""
macOS Now Playing Bridge

Serves a local REST API compatible with the SMTC Bridge JSON schema
(https://github.com/nuttylmao/smtc-bridge) so the nutty.gg "Now Playing"
OBS widget can be used on macOS instead of Windows.

Reads system-wide now-playing info via the private MediaRemote.framework,
using the mediaremote-adapter workaround (see vendor/README below) since
Apple locked down direct access to it starting with macOS 15.4.

Limitation: macOS only ever exposes ONE "current" now-playing app at a
time (whatever the OS considers active), unlike Windows SMTC which lists
all active sessions simultaneously. This bridge therefore always reports
zero or one session.

Usage:
    python3 now_playing_bridge.py [--host 127.0.0.1] [--port 5000]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_VERSION = "1.0.0"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(SCRIPT_DIR, "vendor")
PERL_SCRIPT = os.path.join(VENDOR_DIR, "mediaremote-adapter.pl")
FRAMEWORK_PATH = os.path.join(VENDOR_DIR, "MediaRemoteAdapter.framework")
TEST_CLIENT_PATH = os.path.join(VENDOR_DIR, "MediaRemoteAdapterTestClient")


########################
### SMTC BRIDGE ENUMS ###
########################

class PlaybackStatus:
    CLOSED, OPENED, CHANGING, STOPPED, PLAYING, PAUSED = range(6)


class PlaybackType:
    UNKNOWN, MUSIC, VIDEO, IMAGE = range(4)


class AutoRepeatMode:
    NONE, TRACK, LIST = range(3)


# MediaRemote shuffleMode/repeatMode use 1-based enums; see mediaremote-adapter README.
SHUFFLE_ACTIVE_MODES = {2, 3}
REPEAT_MODE_MAP = {1: AutoRepeatMode.NONE, 2: AutoRepeatMode.TRACK, 3: AutoRepeatMode.LIST}


############################
### MEDIAREMOTE WATCHER ###
############################

class MediaRemoteWatcher:
    """Keeps the latest now-playing state by tailing `mediaremote-adapter.pl stream`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._raw: dict = {}
        self._process: subprocess.Popen | None = None
        self._stop_requested = False
        self._thread = threading.Thread(target=self._run_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._raw)

    def _run_forever(self) -> None:
        # If the stream process ever dies (crash, MediaRemote hiccup), restart it
        # after a short delay instead of leaving the bridge stuck on stale data.
        while not self._stop_requested:
            try:
                self._stream_once()
            except Exception as exc:
                print(f"[watcher] stream crashed: {exc}", file=sys.stderr)
            if not self._stop_requested:
                time.sleep(2)

    def _stream_once(self) -> None:
        cmd = ["/usr/bin/perl", PERL_SCRIPT, FRAMEWORK_PATH, "stream", "--debounce=100"]
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
        )
        assert self._process.stdout is not None
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._apply(message)
        self._process.wait()

    def _apply(self, message: dict) -> None:
        payload = message.get("payload") or {}
        is_diff = bool(message.get("diff"))
        with self._lock:
            if is_diff:
                for key, value in payload.items():
                    if value is None:
                        self._raw.pop(key, None)
                    else:
                        self._raw[key] = value
            else:
                self._raw = {k: v for k, v in payload.items() if v is not None}


#############################
### PAYLOAD CONSTRUCTION ###
#############################

def build_session(raw: dict) -> dict | None:
    # MediaRemote reports "null" (empty payload) when nothing is playing,
    # and title is the one field the adapter always guarantees when present.
    if not raw or not raw.get("title"):
        return None

    playing = bool(raw.get("playing", False))
    artist = raw.get("artist") or "Unknown"
    album = raw.get("album") or "Unknown"
    genre = raw.get("genre")

    artwork_data = raw.get("artworkData")
    artwork_mime = raw.get("artworkMimeType") or "image/jpeg"
    thumbnail = f"data:{artwork_mime};base64,{artwork_data}" if artwork_data else None

    duration_ms = int((raw.get("duration") or 0) * 1000)
    position_ms = int((raw.get("elapsedTime") or 0) * 1000)

    # No direct media-type field is reliably populated; infer like the Linux
    # MPRIS port does: artist/album metadata present means it's music.
    playback_type = PlaybackType.MUSIC if (raw.get("artist") or raw.get("album")) else PlaybackType.UNKNOWN

    return {
        "source_app_id": raw.get("bundleIdentifier") or "unknown",
        "media_properties": {
            "Title": raw.get("title") or "Unknown",
            "Artist": artist,
            "AlbumTitle": album,
            # MediaRemote has no distinct "album artist" field.
            "AlbumArtist": "Unknown",
            "Thumbnail": thumbnail,
            "AlbumTrackCount": raw.get("totalTrackCount") or 0,
            "TrackNumber": raw.get("trackNumber") or 0,
            "Genres": [genre] if genre else [],
            "Subtitle": "",
        },
        "playback_info": {
            "PlaybackStatus": PlaybackStatus.PLAYING if playing else PlaybackStatus.PAUSED,
            "PlaybackType": playback_type,
            "PlaybackRate": raw.get("playbackRate", 1.0),
            "IsShuffleActive": raw.get("shuffleMode") in SHUFFLE_ACTIVE_MODES,
            "AutoRepeatMode": REPEAT_MODE_MAP.get(raw.get("repeatMode"), AutoRepeatMode.NONE),
        },
        "timeline_properties": {
            "Position": position_ms,
            "StartTime": 0,
            "EndTime": duration_ms,
            "MinSeekTime": 0,
            "MaxSeekTime": duration_ms,
            "LastUpdatedTime": raw.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def build_payload(raw: dict) -> dict:
    session = build_session(raw)
    sessions = [session] if session else []
    return {
        "app_version": APP_VERSION,
        "os": f"macOS {platform.mac_ver()[0]}",
        # macOS never exposes more than one active session, so this is
        # simply the one session we have, unlike Windows SMTC's real
        # "currently focused among many" selection.
        "current_session_id": session["source_app_id"] if session else None,
        "sessions": sessions,
    }


####################
### HTTP SERVER ###
####################

watcher = MediaRemoteWatcher()


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        # Chrome's Private Network Access checks require this on every
        # response, not just the preflight, once a private-network fetch
        # has been permitted.
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "0")
        # Required for Chrome's Private Network Access preflight: without
        # this, a page served over https fetching http://127.0.0.1 gets
        # silently blocked (surfaces as a generic "Failed to fetch").
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/now-playing":
            try:
                self._send_json(build_payload(watcher.snapshot()))
            except Exception as exc:
                self._send_json(
                    {"app_version": APP_VERSION, "current_session_id": None, "sessions": [], "error": str(exc)},
                    status=500,
                )
        elif path == "/health":
            self._send_json({"ok": True, "app_version": APP_VERSION})
        else:
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args) -> None:
        pass  # keep the terminal quiet; nothing useful to log per request


####################
### ENTRY POINT ###
####################

def check_adapter() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/perl", PERL_SCRIPT, FRAMEWORK_PATH, TEST_CLIENT_PATH, "test"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="macOS Now Playing Bridge (SMTC Bridge compatible)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    if not os.path.exists(FRAMEWORK_PATH) or not os.path.exists(PERL_SCRIPT):
        print(f"Missing vendor files in {VENDOR_DIR} - see README.md", file=sys.stderr)
        sys.exit(1)

    if not check_adapter():
        print(
            "WARNING: MediaRemote adapter self-test failed on this macOS version - "
            "now-playing info may not be available. See vendor/mediaremote-adapter for details.",
            file=sys.stderr,
        )

    watcher.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Now Playing Bridge running: http://{args.host}:{args.port}/now-playing")
    print("Press Ctrl+C to stop.")

    def handle_signal(signum, frame) -> None:
        # The default SIGTERM action kills the interpreter immediately
        # without running atexit handlers, which would orphan the Perl
        # adapter subprocess. Clean it up explicitly before exiting.
        print("\nShutting down...")
        watcher.stop()
        os._exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server.serve_forever()


if __name__ == "__main__":
    main()
