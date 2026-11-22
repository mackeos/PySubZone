# PySubZone

<p align="center">
  <strong>Find the right subtitle. Download it. Get back to watching.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/pysubzone/"><img src="https://img.shields.io/pypi/v/pysubzone?label=PyPI&color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/pysubzone/"><img src="https://img.shields.io/pypi/pyversions/pysubzone" alt="Python versions"></a>
  <a href="https://github.com/mackeos/PySubZone/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license"></a>
</p>

**PySubZone** is a Python library, command-line tool, and interactive terminal app for searching and downloading subtitles from [SubZone](https://subzone.ir/), the Subf2m subtitle database.

Whether you need one subtitle or want to match subtitles for an entire video collection, PySubZone gives you a straightforward way to find the version that fits.

Inspired by [PyAddic7](https://github.com/mackeos/PyAddic7), which works with addic7ed.com.

## What can it do?

- **Search titles** — find movies and TV shows.
- **Browse subtitle versions** — see the uploads available for a title, with language filtering.
- **Download subtitles** — download a specific upload by reference or URL, or grab the newest uploads from a title page.
- **Match video files automatically** — search for suitable subtitles based on release names, including episode number, resolution, source, and release group.
- **Use it your way** — choose the Python API, the command-line interface, or the interactive terminal UI.

The CLI is built with [Typer](https://typer.tiangolo.com/) and Rich, while the interactive TUI is powered by [Textual](https://github.com/Textualize/textual).

> **A note on responsible use:** PySubZone accesses a public website for personal use. Please keep request rates reasonable and respect the source website's terms. Do not redistribute downloaded subtitles.

## Packages

| Package | Purpose | PyPI |
|---|---|---|
| **PySubZone** (`pysubzone`) | Main Python API, CLI, and TUI package | [pypi.org/project/pysubzone](https://pypi.org/project/pysubzone/) |
| **PySubF2M** (`pysubf2m`) | Compatibility package | coming soon |

## Installation

Python **3.9 or newer** is required.

Install the main package with pip:

```bash
pip install pysubzone
```

Or use [pipx](https://pipx.pypa.io/) to install the command-line application in an isolated environment:

```bash
pipx install pysubzone
```

A compatibility package, **PySubF2M** (`pysubf2m`), is planned as a separate release.

After installing PySubZone, the `subz` command is available. You can also run it with:

```bash
python -m pysubzone
```

## Quick start

Search for a title, inspect its subtitle versions, and download one:

```bash
# Search for a movie or show
subz search "dexter resurrection"

# Show the available Farsi subtitle versions
subz versions dexter-resurrection-season-2 -l fa

# Download a specific version
subz download dexter-resurrection-season-2/farsi_persian/3770171
```

## Command-line reference

```text
subz search QUERY [-l LANG] [--json]          Search titles
subz versions TITLE [-l LANG] [--json]        List subtitle uploads
subz download REF [-o DIR] [--zip] [--json]   Download by reference, URL, or slug
subz url URL [-l LANG] [-n N] [--zip]         Download from a SubZone URL
subz filename FILE [-l LANG] [--no-download]  Find the best subtitle for one video
subz auto PATH [-l LANG] [--dry-run] [--zip]   Match videos in a folder
subz popular [--mode popular|latest]           Browse popular or latest uploads
            [-t all|film|series|music] [-p PAGE]
subz tui                                       Open the interactive interface
```

Global options:

- `--base-url` — use a different base URL.
- `--user-agent` — set the HTTP user agent.
- `--timeout` — configure the request timeout.
- `--retries` — configure retry attempts.
- `-v`, `--verbose` — enable verbose output.

Every data command supports `--json` for scripting and automation.

Exit codes follow the PyAddic7 contract:

| Code | Meaning |
|---:|---|
| `0` | Success |
| `1` | Nothing found |
| `2` | Error |

### Search and browse

```bash
# Search for a title
subz search "dexter resurrection"

# Search for titles with Farsi subtitles
subz search odyssey -l fa

# List Farsi subtitle versions for a title
subz versions dexter-resurrection-season-2 -l fa

# Browse popular movie uploads
subz popular -t film

# Browse the latest series uploads, page 2
subz popular --mode latest -t series -p 2
```

### Download subtitles

A **reference** identifies one subtitle upload and follows this format:

```text
<slug>/<lang>/<id>
```

You can copy a reference from a version listing.

```bash
# Download using a reference
subz download dexter-resurrection-season-2/farsi_persian/3770171

# Download using the full subtitle URL
subz download https://subzone.ir/subtitles/dexter-resurrection-season-2/farsi_persian/3770171

# Grab the two newest Farsi uploads from a title URL
subz url https://subzone.ir/subtitles/the-odyssey-2026 -l fa -n 2

# Grab all available uploads from that title URL
subz url https://subzone.ir/subtitles/the-odyssey-2026 -l fa -n 0

# Download by title slug
subz download the-odyssey-2026 -l en --zip --out ./downloads
```

By default, downloaded archives are **extracted** and subtitle files receive a language suffix, such as `Movie.fa.srt` (or retain the release name when the archive includes one).

Useful output options:

- `--zip` — keep the original archive instead of extracting it.
- `--no-suffix` — do not append a language suffix.
- `--lang-suffix XX` — choose a custom language suffix.
- `-o DIR` / `--out DIR` — choose the output directory.

### Automatically match a video collection

PySubZone can inspect video release names, search the site, score available subtitle versions, and download the best match.

```bash
# Find a subtitle for one video
subz filename "Dexter.Resurrection.S02E01.1080p.AMZN.WEB-DL-NTb.mkv"

# Preview matches in a folder without downloading
subz auto /media/shows -l fa --dry-run

# Download and extract subtitles beside the videos
subz auto /media/shows -l fa

# Save all subtitles into a separate folder
subz auto /media/shows -l fa -o ./subs
```

For each video, the release name is parsed for details such as the title, season and episode, resolution, source, and release group. Subtitle versions with conflicting episode numbers are rejected, exact release matches are preferred, and season packs can be used as fallbacks.

A failed match does not stop the whole collection: unsuccessful files are included in the final summary.

## Interactive terminal interface

Prefer an interactive experience? Run:

```bash
subz tui
```

The TUI guides you through the process:

1. Enter a search query to see matching titles.
2. Select a title to browse its uploaded subtitle versions.
3. Filter versions by language.
4. Select a version to open the download dialog.
5. Choose a destination and whether to extract subtitles or keep the ZIP archive.

The download dialog includes a live progress bar.

**Keyboard shortcuts**

| Key | Action |
|---|---|
| `Esc` | Go back or close the current view |
| `r` | Refresh subtitle versions |
| `Ctrl+Q` | Quit |

### TUI screenshots

| Search | Subtitle versions | Download |
|---|---|---|
| ![Search screen](https://raw.githubusercontent.com/mackeos/PySubZone/master/docs/tui_search.svg) | ![Versions screen](https://raw.githubusercontent.com/mackeos/PySubZone/master/docs/tui_versions.svg) | ![Download dialog](https://raw.githubusercontent.com/mackeos/PySubZone/master/docs/tui_download.svg) |

## Python API

You can use PySubZone directly from your own Python projects:

```python
from pysubzone import SubZoneClient

client = SubZoneClient(timeout=20)

# Search for titles
results = client.search("dexter resurrection")

# Find a title and list its Farsi subtitle versions
title = client.search_exact("Dexter Resurrection")
versions = client.title_versions(title.slug, "farsi_persian")

# Download the first version and extract it into ./subs
result = client.download(
    versions[0],
    dest_dir="subs",
    save_mode="extract",
)
print(result.srt_paths)

# Find the best subtitle for a video release name
best_title, version, score = client.best_for_file(
    "Breaking.Bad.S05E14.720p.BluRay.x264-DEMAND"
)

# Download the match and associate it with the video filename
client.download(
    version,
    dest_dir=".",
    save_mode="extract",
    target_video="Breaking.Bad.S05E14.mkv",
)

# Download from a SubZone title URL
client.download_url(
    "/subtitles/the-odyssey-2026",
    lang="fa",
    limit=1,
    dest_dir="subs",
)
```

All client exceptions derive from `pysubzone.SubZoneError`. Specific errors include `NetworkError`, `TitleNotFoundError`, `NoSubtitleFoundError`, and `DownloadError`.

## How it works

The project is organized into a few focused modules:

```text
src/pysubzone/
  client.py       HTTP client, retries, signed-CDN downloads, ZIP verification
  parsing.py      Search, title, version, and browse-page parsers
  models.py       Data models with as_dict() support for JSON output
  languages.py    Language slugs and aliases (fa, en, tr, ...)
  matcher.py      Release-name parsing and video-folder scanning
  scoring.py      Subtitle-version scoring for auto-match
  cli.py          Typer command-line interface
  tui.py          Textual terminal interface
tests/            Offline tests using saved HTML fixtures
```

### Website endpoints and client methods

| SubZone route | PySubZone method |
|---|---|
| `/subtitles/searchbytitle?query=...` | `search()`, `search_exact()` |
| `/subtitles/<slug>` (optionally `/<lang>`) | `title()`, `title_versions()` |
| `/subtitles/<slug>/<lang>/<id>` | `get_version()` |
| `/subtitles/<slug>/<lang>/<id>/download` | `download()` |
| `/browse/popular|latest/...` | `browse()` |

Downloads follow a redirect to a signed CDN URL. Before saving a download, PySubZone verifies the ZIP file signature. This helps detect stale uploads that return an S3 `NoSuchKey` XML response with HTTP 200; these are reported as `DownloadError` with a hint to try another version.

## Development

Clone the repository, install the project in editable mode with development dependencies, and run the test suite:

```bash
pip install -e ".[dev]"
pytest
```

The test suite is designed to run **offline**. It uses saved HTML fixtures and synthetic release names, so tests do not need to access SubZone.

## Support the project

If PySubZone saves you time or makes subtitle management easier, consider supporting its continued development. Donations are entirely optional — using the project, reporting bugs, sharing feedback, and contributing code are all valuable ways to help.

See **[DONATE.md](https://github.com/mackeos/PySubZone/blob/master/DONATE.md)** for cryptocurrency donation options.

## License

PySubZone is released under the **MIT License**. See [LICENSE](https://github.com/mackeos/PySubZone/blob/master/LICENSE) for the full license text.
