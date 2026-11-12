"""
textify/gui.py — the Tkinter front-end.

A small desktop window: pick any file, watch it get converted to text, read
it line by line, save it as a .txt.  The conversion itself runs in a worker
thread (in converter.py) so the window never freezes on big files.

Reading is meant to be fun: a converted file is *held back* instead of being
dumped on screen.  Press Enter and it is typed out character by character, at
a speed you choose under View → Typing speed.

Everything else is driven from the menu bar — File → Open… (Ctrl+O),
File → Save as .txt… (Ctrl+S), View → Line numbers, View → Typing speed,
View → Theme.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.font import Font, nametofont

from converter import ConversionResult, convert_to_text, human_size
from filepicker import open_file, save_file
from themes import (
    DEFAULT_THEME,
    THEMES,
    THEME_ORDER,
    Theme,
    save_speed,
    save_theme,
    saved_speed,
    saved_theme,
)

# Beyond these limits only the first lines are shown on screen (the full text
# is always kept in memory and can be saved with File → Save as .txt…).
DISPLAY_MAX_LINES = 20_000
DISPLAY_MAX_CHARS = 2_000_000

_PLACEHOLDER = (
    "Open a file to convert it to text.\n"
    "\n"
    "(File → Open…, or Ctrl+O)\n"
    "\n"
    "Textify reads documents, spreadsheets, presentations, PDFs, HTML,\n"
    "images (OCR), archives and even raw binary files, and shows their\n"
    "contents here, line by line.\n"
    "\n"
    "Tip: change the colours under View → Theme."
)

# Shown while a converted file waits to be read (the text itself is held back
# until Enter is pressed).  It is drawn with the "hint" tag, so it stays muted.
_READY_HINT = (
    "Ready — press Enter to read it.\n"
    "\n"
    "(View → Typing speed changes how fast the text appears.)"
)

# View → Typing speed presets, in characters per second.  0 means "instant":
# the whole text appears at once, with no animation.
SPEED_PRESETS: tuple[tuple[str, int], ...] = (
    ("Instant", 0),
    ("Very fast", 200),
    ("Fast", 100),
    ("Normal", 45),
    ("Slow", 20),
    ("Very slow", 8),
)
DEFAULT_SPEED = 45
SPEED_MIN = 1
SPEED_MAX = 400
TYPE_TICK_MS = 16                     # the reveal timer runs at ~60 frames/s


# One long-lived X11 connection, opened on first use (Linux/X11 only).
_X11: "list | None" = None
_X11_FAILED = False


def _x11() -> "list | None":
    """Open one long-lived X11 connection, or return None (Linux only)."""
    global _X11, _X11_FAILED
    if _X11 is not None or _X11_FAILED:
        return _X11
    if not sys.platform.startswith("linux"):
        _X11_FAILED = True
        return None
    try:
        lib = ctypes.CDLL("libX11.so.6")
    except OSError:
        _X11_FAILED = True
        return None
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XChangeProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ]
    lib.XQueryTree.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    lib.XSetErrorHandler.restype = ctypes.c_void_p
    lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    display = lib.XOpenDisplay(None)
    if not display:
        _X11_FAILED = True
        return None
    _X11 = [lib, display]
    return _X11


def _swallow_x_error(_display, _event) -> int:
    """Xlib's default error handler prints and calls exit(1); don't."""
    return 0


# Kept at module level so the callback is never garbage-collected.
_X_ERROR_HANDLER = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
)(_swallow_x_error)


def _parent_window(window_id: int) -> int:
    """The X window that contains *window_id* (0 when it cannot be read).

    Tk wraps every toplevel: ``winfo_id()`` is the window the widgets live in,
    while the window the window manager actually manages — and reads its
    window-manager hints from — is its parent.
    """
    x11 = _x11()
    if x11 is None or not window_id:
        return 0
    lib, display = x11
    root, parent = ctypes.c_ulong(), ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    count = ctypes.c_uint()
    # Our calls touch windows by raw id, so any stale id would otherwise abort
    # the whole process: borrow Xlib's error handler for the duration.
    previous = lib.XSetErrorHandler(_X_ERROR_HANDLER)
    try:
        lib.XQueryTree(
            ctypes.c_void_p(display), ctypes.c_ulong(window_id),
            ctypes.byref(root), ctypes.byref(parent),
            ctypes.byref(children), ctypes.byref(count),
        )
        lib.XSync(ctypes.c_void_p(display), 0)
    finally:
        lib.XSetErrorHandler(previous)
    return parent.value


def _set_motif_hint(window_id: int) -> None:
    """Ask the window manager to draw no decorations for *window_id*."""
    x11 = _x11()
    if x11 is None or not window_id:
        return
    lib, display = x11
    atom = lib.XInternAtom(ctypes.c_void_p(display), b"_MOTIF_WM_HINTS", False)
    # flags = MWM_HINTS_DECORATIONS (2), decorations = 0 (none)
    hints = (ctypes.c_ulong * 5)(2, 0, 0, 0, 0)
    lib.XChangeProperty(
        ctypes.c_void_p(display), ctypes.c_ulong(window_id),
        ctypes.c_ulong(atom), ctypes.c_ulong(atom), 32, 0,
        ctypes.cast(hints, ctypes.c_void_p), 5,
    )
    lib.XFlush(ctypes.c_void_p(display))


def hide_title_bar(window: tk.Misc) -> bool:
    """Drop the OS title bar while keeping the window *managed*.

    Uses ``_MOTIF_WM_HINTS``, which KWin, mutter, Xfwm and Openbox all honour
    for X11 clients.  Unlike ``overrideredirect`` this leaves the window
    managed, so it keeps its taskbar entry, Alt+Tab, focus and the ability to
    stack the app's own dialogs on top of it.  Best-effort: returns True when
    the hint was delivered (harmless, and False, anywhere else).
    """
    if _x11() is None:
        return False
    try:
        inner = window.winfo_id()
    except tk.TclError:
        return False
    outer = _parent_window(inner)
    # Managers read the hint from the window they manage (the parent of
    # winfo_id()); set it on both so ones that look at the client work too.
    _set_motif_hint(inner)
    _set_motif_hint(outer)
    return bool(outer)


class SpeedDialog(tk.Toplevel):
    """A small themed slider for an exact typing speed (characters/second).

    Used by View → Typing speed → Custom….  ``result`` is the chosen rate, or
    None when the dialog was cancelled.
    """

    def __init__(self, parent: tk.Misc, theme: Theme, value: int) -> None:
        super().__init__(parent)
        self.theme = theme
        self.result: "int | None" = None

        self.title("Typing speed")
        self.resizable(False, False)
        self.transient(parent)

        self.speed_var = tk.IntVar(
            value=max(SPEED_MIN, min(SPEED_MAX, int(value or DEFAULT_SPEED)))
        )
        self._build_widgets()
        self._apply_theme()

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Return>", lambda _event: self._accept())
        self._center_on_parent()

        # A grab makes the dialog modal; some window managers are not ready for
        # it the instant the window is created, hence the retry.
        self.update_idletasks()
        self._grab()
        self.scale.focus_set()

    # ------------------------------------------------------------- layout
    def _build_widgets(self) -> None:
        self.body = tk.Frame(self, borderwidth=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=18, pady=16)

        self.caption = tk.Label(
            self.body, text="Characters per second", anchor="w"
        )
        self.caption.pack(fill="x")

        self.scale = tk.Scale(
            self.body,
            from_=SPEED_MIN,
            to=SPEED_MAX,
            orient="horizontal",
            variable=self.speed_var,
            length=320,
            showvalue=True,
            font=nametofont("TkDefaultFont"),
            sliderlength=20,
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            sliderrelief="flat",
        )
        self.scale.pack(fill="x", pady=(4, 2))

        self.hint = tk.Label(
            self.body,
            text="How fast the text appears after you press Enter.",
            anchor="w",
        )
        self.hint.pack(fill="x")

        self.buttons = tk.Frame(self.body, borderwidth=0, highlightthickness=0)
        self.buttons.pack(fill="x", pady=(16, 0))
        self.cancel_button = tk.Button(
            self.buttons, text="Cancel", command=self._cancel
        )
        self.cancel_button.pack(side="right")
        self.ok_button = tk.Button(
            self.buttons, text="Use this speed", command=self._accept
        )
        self.ok_button.pack(side="right", padx=(0, 8))

    def _apply_theme(self) -> None:
        t = self.theme
        self.configure(background=t.window_bg)
        for frame in (self.body, self.buttons):
            frame.configure(background=t.window_bg)
        self.caption.configure(background=t.window_bg, foreground=t.chrome_fg)
        self.hint.configure(background=t.window_bg, foreground=t.muted_fg)
        self.scale.configure(
            background=t.window_bg,
            foreground=t.chrome_fg,
            troughcolor=t.chrome_bg,
            activebackground=t.accent,
            highlightbackground=t.window_bg,
        )
        for button in (self.cancel_button, self.ok_button):
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

    # ------------------------------------------------------------ actions
    def _accept(self) -> None:
        self.result = max(SPEED_MIN, int(self.speed_var.get()))
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _grab(self, attempts: int = 5) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
        except tk.TclError:
            if attempts > 0:
                try:
                    self.after(60, lambda: self._grab(attempts - 1))
                except tk.TclError:
                    pass

    def _center_on_parent(self) -> None:
        self.update_idletasks()
        try:
            parent = self.master
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        except tk.TclError:
            return
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class TextifyApp(tk.Tk):
    SAVE_LABEL = "Save as .txt…"

    def __init__(self) -> None:
        super().__init__()
        self.title("Textify — any file → text")
        self.geometry("960x640")
        self.minsize(720, 480)

        self.result: ConversionResult | None = None
        self._worker: threading.Thread | None = None
        # Finished conversions are handed to the Tk thread through this queue
        # — tkinter widgets must never be touched from a worker thread.
        self._events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._drain_id: str | None = None
        # every menu, so apply_theme() can recolour them
        self._menus: list[tk.Menu] = []
        # window-drag / resize bookkeeping (the window is frameless)
        self._move_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None
        self._min_size = (720, 480)

        self.numbers_var = tk.BooleanVar(value=True)
        self.theme_var = tk.StringVar(value=saved_theme())
        self.speed_var = tk.IntVar(value=saved_speed(DEFAULT_SPEED))

        # The Enter-to-read reveal.  A converted file is *not* shown straight
        # away: it waits in the "ready" state until Enter types it out.
        #   idle   — nothing converted yet
        #   ready  — converted, held back, waiting for Enter
        #   typing — being typed out character by character
        #   done   — fully shown
        self._reveal_state = "idle"
        self._reveal_body = ""            # the text being typed out
        self._reveal_pos = 0              # characters revealed so far
        self._reveal_accum = 0.0          # fractional characters, between ticks
        self._reveal_last = 0.0           # timestamp of the previous tick
        self._type_id: str | None = None  # the pending after() callback
        self._status_base = ""            # the file's status line
        self._status_base_state = "idle"  # its dot colour (done / warn)
        self._stats_full = ""             # the "N lines · size" text

        self._build_menu()
        # The status bar is packed before the editor so it reserves its strip
        # at the bottom before the text area expands into the rest.
        self._build_status_bar()
        self._build_editor()
        self.apply_theme(self.theme_var.get(), persist=False)

        # Clean, terminal-like window: no OS title bar.  The hint can only be
        # delivered once the window is mapped (the manager reads it from the
        # window it manages, which does not exist before then), so wait for
        # the <Map> event.  The window stays *managed*: moving, resizing,
        # focus, Alt+Tab and the file dialog all keep working.  Set
        # TEXTIFY_DECORATED=1 to keep a normal, decorated window instead.
        self.frameless = not os.environ.get("TEXTIFY_DECORATED")
        self.title_bar_hidden = False
        self._enable_window_drag()
        if self.frameless:
            self.bind("<Map>", lambda _event: self._on_map(), add="+")

        self.bind("<Control-o>", self.choose_file)
        self.bind("<Control-s>", self.save_text)
        self.bind("<Control-q>", lambda _event: self.destroy())

        # Enter starts the typewriter reveal (and finishes it, if it is already
        # running).  The editor is read-only, so Return has no other job there;
        # binding it on the widget as well keeps the Text class bindings from
        # swallowing it first.
        for widget in (self, self.text):
            widget.bind("<Return>", self._start_reveal)
            widget.bind("<KP_Enter>", self._start_reveal)

        self._drain_id = self.after(100, self._drain_events)
        self._render()
        self._set_status("Ready — open any file to convert it to text.")

    def destroy(self) -> None:
        """Stop the event poll before tearing the window down (a pending
        `after` callback would otherwise fire into a dead interpreter)."""
        self._cancel_typing()
        if self._drain_id is not None:
            try:
                self.after_cancel(self._drain_id)
            except tk.TclError:
                pass
            self._drain_id = None
        super().destroy()

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        # A hand-built menu bar rather than Tk's native one: the native bar
        # cannot be styled consistently across themes, and the frameless
        # window needs a strip at the top that doubles as the drag handle.
        self.menubar = tk.Frame(self, borderwidth=0, highlightthickness=0)
        self.menubar.pack(side="top", fill="x")

        self._menu_buttons: list[tk.Menubutton] = []

        def tab(label: str) -> tk.Menubutton:
            """Add a tab to the menu bar.

            Tk refuses to post a Menubutton's menu unless that menu is a
            *descendant* of the button — ``tk::MbPost`` raises POST_NONCHILD
            otherwise — so the menus below are built as children of their own
            button rather than of the bar.
            """
            button = tk.Menubutton(
                self.menubar,
                text=label,
                indicatoron=False,        # no little drop-down arrow
                direction="below",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                padx=12,
                pady=6,
                cursor="hand2",
            )
            button.pack(side="left")
            self._menu_buttons.append(button)
            return button

        file_button = tab("File")
        file_menu = tk.Menu(file_button, tearoff=0)
        file_menu.add_command(
            label="Open…", accelerator="Ctrl+O", command=self.choose_file
        )
        file_menu.add_command(
            label=self.SAVE_LABEL,
            accelerator="Ctrl+S",
            command=self.save_text,
            state="disabled",                     # enabled once a file is converted
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Exit", accelerator="Ctrl+Q", command=self.destroy
        )
        file_button.configure(menu=file_menu)
        self.file_menu = file_menu

        view_button = tab("View")
        view_menu = tk.Menu(view_button, tearoff=0)
        view_menu.add_checkbutton(
            label="Line numbers",
            variable=self.numbers_var,
            command=self._render,
        )
        view_menu.add_separator()

        theme_menu = tk.Menu(view_menu, tearoff=0)
        for name in THEME_ORDER:
            theme_menu.add_radiobutton(
                label=name,
                value=name,
                variable=self.theme_var,
                command=lambda chosen=name: self.apply_theme(chosen),
            )
        view_menu.add_cascade(label="Theme", menu=theme_menu)

        speed_menu = tk.Menu(view_menu, tearoff=0)
        for label, cps in SPEED_PRESETS:
            speed_menu.add_radiobutton(
                label=label,
                value=cps,
                variable=self.speed_var,
                command=self._speed_chosen,
            )
        speed_menu.add_separator()
        speed_menu.add_command(label="Custom…", command=self._ask_speed)
        view_menu.add_cascade(label="Typing speed", menu=speed_menu)

        view_button.configure(menu=view_menu)
        self.view_menu = view_menu
        self.theme_menu = theme_menu
        self.speed_menu = speed_menu

        help_button = tab("Help")
        help_menu = tk.Menu(help_button, tearoff=0)
        help_menu.add_command(label="About Textify", command=self._show_about)
        help_button.configure(menu=help_menu)

        self._menus = [file_menu, view_menu, theme_menu, speed_menu, help_menu]

    def _build_editor(self) -> None:
        self.body = tk.Frame(self, borderwidth=0, highlightthickness=0)
        self.body.pack(side="top", fill="both", expand=True)

        # No scrollbars — a clean, terminal-like surface.  The mouse wheel
        # scrolls vertically and Shift+wheel horizontally (both bound by Tk).
        self.text = tk.Text(
            self.body,
            font=nametofont("TkFixedFont"),
            wrap="none",
            state="disabled",          # read-only: content comes from converter.py
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=10,
        )
        self.text.pack(side="top", fill="both", expand=True)

        # Ctrl+A selects everything (not bound by default on Linux)
        self.text.bind(
            "<Control-a>",
            lambda _event: self.text.tag_add("sel", "1.0", "end") or "break",
        )

    def _build_status_bar(self) -> None:
        # A slim, quiet bar: a hairline rule, a state dot, then two lines of
        # small text.  The colour lives in the dot — not in the whole strip.
        self.status_bar = tk.Frame(self, borderwidth=0, highlightthickness=0)
        self.status_bar.pack(side="bottom", fill="x")

        self.status_rule = tk.Frame(
            self.status_bar, height=1, borderwidth=0, highlightthickness=0
        )
        self.status_rule.pack(side="top", fill="x")

        self.status_row = tk.Frame(
            self.status_bar, borderwidth=0, highlightthickness=0
        )
        self.status_row.pack(side="top", fill="x")

        self.status_var = tk.StringVar()
        self.stats_var = tk.StringVar()
        self._status_state = "idle"

        self._status_font = self._small_font()

        self.status_dot = tk.Canvas(
            self.status_row,
            width=14,
            height=14,
            borderwidth=0,
            highlightthickness=0,
            takefocus=0,
        )
        self.status_dot.pack(side="left", padx=(12, 7), pady=4)
        self._dot = self.status_dot.create_oval(
            4, 4, 10, 10, outline="", fill="#888888"
        )

        self.status_left = tk.Label(
            self.status_row,
            textvariable=self.status_var,
            anchor="w",
            font=self._status_font,
        )
        self.status_left.pack(side="left", fill="x", expand=True)

        # Bottom-right resize grip — the window has no WM frame to grab.
        # Packed first so it claims the very corner of the window.
        self.resize_grip = tk.Canvas(
            self.status_row,
            width=16,
            height=16,
            borderwidth=0,
            highlightthickness=0,
            cursor="bottom_right_corner",
        )
        self.resize_grip.pack(side="right", padx=(0, 8))

        self.status_right = tk.Label(
            self.status_row,
            textvariable=self.stats_var,
            anchor="e",
            font=self._status_font,
        )
        self.status_right.pack(side="right", padx=(10, 0))

    def _small_font(self) -> Font:
        """The default font, one point smaller — for the status bar."""
        base = nametofont("TkDefaultFont")
        font = base.copy()
        size = base.cget("size")
        if size > 0:                       # positive = points (vs. pixels)
            font.configure(size=max(9, size - 1))
        return font

    # --------------------------------------------------------------- theme
    def apply_theme(self, name: str, persist: bool = True) -> None:
        """Recolour every widget for *name* (and remember the choice)."""
        theme = THEMES.get(name, THEMES[DEFAULT_THEME])
        self.theme = theme
        self.theme_var.set(theme.name)

        # editor
        self.text.configure(
            background=theme.editor_bg,
            foreground=theme.editor_fg,
            insertbackground=theme.cursor,
            selectbackground=theme.selection_bg,
            inactiveselectbackground=theme.selection_bg,
        )
        self.body.configure(background=theme.editor_bg)
        self.configure(background=theme.window_bg)
        # the muted "press Enter to read it" line shown while a file waits
        self.text.tag_configure("hint", foreground=theme.muted_fg)

        # status bar — a quiet surface, a hairline rule and a state dot
        self.status_bar.configure(background=theme.status_bg)
        self.status_row.configure(background=theme.status_bg)
        self.status_rule.configure(background=theme.status_border)
        self.status_left.configure(
            background=theme.status_bg, foreground=theme.status_fg
        )
        self.status_right.configure(
            background=theme.status_bg, foreground=theme.muted_fg
        )
        self.status_dot.configure(background=theme.status_bg)
        self._paint_dot()
        self.resize_grip.configure(background=theme.status_bg)
        self._paint_grip()

        # scrollbars (ttk needs the 'clam' engine to honour colours)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "TScrollbar",
            background=theme.chrome_bg,
            troughcolor=theme.editor_bg,
            bordercolor=theme.chrome_bg,
            arrowcolor=theme.chrome_fg,
            lightcolor=theme.chrome_bg,
            darkcolor=theme.chrome_bg,
        )
        style.map("TScrollbar", background=[("active", theme.chrome_active_bg)])

        # menus
        self.menubar.configure(background=theme.menu_bg)
        for button in self._menu_buttons:
            button.configure(
                background=theme.menu_bg,
                foreground=theme.menu_fg,
                activebackground=theme.menu_active_bg,
                activeforeground=theme.menu_active_fg,
                disabledforeground=theme.muted_fg,
            )
        for menu in self._menus:
            menu.configure(
                background=theme.menu_bg,
                foreground=theme.menu_fg,
                activebackground=theme.menu_active_bg,
                activeforeground=theme.menu_active_fg,
                disabledforeground=theme.muted_fg,
                selectcolor=theme.menu_bg,
            )

        if persist:
            save_theme(theme.name)

    # ------------------------------------------------------------- actions
    def choose_file(self, _event: "tk.Event | None" = None) -> None:
        chosen = open_file(
            self,
            self.theme,
            initialdir=self.result.path.parent if self.result else Path.home(),
            title="Choose a file to convert",
        )
        if chosen:
            self.load_file(chosen)

    def load_file(self, path: Path) -> None:
        """Convert *path* in a background thread, then display the result."""
        if self._worker is not None and self._worker.is_alive():
            return                                  # one conversion at a time
        self._cancel_typing()
        self._set_status(f"Converting {path.name}…", "busy")
        self.stats_var.set("")
        self.configure(cursor="watch")
        self._set_save_enabled(False)

        self._worker = threading.Thread(
            target=self._convert_worker, args=(path,), daemon=True
        )
        self._worker.start()

    def _convert_worker(self, path: Path) -> None:
        try:
            result = convert_to_text(path)
        except Exception as error:
            self._events.put(("error", (path, error)))
            return
        self._events.put(("done", result))

    def _drain_events(self) -> None:
        """Move finished conversions from the worker thread into the UI."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "done":
                    self._conversion_done(payload)  # type: ignore[arg-type]
                else:
                    path, error = payload  # type: ignore[misc]
                    self._conversion_failed(path, error)
        except queue.Empty:
            pass
        self._drain_id = self.after(100, self._drain_events)

    def _conversion_done(self, result: ConversionResult) -> None:
        # The text is processed and kept, but *not* displayed: the editor goes
        # to the "ready" state and waits for Enter.
        self._cancel_typing()
        self.result = result
        self._reveal_state = "ready"
        self._reveal_body = ""
        self._reveal_pos = 0
        self.configure(cursor="")
        self._set_save_enabled(True)
        self._render()

        status = f"{result.path.name} — {result.method}"
        if result.warnings:
            status += "   ·   " + "; ".join(result.warnings)
        if len(status) > 160:
            status = status[:157] + "…"
        self._status_base = status
        self._status_base_state = "warn" if result.warnings else "done"
        self._set_status(
            f"{status}   ·   press Enter to read", self._status_base_state
        )
        self._stats_full = (
            f"{result.line_count:,} lines  ·  {human_size(result.size)}"
        )
        self.stats_var.set(self._stats_full)
        self.title(f"{result.path.name} — Textify")

    def _conversion_failed(self, path: Path, error: Exception) -> None:
        self.configure(cursor="")
        self._set_status(f"Could not convert {path.name}.", "error")
        messagebox.showerror(
            "Textify",
            f"Could not convert {path.name}:\n\n{error}",
            parent=self,
        )

    def save_text(self, _event: "tk.Event | None" = None) -> None:
        if self.result is None:
            return
        target = save_file(
            self,
            self.theme,
            initialdir=self.result.path.parent,
            initialfile=self.result.path.with_suffix(".txt").name,
            title="Save converted text",
        )
        if target is None:
            return
        try:
            target.write_text(self.result.text, encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Textify", f"Could not save:\n\n{error}", parent=self)
            return
        self._set_status(
            f"Saved {len(self.result.text):,} characters to {target.name}", "done"
        )

    # ------------------------------------------------------------- display
    def _compose_body(self) -> str:
        """The editor's text for self.result (line numbers, truncation note)."""
        if self.result is None:
            return ""
        lines = self.result.text.splitlines()
        total = len(lines)
        shown = lines
        truncated = False
        if total > DISPLAY_MAX_LINES:
            shown = lines[:DISPLAY_MAX_LINES]
            truncated = True

        if self.numbers_var.get():
            width = len(str(total))
            body = "\n".join(
                f"{number:>{width}} | {line}"
                for number, line in enumerate(shown, start=1)
            )
        else:
            body = "\n".join(shown)

        if len(body) > DISPLAY_MAX_CHARS:
            body = body[:DISPLAY_MAX_CHARS]
            truncated = True

        if truncated:
            body += (
                f"\n\n[display truncated — showing the first {len(shown):,} of "
                f"{total:,} lines; use File → Save as .txt… for the full text]"
            )
        return body

    def _render(self) -> None:
        """Repaint the editor from self.result — or hold the text back.

        A freshly converted file is not shown: it waits in the "ready" state
        until Enter types it out (see `_start_reveal`).  Toggling line numbers
        while a reveal is running simply finishes it.
        """
        self._cancel_typing()
        if self._reveal_state == "typing":
            self._reveal_state = "done"      # keep what has been typed so far

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")

        if self.result is None:
            self._reveal_state = "idle"
            self.text.insert("1.0", _PLACEHOLDER)
        elif self._reveal_state == "done":
            self.text.insert("1.0", self._compose_body())
            self.text.yview_moveto(0)
        else:                                 # "ready": converted, not shown
            self.text.insert("1.0", _READY_HINT, "hint")

        self.text.configure(state="disabled")

    # -------------------------------------------------------- typing reveal
    def _start_reveal(self, _event: "tk.Event | None" = None) -> str:
        """Enter: type the converted text out — or finish it, if it is running.

        Returns "break" so the keystroke stops here (the editor is read-only,
        so Return has nothing else to do).
        """
        if self._reveal_state == "typing":
            self._finish_reveal()             # Enter again = skip to the end
            return "break"
        if self.result is None or self._reveal_state == "done":
            return "break"

        self._reveal_body = self._compose_body()
        self._reveal_pos = 0
        self._reveal_accum = 0.0
        self._reveal_last = time.monotonic()
        self._reveal_state = "typing"

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)

        self._set_status(f"{self._status_base}   ·   reading…", "busy")
        if self.speed_var.get() <= 0 or not self._reveal_body:
            self._finish_reveal()             # "Instant"
        else:
            self._type_tick()
        return "break"

    def _type_tick(self) -> None:
        """Reveal the next slice of the text (called ~60 times a second)."""
        self._type_id = None
        if self._reveal_state != "typing":
            return

        speed = self.speed_var.get()          # read every tick: changes apply live
        now = time.monotonic()
        elapsed = now - self._reveal_last
        self._reveal_last = now
        if speed <= 0:
            self._finish_reveal()
            return

        self._reveal_accum += speed * elapsed
        step = int(self._reveal_accum)
        if step <= 0:                         # not a whole character yet
            self._type_id = self.after(TYPE_TICK_MS, self._type_tick)
            return
        self._reveal_accum -= step

        follow = self._view_at_bottom()       # don't fight a reader who scrolled up
        end = min(len(self._reveal_body), self._reveal_pos + step)
        self.text.configure(state="normal")
        self.text.insert("end", self._reveal_body[self._reveal_pos:end])
        self.text.configure(state="disabled")
        self._reveal_pos = end
        if follow:
            self.text.see("end")

        if self._reveal_pos >= len(self._reveal_body):
            self._finish_reveal()
            return

        self.stats_var.set(
            f"{self._reveal_pos:,} / {len(self._reveal_body):,} chars"
            f"  ·  {speed} chars/s"
        )
        self._type_id = self.after(TYPE_TICK_MS, self._type_tick)

    def _finish_reveal(self) -> None:
        """Stop typing and show the whole text."""
        self._cancel_typing()
        if self._reveal_state != "typing":
            return
        rest = self._reveal_body[self._reveal_pos:]
        if rest:
            self.text.configure(state="normal")
            self.text.insert("end", rest)
            self.text.configure(state="disabled")
        self._reveal_pos = len(self._reveal_body)
        self._reveal_state = "done"
        self.stats_var.set(self._stats_full)
        self._set_status(self._status_base, self._status_base_state)

    def _cancel_typing(self) -> None:
        if self._type_id is not None:
            try:
                self.after_cancel(self._type_id)
            except tk.TclError:
                pass
            self._type_id = None

    def _view_at_bottom(self) -> bool:
        """True when the editor is scrolled to its end (so we may follow it)."""
        try:
            return self.text.yview()[1] >= 0.999
        except tk.TclError:
            return True

    def _speed_chosen(self) -> None:
        """A preset was picked — remember it (a running reveal adapts at once)."""
        save_speed(self.speed_var.get())

    def _ask_speed(self) -> None:
        """View → Typing speed → Custom…: an exact characters-per-second rate."""
        dialog = SpeedDialog(self, self.theme, self.speed_var.get())
        self.wait_window(dialog)
        if dialog.result is not None:
            self.speed_var.set(dialog.result)
            save_speed(dialog.result)

    def _set_save_enabled(self, enabled: bool) -> None:
        self.file_menu.entryconfig(
            self.SAVE_LABEL, state="normal" if enabled else "disabled"
        )

    def _set_status(self, message: str, state: str = "idle") -> None:
        """Update the bar's text and recolour the state dot.

        *state* is one of idle / busy / done / warn / error.
        """
        self.status_var.set(message)
        self._status_state = state
        self._paint_dot()

    def _paint_dot(self) -> None:
        colours = {
            "idle": self.theme.muted_fg,
            "busy": self.theme.accent,
            "done": self.theme.success,
            "warn": self.theme.warning,
            "error": self.theme.error,
        }
        self.status_dot.itemconfigure(
            self._dot, fill=colours.get(self._status_state, self.theme.muted_fg)
        )

    def _paint_grip(self) -> None:
        """Three little diagonal strokes in the bottom-right corner."""
        self.resize_grip.delete("all")
        colour = self.theme.muted_fg
        for offset in (0, 4, 8):
            self.resize_grip.create_line(
                15 - offset, 15, 15, 15 - offset, fill=colour, width=1
            )

    # ------------------------------------------------ frameless window bits
    def _enable_window_drag(self) -> None:
        """Give the window the two gestures a title bar would have provided.

        The menu bar doubles as the drag handle and the grip in the status bar
        resizes the window.  (The desktop's own Alt+drag / Alt+right-drag keep
        working too, because the window stays managed.)
        """
        self._bind_drag(self.menubar)
        self.resize_grip.bind("<Button-1>", self._start_resize)
        self.resize_grip.bind("<B1-Motion>", self._do_resize)

    def _on_map(self, attempt: int = 0) -> None:
        """Apply the "no title bar" hint once the window is really mapped."""
        if self.title_bar_hidden or not self.frameless:
            return
        if not self.winfo_ismapped():
            if attempt < 40:                    # give up after ~1 second
                self.after(25, lambda: self._on_map(attempt + 1))
            return
        self.title_bar_hidden = hide_title_bar(self)

    def _bind_drag(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event: "tk.Event") -> None:
        self._move_origin = (
            event.x_root - self.winfo_rootx(),
            event.y_root - self.winfo_rooty(),
        )

    def _do_move(self, event: "tk.Event") -> None:
        if self._move_origin is None:
            return
        offset_x, offset_y = self._move_origin
        self.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _start_resize(self, event: "tk.Event") -> None:
        self._resize_origin = (
            event.x_root,
            event.y_root,
            self.winfo_width(),
            self.winfo_height(),
        )

    def _do_resize(self, event: "tk.Event") -> None:
        if self._resize_origin is None:
            return
        start_x, start_y, width, height = self._resize_origin
        new_width = max(self._min_size[0], width + (event.x_root - start_x))
        new_height = max(self._min_size[1], height + (event.y_root - start_y))
        self.geometry(f"{new_width}x{new_height}")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Textify",
            "Textify — any file → text\n\n"
            "Converts documents, spreadsheets, presentations, PDFs, HTML,\n"
            "images (OCR), archives and even raw binary files into plain\n"
            "text and shows them line by line.\n\n"
            "File → Open… (Ctrl+O) opens a file, File → Save as .txt…\n"
            "(Ctrl+S) saves the result, View → Theme changes the colours.\n\n"
            "A converted file is held back rather than dumped on screen:\n"
            "press Enter and it is typed out character by character.\n"
            "View → Typing speed sets how fast (Enter again skips ahead).\n\n"
            "The window has no title bar: drag the menu bar to move it,\n"
            "use the grip in the bottom-right corner to resize, or use\n"
            "your desktop's own Alt+drag / Alt+right-drag.",
            parent=self,
        )


def run(start_file: str | None = None) -> None:
    """Create the window and start the Tk event loop."""
    if sys.platform == "win32":                     # crisp text on Windows
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    app = TextifyApp()
    if start_file:
        app.load_file(Path(start_file))
    app.mainloop()
