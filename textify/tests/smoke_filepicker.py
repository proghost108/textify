"""
One-shot smoke test for textify/filepicker.py (not part of the main suite).

Builds a little directory tree, then drives the themed file dialog directly
with the window withdrawn (nothing appears on screen).

Requires working Tk/Tcl libraries — see the notes in tests/smoke_gui.py.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import tkinter as tk  # noqa: E402

from filepicker import FileDialog  # noqa: E402
from themes import THEMES  # noqa: E402


def make_dialog(root: tk.Tk, theme: str, **kwargs) -> FileDialog:
    dialog = FileDialog(root, THEMES[theme], **kwargs)
    dialog.withdraw()                     # stay invisible on the user's screen
    dialog.update_idletasks()
    return dialog


def listed(dialog: FileDialog) -> list[str]:
    return list(dialog.listbox.get(0, "end"))


def main() -> int:
    checks: list[str] = []
    root = tk.Tk()
    root.withdraw()

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        (base / "docs").mkdir()
        (base / "docs" / "inner.txt").write_text("inner", encoding="utf-8")
        (base / "zeta.txt").write_text("z", encoding="utf-8")
        (base / "Alpha.txt").write_text("a", encoding="utf-8")
        (base / ".hidden.txt").write_text("h", encoding="utf-8")
        (base / ".secret").mkdir()

        # --- listing: folders first, sorted, dotfiles hidden, themed -----
        dialog = make_dialog(root, "Nord", mode="open", initialdir=base)
        assert listed(dialog) == ["docs/", "Alpha.txt", "zeta.txt"], listed(dialog)
        assert dialog.listbox.cget("background") == THEMES["Nord"].editor_bg
        assert dialog.cget("background") == THEMES["Nord"].window_bg
        assert dialog.path_var.get() == str(base)
        checks.append("listing: folders first, sorted, dotfiles hidden, themed")

        # --- the eye symbol toggles dotfiles -----------------------------
        dialog._toggle_hidden()
        names = listed(dialog)
        assert ".hidden.txt" in names and ".secret/" in names, names
        dialog._toggle_hidden()
        assert ".hidden.txt" not in listed(dialog)
        checks.append("eye symbol toggles hidden (dot) files on and off")

        # --- navigation: into a folder, up, home -------------------------
        dialog._navigate(base / "docs")
        assert listed(dialog) == ["inner.txt"], listed(dialog)
        dialog._go_up()
        assert dialog.current_dir == base
        dialog._go_home()
        assert dialog.current_dir == Path.home()
        checks.append("navigation: into a folder, up, home")

        # --- typing a folder in the path box -----------------------------
        dialog.path_var.set(str(base / "docs"))
        dialog._navigate_typed()
        assert dialog.current_dir == base / "docs"
        checks.append("typing a folder in the path box navigates")

        # --- open mode: choosing an existing file ------------------------
        dialog._navigate(base)
        dialog.filename_var.set("Alpha.txt")
        dialog._accept()
        assert dialog.result == base / "Alpha.txt", dialog.result
        checks.append("open mode accepts an existing file")

        # --- open mode: a missing file is refused ------------------------
        dialog = make_dialog(root, "Light", mode="open", initialdir=base)
        assert dialog.listbox.cget("background") == THEMES["Light"].editor_bg
        dialog.filename_var.set("nope.txt")
        dialog._accept()
        assert dialog.result is None
        assert "No such file" in dialog.message_label.cget("text")
        dialog.destroy()
        checks.append("open mode refuses a missing file (Light theme applied)")

        # --- save mode: appends .txt, no existence required --------------
        dialog = make_dialog(
            root, "Dracula", mode="save", initialdir=base, initialfile="notes"
        )
        assert dialog.accept_button.cget("text") == "Save"
        dialog._accept()
        assert dialog.result == base / "notes.txt", dialog.result
        checks.append("save mode appends .txt and returns the path")

        # --- cancel returns nothing --------------------------------------
        dialog = make_dialog(root, "Dark", mode="open", initialdir=base)
        dialog._cancel()
        assert dialog.result is None
        checks.append("cancel returns no path")

    root.destroy()
    for line in checks:
        print(f"PASS  {line}")
    print("\nFile picker smoke test OK — all flows work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
