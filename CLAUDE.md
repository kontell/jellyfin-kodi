# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Jellyfin-Kodi is a Kodi addon (`plugin.video.jellyfin`) that syncs Jellyfin media libraries to the Kodi database for native playback. It supports direct play, transcoding, live TV, and real-time sync via WebSocket.

## Build & Development Commands

### Build the addon
```bash
python3 build.py --version py3 --source . --target .
# Produces: plugin.video.jellyfin+py3.zip (~860K)
```
Do NOT use `--dev` — it bypasses the folder filter and bundles `.git/` etc., inflating the ZIP to ~12M. Build generates `addon.xml` from `.build/template.xml` using version/changelog from `release.yaml`.

### Run tests
```bash
pip install -r requirements-test.txt
coverage run        # Runs pytest via tox.ini config
coverage report     # View coverage summary
```

### Lint
```bash
flake8 jellyfin_kodi tests
```
Config in `tox.ini`: max-line-length 9999, PEP8 import style, ignores I202 + E203.

### Format
```bash
black .
```

### Pre-commit hooks
```bash
pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
```
Hooks: trailing-whitespace, black, flake8, editorconfig-checker, no-commit-to-branch (blocks direct commits to master/dev).

## Architecture

### Entry Points (root level)

| File | Purpose |
|---|---|
| `service.py` | Long-running service thread — starts on login, manages sync and monitoring |
| `default.py` | Plugin browsing interface — mode-based URL routing |
| `context.py` | Context menu: manage/sync items |
| `context_play.py` | Context menu: play with transcode |
| `context_record.py` | Context menu: record Live TV |

Each root entry point delegates to its counterpart in `jellyfin_kodi/entrypoint/`.

### Core Components (`jellyfin_kodi/`)

- **`jellyfin/`** — Jellyfin server communication layer
  - Uses the **Borg pattern** for shared client state across instances
  - `api.py`: REST API wrappers. `http.py`: HTTP client. `ws_client.py`: WebSocket for real-time events
  - `connection_manager.py`: Server discovery and connection state machine
  - `credentials.py`: Multi-server credential storage

- **`objects/`** — Media object mapping (Jellyfin → Kodi)
  - `movies.py`, `tvshows.py`, `music.py`, `musicvideos.py`: Media-specific mappers
  - `kodi/`: Direct Kodi database write operations
  - `obj_map.json`: JSON field mapping definitions
  - `actions.py`: Item action handlers (play, playlist, etc.)

- **`database/`** — SQLite tracking of Jellyfin↔Kodi ID mappings
  - `jellyfin_db.py`: Abstraction layer with named tuple factory
  - `queries.py`: SQL query definitions

- **`library.py`** — Threaded library sync (incremental/full/repair)
- **`full_sync.py`** — Context manager for complete library synchronization
- **`player.py`** — Extends `xbmc.Player`, handles resume points and media segment skipping
- **`monitor.py`** — Extends `xbmc.Monitor`, coordinates Kodi events with WebSocket listener
- **`connect.py`** — Server registration and login workflow
- **`views.py`** — Kodi library node management (DYNNODES for dynamic browsing)
- **`downloader.py`** — Worker thread pool for metadata fetching

- **`helper/`** — Utilities
  - `playutils.py`: Transcode vs direct play logic
  - `utils.py`: Settings access (`settings()`), window properties (`window()`), dialogs
  - `xmls.py`: XML generation for Kodi features
  - `wrapper.py`: Decorators for progress dialogs and threading

- **`livetv/`** — Live TV / IPTV Manager integration (see below)
- **`dialogs/`** — Login, server connection, user selection UI

### Key Patterns

- **Inter-component communication**: Window properties via `window()` helper, not direct references
- **Kodi API**: `xbmcvfs` for files, `xbmcgui` for UI, `xbmcplugin` for directory listings, `xbmc.Monitor` for events
- **Configuration**: Kodi addon settings accessed via `settings('setting_id')`, credentials stored in `data.json`
- **Threading**: Service thread, library sync thread, playback monitor, WebSocket listener, download worker queue

## Live TV / IPTV Manager Integration

Jellyfin Live TV channels are exposed to Kodi's EPG via **IPTV Simple Client** and **IPTV Manager**. This is the active area of development.

### How it works

1. **IPTV Manager** (a separate Kodi addon) calls into this plugin via `RunPlugin()` with `?mode=iptv_channels&port=N` or `?mode=iptv_epg&port=N`.
2. The `default.py` entry point routes these to `IPTVManager`, which connects back to IPTV Manager's localhost socket and sends JSON payloads (JSON-STREAMS v1 for channels, JSON-EPG v1 for programme guide).
3. IPTV Manager feeds this data to **IPTV Simple Client** (PVR addon), which populates Kodi's native EPG and channel list.
4. Channel stream URLs use `plugin://plugin.video.jellyfin/?id=<channel_id>&mode=play&server=<id>`, so playback goes through the plugin's normal `Actions.play()` path.

### Key files

| File | Role |
|---|---|
| `jellyfin_kodi/livetv/livetv.py` | `LiveTV` class — fetches channels/programmes from Jellyfin API, builds IPTV Manager payloads, manages stream URLs |
| `jellyfin_kodi/livetv/iptvmanager.py` | `IPTVManager` class — handles the socket callback protocol to send data back to IPTV Manager |
| `jellyfin_kodi/jellyfin/api.py` | `get_channels()`, `get_programs()` — Jellyfin REST API wrappers for Live TV endpoints |
| `jellyfin_kodi/entrypoint/default.py` | Routes `iptv_channels` and `iptv_epg` modes |
| `resources/settings.xml` | Live TV settings section (EPG days, force transcode toggle) |

### Force Transcode setting

The `livetv.force_transcode` setting (default on) appends `&transcode=true` to stream URLs and enables `inputstream.ffmpegdirect` with HLS/timeshift for channel playback. Users with direct-stream setups can disable this.

## CI/CD

- **test.yaml**: Runs on PRs and master pushes. Matrix: Python 3.8–3.14, Ubuntu + Windows. Runs flake8 then pytest with coverage.
- **build.yaml**: Builds addon ZIP on master push and tags.
- **publish.yaml**: Manual trigger — creates GitHub release, builds, deploys to repo server.

## Versioning

Version defined in `release.yaml` (currently 2.0.0). Build injects it into `addon.xml` with a `+py3` suffix. Changelog is also in `release.yaml`.
