"""
Tests for textify/themes.py.

Run either way:

    python tests/test_themes.py
    pytest tests/ -q
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import themes  # noqa: E402
from themes import DEFAULT_THEME, THEMES, THEME_ORDER  # noqa: E402

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

_COLOR_FIELDS = (
    "editor_bg", "editor_fg", "selection_bg", "cursor",
    "window_bg", "chrome_bg", "chrome_fg", "chrome_active_bg",
    "status_bg", "status_fg", "status_border",
    "accent", "success", "warning", "error",
    "menu_bg", "menu_fg", "menu_active_bg", "menu_active_fg",
    "muted_fg",
)


def _use_temp_config(tmp_dir: Path) -> None:
    """Point the settings file at a throwaway directory."""
    os.environ["TEXTIFY_CONFIG_DIR"] = str(tmp_dir)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_theme_catalogue() -> None:
    assert set(THEMES) == set(THEME_ORDER), "THEMES and THEME_ORDER disagree"
    assert DEFAULT_THEME in THEMES
    for name, theme in THEMES.items():
        assert theme.name == name
        assert isinstance(theme.dark, bool)
        for field in _COLOR_FIELDS:
            value = getattr(theme, field)
            assert _HEX_COLOR.match(value), f"{name}.{field} = {value!r} is not #rrggbb"


def test_theme_set() -> None:
    assert THEME_ORDER == ["Dark", "Light", "Nord", "Dracula", "Gruvbox"]


def test_settings_round_trip(tmp_dir: Path) -> None:
    _use_temp_config(tmp_dir)

    assert themes.load_settings() == {}              # nothing saved yet
    assert themes.saved_theme() == DEFAULT_THEME

    themes.save_theme("Dracula")
    assert themes.saved_theme() == "Dracula"
    saved = json.loads(themes.config_file().read_text(encoding="utf-8"))
    assert saved["theme"] == "Dracula"


def test_settings_survive_corruption(tmp_dir: Path) -> None:
    _use_temp_config(tmp_dir)

    themes.config_file().parent.mkdir(parents=True, exist_ok=True)
    themes.config_file().write_text("{ this is not json", encoding="utf-8")
    assert themes.load_settings() == {}
    assert themes.saved_theme() == DEFAULT_THEME

    themes.config_file().write_text('{"theme": "NotATheme"}', encoding="utf-8")
    assert themes.saved_theme() == DEFAULT_THEME

    themes.config_file().write_text('["not", "a", "dict"]', encoding="utf-8")
    assert themes.load_settings() == {}


def test_save_theme_keeps_other_settings(tmp_dir: Path) -> None:
    _use_temp_config(tmp_dir)

    themes.save_settings({"theme": "Light", "other": 42})
    themes.save_theme("Nord")
    settings = themes.load_settings()
    assert settings == {"theme": "Nord", "other": 42}


def test_config_dir_override(tmp_dir: Path) -> None:
    _use_temp_config(tmp_dir)
    assert themes.config_dir() == tmp_dir

    os.environ.pop("TEXTIFY_CONFIG_DIR", None)
    assert themes.config_dir() == Path.home() / ".config" / "textify"


# --------------------------------------------------------------------------
# standalone runner (same pattern as test_converter.py)
# --------------------------------------------------------------------------
def main() -> int:
    import inspect

    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_")
    ]
    failures = 0
    for test in tests:
        takes_argument = bool(inspect.signature(test).parameters)
        with tempfile.TemporaryDirectory(prefix="textify-themes-") as raw_dir:
            try:
                test(Path(raw_dir)) if takes_argument else test()
                print(f"PASS  {test.__name__}")
            except AssertionError as error:
                failures += 1
                print(f"FAIL  {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
