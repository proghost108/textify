"""
textify/themes.py — colour themes and a tiny settings file.

Themes are plain data; the GUI applies them in `TextifyApp.apply_theme()`.
The chosen theme is remembered in `~/.config/textify/config.json`.  The
directory can be overridden with the `TEXTIFY_CONFIG_DIR` environment
variable (the tests use that to avoid touching a real user's config).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THEME = "Dark"


@dataclass(frozen=True)
class Theme:
    """Every colour the app needs, in one place."""

    name: str
    dark: bool
    # editor
    editor_bg: str
    editor_fg: str
    selection_bg: str
    cursor: str
    # window / widgets
    window_bg: str
    chrome_bg: str
    chrome_fg: str
    chrome_active_bg: str
    # status bar
    status_bg: str
    status_fg: str
    status_border: str
    # accents (status dot today, highlights later)
    accent: str
    success: str
    warning: str
    error: str
    # menu bar
    menu_bg: str
    menu_fg: str
    menu_active_bg: str
    menu_active_fg: str
    muted_fg: str


THEMES: dict[str, Theme] = {
    theme.name: theme
    for theme in (
        Theme(
            name="Dark",
            dark=True,
            editor_bg="#1e1e1e",
            editor_fg="#d4d4d4",
            selection_bg="#264f78",
            cursor="#d4d4d4",
            window_bg="#252526",
            chrome_bg="#333333",
            chrome_fg="#d4d4d4",
            chrome_active_bg="#444444",
            status_bg="#181819",
            status_fg="#b9b9c0",
            status_border="#2a2a2d",
            accent="#4c9aff",
            success="#3fb950",
            warning="#d29922",
            error="#f85149",
            menu_bg="#333333",
            menu_fg="#d4d4d4",
            menu_active_bg="#094771",
            menu_active_fg="#ffffff",
            muted_fg="#7a7a7a",
        ),
        Theme(
            name="Light",
            dark=False,
            editor_bg="#ffffff",
            editor_fg="#1f1f1f",
            selection_bg="#add6ff",
            cursor="#1f1f1f",
            window_bg="#f3f3f3",
            chrome_bg="#e8e8e8",
            chrome_fg="#1f1f1f",
            chrome_active_bg="#d6d6d6",
            status_bg="#fafafa",
            status_fg="#5c5c66",
            status_border="#e2e2e6",
            accent="#0969da",
            success="#1a7f37",
            warning="#9a6700",
            error="#cf222e",
            menu_bg="#f3f3f3",
            menu_fg="#1f1f1f",
            menu_active_bg="#cfe8ff",
            menu_active_fg="#000000",
            muted_fg="#9a9a9a",
        ),
        Theme(
            name="Nord",
            dark=True,
            editor_bg="#2e3440",
            editor_fg="#d8dee9",
            selection_bg="#434c5e",
            cursor="#88c0d0",
            window_bg="#2e3440",
            chrome_bg="#3b4252",
            chrome_fg="#d8dee9",
            chrome_active_bg="#4c566a",
            status_bg="#272b33",
            status_fg="#a9b6cc",
            status_border="#3b4252",
            accent="#88c0d0",
            success="#a3be8c",
            warning="#ebcb8b",
            error="#bf616a",
            menu_bg="#3b4252",
            menu_fg="#d8dee9",
            menu_active_bg="#5e81ac",
            menu_active_fg="#eceff4",
            muted_fg="#616e88",
        ),
        Theme(
            name="Dracula",
            dark=True,
            editor_bg="#282a36",
            editor_fg="#f8f8f2",
            selection_bg="#44475a",
            cursor="#f8f8f2",
            window_bg="#21222c",
            chrome_bg="#343746",
            chrome_fg="#f8f8f2",
            chrome_active_bg="#44475a",
            status_bg="#191a21",
            status_fg="#b0b2c8",
            status_border="#2e3040",
            accent="#bd93f9",
            success="#50fa7b",
            warning="#f1fa8c",
            error="#ff5555",
            menu_bg="#343746",
            menu_fg="#f8f8f2",
            menu_active_bg="#6272a4",
            menu_active_fg="#f8f8f2",
            muted_fg="#6272a4",
        ),
        Theme(
            name="Gruvbox",
            dark=True,
            editor_bg="#282828",
            editor_fg="#ebdbb2",
            selection_bg="#504945",
            cursor="#ebdbb2",
            window_bg="#1d2021",
            chrome_bg="#3c3836",
            chrome_fg="#ebdbb2",
            chrome_active_bg="#504945",
            status_bg="#171918",
            status_fg="#a89984",
            status_border="#32302f",
            accent="#83a598",
            success="#b8bb26",
            warning="#fabd2f",
            error="#fb4934",
            menu_bg="#3c3836",
            menu_fg="#ebdbb2",
            menu_active_bg="#504945",
            menu_active_fg="#fbf1c7",
            muted_fg="#928374",
        ),
    )
}

# Nice order for the View → Theme submenu (Dark first = the default).
THEME_ORDER = ["Dark", "Light", "Nord", "Dracula", "Gruvbox"]


# --------------------------------------------------------------------------
# Settings file (~/.config/textify/config.json)
# --------------------------------------------------------------------------
def config_dir() -> Path:
    override = os.environ.get("TEXTIFY_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "textify"


def config_file() -> Path:
    return config_dir() / "config.json"


def load_settings() -> dict:
    """Return the saved settings, or an empty dict if there are none."""
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict) -> None:
    """Persist settings; failures are ignored (a read-only config dir must
    never break the app)."""
    try:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        config_file().write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def saved_theme() -> str:
    """The theme to start with (falls back to the default)."""
    name = load_settings().get("theme")
    return name if name in THEMES else DEFAULT_THEME


def save_theme(name: str) -> None:
    settings = load_settings()
    settings["theme"] = name
    save_settings(settings)


def saved_speed(default: int) -> int:
    """The typing speed (characters per second) to start with.

    ``0`` means "instant": the whole text appears at once, with no animation.
    Anything unreadable in the settings file falls back to *default*.
    """
    try:
        value = int(load_settings().get("typing_speed", default))
    except (TypeError, ValueError):
        return default
    return max(0, value)


def save_speed(cps: int) -> None:
    settings = load_settings()
    settings["typing_speed"] = max(0, int(cps))
    save_settings(settings)
