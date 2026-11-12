"""
textify/filepicker.py — a themed file dialog.

Tk's built-in file dialog ignores the app's colours, so Textify brings its own:
a small Toplevel with a folder list, an editable path box, Up/Home buttons and
a little eye symbol that toggles hidden (dot) files on and off.

Use it through the two helpers at the bottom:

    path = open_file(parent, theme, initialdir=...)   # Path or None
    path = save_file(parent, theme, initialfile=...)  # Path or None
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from themes import Theme


class FileDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        *,
        mode: str = "open",
        title: str = "Choose a file",
        initialdir: str | Path | None = None,
        initialfile: str = "",
    ) -> None:
        super().__init__(parent)
        self.parent = parent
        self.theme = theme
        self.mode = mode
        self.result: Path | None = None
        self.show_hidden = tk.BooleanVar(value=False)
        self.current_dir = self._as_directory(initialdir)
        self._rows: list[tuple[str, Path, bool]] = []   # (shown name, path, is_dir)

        self.title(title)
        self.geometry("680x480")
        self.minsize(520, 360)
        self.transient(parent)

        self._build_widgets()
        self._apply_theme()
        self._refresh_listing()
        if initialfile:
            self.filename_var.set(initialfile)
            self._select_name(initialfile)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel())
        self._center_on_parent()

        # A grab makes the dialog modal; some window managers are not ready
        # for it the instant the window is created, hence the retry.
        self.update_idletasks()
        self._grab()

        self.filename_entry.focus_set()
        self.filename_entry.selection_range(0, "end")

    # ------------------------------------------------------------- layout
    def _build_widgets(self) -> None:
        # top row: Up / Home / path / hidden-files eye
        self.top = tk.Frame(self)
        self.top.pack(side="top", fill="x", padx=10, pady=(10, 6))

        self.up_button = tk.Button(self.top, text="Up", command=self._go_up)
        self.up_button.pack(side="left")
        self.home_button = tk.Button(self.top, text="Home", command=self._go_home)
        self.home_button.pack(side="left", padx=(6, 6))

        self.path_var = tk.StringVar()
        self.path_entry = tk.Entry(self.top, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<Return>", self._navigate_typed)

        self.eye = tk.Canvas(
            self.top, width=30, height=22, highlightthickness=0, cursor="hand2"
        )
        self.eye.pack(side="left", padx=(8, 0))
        self.eye.bind("<Button-1>", lambda _event: self._toggle_hidden())

        # bottom rows (packed before the list so they keep their space)
        self.button_row = tk.Frame(self)
        self.button_row.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

        self.message_label = tk.Label(self.button_row, anchor="w")
        self.message_label.pack(side="left", fill="x", expand=True)

        self.cancel_button = tk.Button(
            self.button_row, text="Cancel", command=self._cancel
        )
        self.cancel_button.pack(side="right")
        self.accept_button = tk.Button(
            self.button_row,
            text="Open" if self.mode == "open" else "Save",
            command=self._accept,
        )
        self.accept_button.pack(side="right", padx=(0, 8))

        self.name_row = tk.Frame(self)
        self.name_row.pack(side="bottom", fill="x", padx=10, pady=(0, 4))

        self.name_label = tk.Label(self.name_row, text="File name:")
        self.name_label.pack(side="left")
        self.filename_var = tk.StringVar()
        self.filename_entry = tk.Entry(self.name_row, textvariable=self.filename_var)
        self.filename_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.filename_entry.bind("<Return>", lambda _event: self._accept())

        # middle: the folder contents
        self.middle = tk.Frame(self)
        self.middle.pack(side="top", fill="both", expand=True, padx=10)

        self.listbox = tk.Listbox(
            self.middle,
            selectmode="browse",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
        )
        self.scrollbar = ttk.Scrollbar(
            self.middle, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=self.scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.listbox.bind("<Double-Button-1>", self._activate)
        self.listbox.bind("<Return>", self._activate)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

    # -------------------------------------------------------------- theme
    def _apply_theme(self) -> None:
        t = self.theme
        for frame in (self, self.top, self.middle, self.name_row, self.button_row):
            frame.configure(background=t.window_bg)

        for label in (self.name_label, self.message_label):
            label.configure(background=t.window_bg, foreground=t.chrome_fg)

        for button in (
            self.up_button,
            self.home_button,
            self.cancel_button,
            self.accept_button,
        ):
            button.configure(
                background=t.chrome_bg,
                foreground=t.chrome_fg,
                activebackground=t.chrome_active_bg,
                activeforeground=t.chrome_fg,
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                padx=12,
                pady=4,
                cursor="hand2",
            )

        for entry in (self.path_entry, self.filename_entry):
            entry.configure(
                background=t.chrome_bg,
                foreground=t.chrome_fg,
                insertbackground=t.chrome_fg,
                relief="flat",
                highlightthickness=1,
                highlightbackground=t.chrome_active_bg,
                highlightcolor=t.chrome_fg,
                selectbackground=t.selection_bg,
                selectforeground=t.chrome_fg,
            )

        self.listbox.configure(
            background=t.editor_bg,
            foreground=t.editor_fg,
            selectbackground=t.selection_bg,
            selectforeground=t.editor_fg,
            borderwidth=0,
            highlightthickness=0,
        )

        self.eye.configure(background=t.window_bg)
        self._draw_eye()

    def _draw_eye(self) -> None:
        """The hidden-files toggle: an open eye, or a slashed one when off."""
        t = self.theme
        self.eye.delete("all")
        outline = t.chrome_fg
        self.eye.create_polygon(
            3, 11, 8, 5, 15, 3, 22, 5, 27, 11, 22, 17, 15, 19, 8, 17,
            smooth=True, outline=outline, fill="", width=1,
        )
        self.eye.create_oval(12, 7, 18, 13, outline=outline, fill=outline)
        if not self.show_hidden.get():
            # punch the background through first, then draw the slash
            self.eye.create_line(4, 19, 26, 3, fill=t.window_bg, width=3)
            self.eye.create_line(4, 19, 26, 3, fill=outline, width=1)

    def _toggle_hidden(self) -> None:
        self.show_hidden.set(not self.show_hidden.get())
        self._draw_eye()
        self._refresh_listing()

    # ----------------------------------------------------------- listing
    def _refresh_listing(self) -> None:
        self.listbox.delete(0, "end")
        self._rows = []
        try:
            entries = list(self.current_dir.iterdir())
        except OSError as error:
            self._show_message(f"Cannot read this folder ({error})", error=True)
            entries = []

        folders: list[str] = []
        files: list[str] = []
        for entry in entries:
            name = entry.name
            if not self.show_hidden.get() and name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            (folders if is_dir else files).append(name)

        folders.sort(key=str.lower)
        files.sort(key=str.lower)
        self._rows = [
            (name + "/", self.current_dir / name, True) for name in folders
        ] + [(name, self.current_dir / name, False) for name in files]

        for shown, _path, _is_dir in self._rows:
            self.listbox.insert("end", shown)

        self.path_var.set(str(self.current_dir))
        if self._rows:
            self.listbox.yview_moveto(0)
        self._show_message(
            f"{len(folders)} folders, {len(files)} files"
            + ("" if self.show_hidden.get() else " (hidden files off)")
        )

    def _show_message(self, text: str, error: bool = False) -> None:
        if error:
            colour = "#ff6b6b" if self.theme.dark else "#c0392b"
        else:
            colour = self.theme.muted_fg
        self.message_label.configure(text=text, foreground=colour)

    def _select_name(self, name: str) -> None:
        for index, (_shown, path, _is_dir) in enumerate(self._rows):
            if path.name == name:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(index)
                self.listbox.activate(index)
                self.listbox.see(index)
                return

    # ---------------------------------------------------------- selection
    def _on_select(self, _event: "tk.Event | None" = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        _shown, path, is_dir = self._rows[selection[0]]
        if not is_dir:
            self.filename_var.set(path.name)
        self._show_message(str(path) + ("/" if is_dir else ""))

    def _activate(self, _event: "tk.Event | None" = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        _shown, path, is_dir = self._rows[selection[0]]
        if is_dir:
            self._navigate(path)
        else:
            self.filename_var.set(path.name)
            self._accept()

    # -------------------------------------------------------- navigation
    def _navigate(self, path: Path) -> None:
        if not path.is_dir():
            self._show_message(f"Not a folder: {path}", error=True)
            return
        self.current_dir = path
        self._refresh_listing()

    def _navigate_typed(self, _event: "tk.Event | None" = None) -> None:
        raw = self.path_var.get().strip()
        if not raw:
            return
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            self._navigate(candidate)
        elif candidate.is_file():
            self._navigate(candidate.parent)
            self.filename_var.set(candidate.name)
            self._select_name(candidate.name)
        else:
            self._show_message(f"No such folder or file: {raw}", error=True)

    def _go_up(self) -> None:
        parent = self.current_dir.parent
        if parent != self.current_dir:
            self._navigate(parent)

    def _go_home(self) -> None:
        self._navigate(Path.home())

    # -------------------------------------------------------------- accept
    def _accept(self) -> None:
        raw = self.filename_var.get().strip()
        if not raw:
            self._show_message("Type a file name first.", error=True)
            return

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.current_dir / candidate

        if candidate.is_dir():
            self._navigate(candidate)
            return

        if self.mode == "open":
            if not candidate.is_file():
                self._show_message(f"No such file: {candidate.name}", error=True)
                return
        else:
            if not candidate.suffix:
                candidate = candidate.with_name(candidate.name + ".txt")
            if candidate.exists():
                if not messagebox.askyesno(
                    "Overwrite?",
                    f"{candidate.name} already exists.\nReplace it?",
                    parent=self,
                ):
                    return

        self.result = candidate
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    # -------------------------------------------------------------- misc
    def _grab(self, attempts: int = 5) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
        except tk.TclError:
            # the window manager may not be ready yet — retry a few times
            if attempts > 0:
                try:
                    self.after(60, lambda: self._grab(attempts - 1))
                except tk.TclError:
                    pass

    def _center_on_parent(self) -> None:
        self.update_idletasks()
        try:
            x = self.parent.winfo_rootx() + (self.parent.winfo_width() - self.winfo_width()) // 2
            y = self.parent.winfo_rooty() + (self.parent.winfo_height() - self.winfo_height()) // 2
        except tk.TclError:
            return
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    @staticmethod
    def _as_directory(initialdir: str | Path | None) -> Path:
        if initialdir:
            candidate = Path(initialdir).expanduser()
            if candidate.is_dir():
                return candidate
        return Path.home()


# --------------------------------------------------------------------------
# helpers used by gui.py
# --------------------------------------------------------------------------
def open_file(
    parent: tk.Misc,
    theme: Theme,
    *,
    initialdir: str | Path | None = None,
    title: str = "Open a file",
) -> Path | None:
    dialog = FileDialog(parent, theme, mode="open", title=title, initialdir=initialdir)
    parent.wait_window(dialog)
    return dialog.result


def save_file(
    parent: tk.Misc,
    theme: Theme,
    *,
    initialdir: str | Path | None = None,
    initialfile: str = "",
    title: str = "Save as",
) -> Path | None:
    dialog = FileDialog(
        parent,
        theme,
        mode="save",
        title=title,
        initialdir=initialdir,
        initialfile=initialfile,
    )
    parent.wait_window(dialog)
    return dialog.result
