"""Config file support for PySubZone.

A small TOML file stores per-user defaults that every command falls back
to when the matching command-line option is not given.  Default location:

* Linux / other : ``~/.config/pysubzone/config.toml`` (XDG_CONFIG_HOME honored)
* macOS         : ``~/Library/Application Support/pysubzone/config.toml``
* Windows       : ``%APPDATA%\\pysubzone\\config.toml``

Set the ``PYSUBZONE_CONFIG`` environment variable to use a different file
(honored by the CLI and the TUI alike - this is also what the tests use).

Manage it from the command line::

    subz config show                 # print path + every effective value
    subz config set defaults.language fa
    subz config set defaults.min-score 60
    subz config unset defaults.zip
    subz config path

Keys are dotted ``"<section>.<name>"`` strings; unknown keys are rejected
when setting but preserved when they already sit in the file (so hand
edits never get lost).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

try:  # Python >= 3.11
    import tomllib  # type: ignore
except ImportError:  # Python 3.9 / 3.10 (dependency: tomli)
    tomllib = None  # type: ignore

CONFIG_ENV_VAR = "PYSUBZONE_CONFIG"

# dotted key -> (section, python type, human description)
CONFIG_SPEC: Dict[str, Tuple[str, type, str]] = {
    "defaults.language": ("defaults", str, "Default subtitle language (fa, en, tr, ...)."),
    "defaults.out": ("defaults", str, "Default -o/--out output folder."),
    "defaults.zip": ("defaults", bool, "Keep zips instead of extracting (--zip)."),
    "defaults.lang-suffix": ("defaults", str, "Language suffix for extracted files (empty = auto)."),
    "defaults.no-suffix": ("defaults", bool, "Name files Movie.srt instead of Movie.fa.srt."),
    "defaults.min-score": ("defaults", int, "Minimum auto-match score 0-100 (0 = accept every best match)."),
    "defaults.prefer-complete": ("defaults", bool, "Prefer one same-season pack covering all episode videos in `subz auto` (--prefer-complete)."),
    "defaults.workers": ("defaults", int, "Parallel downloads for `subz auto` (1-8)."),
    "network.base-url": ("network", str, "Site base URL."),
    "network.user-agent": ("network", str, "HTTP User-Agent header."),
    "network.timeout": ("network", float, "HTTP timeout in seconds."),
    "network.retries": ("network", int, "Retries per request."),
    "network.verbose": ("network", bool, "Log HTTP requests."),
    "network.min-interval": ("network", float, "Minimum seconds between HTTP requests (politeness rate limit, 0 = off)."),
    "network.cache-ttl": ("network", float, "Seconds to keep fetched pages in an in-memory cache (0 = off)."),
}

# dotted key -> (inclusive minimum, inclusive maximum) for numeric keys
_CONFIG_BOUNDS: Dict[str, Tuple[float, float]] = {
    "defaults.min-score": (0, 100),
    "defaults.workers": (1, 8),
    "network.timeout": (1, 600),
    "network.retries": (0, 10),
    "network.min-interval": (0, 60),
    "network.cache-ttl": (0, 3600),
}


def config_dir() -> Path:
    """Directory holding config.toml and tui settings.json."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pysubzone"


def config_path() -> Path:
    """Full path of the TOML config file."""
    return config_dir() / "config.toml"


def tui_settings_path() -> Path:
    """Full path of the TUI's remembered-settings JSON file."""
    return config_dir() / "settings.json"


# ---------------------------------------------------------------------------
# reading / writing
# ---------------------------------------------------------------------------

def _read_toml(path: Path, strict: bool) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    if tomllib is None:
        raise RuntimeError(
            "reading the config file on Python < 3.11 requires the 'tomli' package"
        )
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001 - user-editable file, be explicit
        if strict:
            raise ValueError(f"config file is not valid TOML: {path} ({exc})") from exc
        return {}


def load_config(strict: bool = False) -> Dict[str, Any]:
    """Load the config file as a flat ``{dotted.key: value}`` dict.

    Returns ``{}`` when the file is missing or (unless *strict*) malformed,
    so a broken config never breaks the commands themselves; ``subz config
    show`` passes ``strict=True`` to surface the problem.
    """
    data = _read_toml(config_path(), strict=strict)
    flat: Dict[str, Any] = {}
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            flat[f"{section}.{name}"] = value
    return flat


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # json.dumps yields a valid TOML basic string (quotes + escapes)
    return json.dumps(str(value), ensure_ascii=False)


def save_config(flat: Dict[str, Any]) -> None:
    """Write a flat ``{dotted.key: value}`` dict back as sectioned TOML."""
    nested: Dict[str, Dict[str, Any]] = {}
    for key, value in flat.items():
        section, _, name = key.partition(".")
        if not section or not name:
            continue
        nested.setdefault(section, {})[name] = value
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# pysubzone configuration - manage with: subz config set <key> <value>\n"
        "# keys are <section>.<name>, see: subz config show\n"
    )
    lines = [header]
    for section, values in nested.items():
        lines.append(f"[{section}]")
        for name, value in values.items():
            lines.append(f"{name} = {_format_toml_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# key operations
# ---------------------------------------------------------------------------

def _check_bounds(key: str, number: float) -> None:
    if key in _CONFIG_BOUNDS:
        low, high = _CONFIG_BOUNDS[key]
        if not low <= number <= high:
            raise ValueError(f"{key}: value {number} out of range {low}..{high}")


def coerce_value(key: str, raw: str) -> Any:
    """Parse a string from the command line into the declared type of *key*."""
    if key not in CONFIG_SPEC:
        raise KeyError(
            f"unknown config key {key!r} - run 'subz config show' for the list"
        )
    typ = CONFIG_SPEC[key][1]
    text = str(raw).strip()
    if typ is bool:
        low = text.lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{key}: expected a boolean (true/false), got {raw!r}")
    if typ is int:
        try:
            number = int(text)
        except ValueError:
            raise ValueError(f"{key}: expected an integer, got {raw!r}") from None
        _check_bounds(key, number)
        return number
    if typ is float:
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{key}: expected a number, got {raw!r}") from None
        _check_bounds(key, number)
        return number
    return text


def set_config_value(key: str, raw: str) -> Any:
    """Validate, coerce and persist one key; returns the stored value."""
    typed = coerce_value(key, raw)
    flat = load_config()
    flat[key] = typed
    save_config(flat)
    return typed


def unset_config_value(key: str) -> bool:
    """Remove one key from the file; False when it was not set."""
    if key not in CONFIG_SPEC:
        raise KeyError(
            f"unknown config key {key!r} - run 'subz config show' for the list"
        )
    flat = load_config()
    if key not in flat:
        return False
    del flat[key]
    save_config(flat)
    return True


# ---------------------------------------------------------------------------
# TUI remembered settings (last destination folder, language filter, ...)
# ---------------------------------------------------------------------------

def load_tui_settings() -> Dict[str, Any]:
    try:
        return json.loads(tui_settings_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/corrupt settings must never crash the TUI
        return {}


def save_tui_settings(data: Dict[str, Any]) -> None:
    """Merge *data* into settings.json; failures are silently ignored."""
    try:
        path = tui_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        current = load_tui_settings()
        current.update(data)
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
