"""
One-shot GUI smoke test for textify (not part of the main suite).

Runs the real Tkinter app with the window withdrawn (nothing appears on
screen), exercises themes, converts files through the actual worker-thread
pipeline, and checks what gets rendered.

Requires working Tk/Tcl libraries.  On a system without the tk package
installed you can borrow them from the Arch packages without installing:

    mkdir -p /tmp/tk && cd /tmp/tk
    # extract the tk and tcl packages' usr/ trees here, then:
    LD_LIBRARY_PATH=/tmp/tk/usr/lib \\
    TCL_LIBRARY=/tmp/tk/usr/lib/tcl8.6 \\
    TK_LIBRARY=/tmp/tk/usr/lib/tk8.6 \\
    python tests/smoke_gui.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Keep the test away from the real ~/.config/textify — must be set before the
# app is constructed, because it reads the saved theme on startup.
_CONFIG_DIR = tempfile.mkdtemp(prefix="textify-smoke-config-")
os.environ["TEXTIFY_CONFIG_DIR"] = _CONFIG_DIR

from gui import TextifyApp  # noqa: E402
from themes import DEFAULT_THEME, THEMES, saved_speed, saved_theme  # noqa: E402
from tests.test_converter import build_samples  # noqa: E402


def wait_for_result(app: TextifyApp, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while app.result is None:
        assert time.time() < deadline, "conversion never finished"
        app.update()
        time.sleep(0.02)


def main() -> int:
    checks: list[str] = []

    # --- themes: a fresh config starts on the default -------------------
    app = TextifyApp()
    app.withdraw()                       # stay invisible on the user's screen
    assert app.theme.name == DEFAULT_THEME, app.theme.name
    assert app.text.cget("background") == THEMES[DEFAULT_THEME].editor_bg
    checks.append("fresh start uses the default theme")

    # --- themes: switching recolours the widgets and is remembered ------
    app.apply_theme("Nord")
    assert app.text.cget("background") == THEMES["Nord"].editor_bg
    assert app.status_bar.cget("background") == THEMES["Nord"].status_bg
    assert saved_theme() == "Nord", "theme was not persisted"
    checks.append("theme switch recolours widgets and persists")

    # --- status bar: hairline rule + state dot follow the theme ----------
    assert app.status_rule.cget("background") == THEMES["Nord"].status_border
    assert app.status_row.cget("background") == THEMES["Nord"].status_bg
    assert app.status_left.cget("foreground") == THEMES["Nord"].status_fg
    assert app.status_right.cget("foreground") == THEMES["Nord"].muted_fg
    idle_dot = app.status_dot.itemcget(app._dot, "fill")
    assert idle_dot == THEMES["Nord"].muted_fg, idle_dot

    app._set_status("working…", "busy")
    assert app.status_dot.itemcget(app._dot, "fill") == THEMES["Nord"].accent
    app._set_status("nope", "error")
    assert app.status_dot.itemcget(app._dot, "fill") == THEMES["Nord"].error
    app._set_status("ok", "done")
    assert app.status_dot.itemcget(app._dot, "fill") == THEMES["Nord"].success
    checks.append("status bar: rule, muted text and state dot are themed")

    # --- minimal chrome: no scrollbars, no title bar --------------------
    body_children = app.body.winfo_children()
    assert body_children == [app.text], f"unexpected widgets in the editor: {body_children}"
    assert not hasattr(app, "y_scroll") and not hasattr(app, "x_scroll")
    assert app.frameless is True, "window should be frameless by default"
    # The title bar is dropped with _MOTIF_WM_HINTS on the window the manager
    # manages, so the window stays managed: no override-redirect.
    assert not app.overrideredirect(), "frameless window must stay managed"
    assert app.bind("<Map>"), "the no-title-bar hint is never delivered"
    checks.append("minimal chrome: no scrollbars, managed frameless window")

    # --- the hand-built menu bar doubles as the drag handle -------------
    assert [b.cget("text") for b in app._menu_buttons] == ["File", "View", "Help"]
    assert app.menubar.cget("background") == THEMES["Nord"].menu_bg
    assert app._menu_buttons[0].cget("background") == THEMES["Nord"].menu_bg
    assert app.menubar.bind("<B1-Motion>"), "menu bar is not draggable"
    checks.append("custom menu bar: themed tabs + drag-to-move")

    # --- every tab can actually post its menu ---------------------------
    # Tk's tk::MbPost refuses to post a menu that is not a descendant of its
    # button (error code POST_NONCHILD), which leaves the tabs silently dead.
    for button in app._menu_buttons:
        menu = button.cget("menu")
        assert str(menu).startswith(str(button)), (
            f"{button.cget('text')}: menu {menu} is not a descendant of {button}"
        )
        try:
            app.tk.call("tk::MbPost", button._w, 260, 200)
        except tk.TclError as error:
            raise AssertionError(
                f"{button.cget('text')} tab cannot post its menu: {error}"
            )
        app.tk.call("tk::MenuUnpost", "")
    checks.append("menu tabs: every tab posts its menu")

    # --- View → Typing speed is wired up and themed ---------------------
    assert str(app.speed_menu).startswith(str(app.view_menu)), "speed cascade"
    assert str(app.speed_menu.cget("background")) == THEMES["Nord"].menu_bg
    assert app.speed_var.get() == 45, app.speed_var.get()
    labels = [
        app.speed_menu.entrycget(i, "label")
        for i in range(app.speed_menu.index("end") + 1)
        if app.speed_menu.type(i) == "radiobutton"
    ]
    assert labels == ["Instant", "Very fast", "Fast", "Normal", "Slow", "Very slow"]
    assert app.speed_menu.index("end") == len(labels) + 1   # + separator + Custom…
    checks.append("View → Typing speed: presets + Custom…")

    # --- the bottom-right grip resizes the frameless window -------------
    assert app.resize_grip.cget("cursor") == "bottom_right_corner"
    assert app.resize_grip.bind("<B1-Motion>"), "resize grip is not bound"
    assert app.resize_grip.find_all(), "resize grip is not drawn"
    assert app._min_size == (720, 480)
    checks.append("resize grip: drawn in the status bar and bound")

    # --- themes: the saved theme is restored on the next launch ---------
    app.destroy()
    app = TextifyApp()
    app.withdraw()
    assert app.theme.name == "Nord", app.theme.name
    checks.append("saved theme restored on the next launch")

    # --- content flows ---------------------------------------------------
    with tempfile.TemporaryDirectory() as raw:
        samples = build_samples(Path(raw))

        assert (
            app.file_menu.entrycget("Save as .txt…", "state") == "disabled"
        ), "save should start disabled"

        app.load_file(samples["docx"])
        wait_for_result(app)
        # The conversion is processed but held back: nothing is shown yet.
        ready = app.text.get("1.0", "end")
        assert "Hello from Textify." not in ready, "text shown before Enter"
        assert "press Enter" in app.status_var.get(), "no Enter hint in the status"
        assert (
            app.file_menu.entrycget("Save as .txt…", "state") == "normal"
        ), "save not enabled after conversion"
        assert "Word text extraction" in app.status_var.get(), "bad status"
        checks.append("conversion is processed but not shown until Enter")

        # Enter types it out gradually; a second Enter finishes it at once.
        assert app.text.bind("<Return>"), "Enter is not bound"
        app.speed_var.set(60)
        app._start_reveal()
        deadline = time.time() + 0.25
        while time.time() < deadline:
            app.update()
            time.sleep(0.01)
        partial = app.text.get("1.0", "end")
        assert 0 < len(partial) < len(app._reveal_body), "nothing was typed out"
        app._start_reveal()                     # Enter again = skip to the end
        rendered = app.text.get("1.0", "end")
        assert rendered == app._reveal_body + "\n", "the reveal did not finish"
        assert "Hello from Textify." in rendered, "docx text missing"
        assert rendered.startswith("1 | Hello from Textify."), "no line numbers"
        checks.append("Enter types the text out; Enter again finishes it")

        # Instant speed skips the animation entirely.
        app.result = None
        app.load_file(samples["docx"])
        wait_for_result(app)
        app.speed_var.set(0)
        app._start_reveal()
        assert app.text.get("1.0", "end").startswith("1 | Hello from Textify.")
        checks.append("Instant speed reveals the whole text at once")

        # The speed itself is remembered between launches.
        app.speed_var.set(120)
        app._speed_chosen()
        assert saved_speed(0) == 120, "typing speed was not persisted"
        checks.append("typing speed is remembered")

        app.numbers_var.set(False)
        app._render()
        assert app.text.get("1.0", "end").startswith("Hello from Textify.")
        app.numbers_var.set(True)
        app._render()
        checks.append("line-number toggle")

        app.result = None                # wait for the *new* conversion
        app.load_file(samples["binary"])
        wait_for_result(app)
        app.speed_var.set(0)
        app._start_reveal()
        assert "Hex dump" in app.text.get("1.0", "end"), "hex dump missing"
        checks.append("binary → hex dump rendering")

        # a theme change must not disturb already-converted content
        app.apply_theme("Gruvbox")
        assert "Hex dump" in app.text.get("1.0", "end"), "content lost on theme change"
        checks.append("theme change keeps the converted text")

    app.destroy()
    shutil.rmtree(_CONFIG_DIR, ignore_errors=True)

    for line in checks:
        print(f"PASS  {line}")
    print("\nGUI smoke test OK — all flows work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
