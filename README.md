# macOS Now Playing Bridge

A small local server that exposes what's currently playing on your Mac
(Music, Spotify, Cider, a browser tab, ...) as a REST API, in the same JSON
shape as [SMTC Bridge](https://github.com/nuttylmao/smtc-bridge) (Windows)
and [MPRIS Bridge](https://github.com/TheDoctorTTV/mpris-bridge) (Linux).
Use it to feed OBS browser-source overlays or any other "now playing"
widget built against that schema.

## Requirements

- macOS. Tested on macOS 26 (Tahoe).
- Python 3 — already installed on macOS, nothing to `pip install`

## Quick start

```bash
python3 now_playing_bridge.py
```

That's it. It starts serving on `http://127.0.0.1:5000` and keeps running
until you stop it with `Ctrl+C`. Play something in any app and check:

```bash
curl http://127.0.0.1:5000/now-playing
```

You should get back JSON with the current title, artist, artwork, etc. If
`sessions` is an empty list, nothing is currently registered as playing.

Need a different host/port?

```bash
python3 now_playing_bridge.py --host 0.0.0.0 --port 5050
```

## Using it with a widget (e.g. OBS)

Point your widget/browser source at this bridge's address instead of a
Windows SMTC Bridge instance. For nutty's
[Now Playing widget](https://nutty.gg/en-eur/products/universal-now-playing), for
example, that just means setting:

- **Address**: `127.0.0.1`
- **Port**: `5000` (or whatever you passed to `--port`)

Everything else about the widget's own settings (theme, colors, fonts,
animations, ...) works exactly as on Windows — those are all handled
client-side in the widget itself, independent of this bridge.

## Running it in the background

There's no tray icon or autostart baked in on purpose — this is meant to be
a small, transparent script you run when you need it (e.g. right before you
start streaming). Two easy ways to keep it running without tying up a
terminal tab:

```bash
# Start detached, logs to a file:
nohup python3 now_playing_bridge.py > /tmp/now-playing-bridge.log 2>&1 &

# Stop it again:
pkill -f now_playing_bridge.py
```

If you want it to start automatically on login instead, set up a
`launchd` user agent (`~/Library/LaunchAgents/*.plist`) that runs the same
command — not included here, but a standard, well-documented pattern if you
want to search for it.

## Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/now-playing` | Current media state as JSON (see schema below) |
| `GET` | `/health` | `{ "ok": true, "app_version": "..." }` — liveness check |

### Schema

```json
{
  "app_version": "string",
  "os": "string",
  "current_session_id": "string",
  "sessions": [
    {
      "source_app_id": "string",
      "media_properties": {
        "Title": "string",
        "Artist": "string",
        "AlbumTitle": "string",
        "AlbumArtist": "string",
        "Thumbnail": "string (base64 data URI)",
        "AlbumTrackCount": "integer",
        "TrackNumber": "integer",
        "Genres": "array of strings",
        "Subtitle": "string"
      },
      "playback_info": {
        "PlaybackStatus": "integer",
        "PlaybackType": "integer",
        "PlaybackRate": "number",
        "IsShuffleActive": "boolean",
        "AutoRepeatMode": "integer"
      },
      "timeline_properties": {
        "Position": "integer (ms)",
        "StartTime": "integer (ms)",
        "EndTime": "integer (ms)",
        "MinSeekTime": "integer (ms)",
        "MaxSeekTime": "integer (ms)",
        "LastUpdatedTime": "string (ISO 8601)"
      }
    }
  ]
}
```

`PlaybackStatus`: `0 CLOSED · 1 OPENED · 2 CHANGING · 3 STOPPED · 4 PLAYING · 5 PAUSED`
(macOS only ever reports `PLAYING`/`PAUSED`).
`PlaybackType`: `0 UNKNOWN · 1 MUSIC · 2 VIDEO · 3 IMAGE`.
`AutoRepeatMode`: `0 NONE · 1 TRACK · 2 LIST`.

## Limitation: one session at a time

Unlike Windows (SMTC) or Linux (MPRIS), macOS has no API that lists every
active media session at once — only whichever app it currently considers
"the" now-playing app. So `sessions` here always has zero or one entries. If
you pause Spotify and start a video in your browser, the browser simply
replaces Spotify in the response; it doesn't show up as a second, paused
entry. This only matters if you configure a widget to target one specific
app by name while something else has focus — for "just show whatever's
playing" (the default for most widgets), it's not a limitation at all.

## Troubleshooting

- **`WARNING: MediaRemote adapter self-test failed`** on startup — the
  workaround this project relies on
  ([mediaremote-adapter](https://github.com/ungive/mediaremote-adapter))
  stopped working on your macOS version. Check that project's GitHub issues
  for the current status; Apple has tightened access to the underlying
  private framework before and may do so again.
- **Works in OBS but not in a regular Chrome tab** — expected. Recent
  Chrome versions block a plain HTTPS page from `fetch()`-ing
  `http://127.0.0.1` under its Private Network Access / Local Network Access
  policy. OBS's built-in browser doesn't enforce this, so it isn't an issue
  for the actual OBS-widget use case.
- **`sessions` is always empty** — make sure something is actually playing
  (not just visible/open) in a media app, and that the app in question
  reports Now Playing info to the system at all (most mainstream apps do;
  some background/ambient apps don't).

## Rebuilding `vendor/` from source

`vendor/` bundles a prebuilt copy of `mediaremote-adapter`
(`MediaRemoteAdapter.framework`, `mediaremote-adapter.pl`,
`MediaRemoteAdapterTestClient`) — nothing to build for normal use. If you
ever need to rebuild it yourself (e.g. after an adapter update):

```bash
git clone https://github.com/ungive/mediaremote-adapter.git
cd mediaremote-adapter
mkdir build && cd build
cmake .. && cmake --build .
cp -R MediaRemoteAdapter.framework MediaRemoteAdapterTestClient /path/to/macos-now-playing-bridge/vendor/
cp ../bin/mediaremote-adapter.pl /path/to/macos-now-playing-bridge/vendor/
```

Requires Xcode Command Line Tools and CMake (`brew install cmake`).

## Credits

- [SMTC Bridge](https://github.com/nuttylmao/smtc-bridge) — the original
  Windows project and JSON schema this is compatible with.
- [MPRIS Bridge](https://github.com/TheDoctorTTV/mpris-bridge) — the Linux
  counterpart, same schema.
- [mediaremote-adapter](https://github.com/ungive/mediaremote-adapter) by
  Jonas van den Berg — does the actual work of talking to macOS's private
  `MediaRemote.framework`. This project would not exist without it.

## License

MIT for the code in this repository. `vendor/` contains a build of
`mediaremote-adapter`, licensed BSD-3-Clause by its authors.
