# Textify — any file → text

A small desktop app for PC users (Linux / Windows / macOS). Pick **any** file
and Textify converts it into plain text and displays it **line by line** in a
simple editor window. Save the result as a `.txt` whenever you want.

## What you get for each file type

| File type | What Textify shows |
| --- | --- |
| `.txt` `.md` `.py` `.json` `.csv` … | the text itself (UTF-8 / UTF-16 / latin-1) |
| `.pdf` | extracted text, page by page (pypdf) |
| `.docx` | paragraphs + tables (python-docx) |
| `.xlsx` | every sheet, rows tab-separated (openpyxl) |
| `.pptx` | slide text + speaker notes (python-pptx) |
| `.html` | visible text — tags, scripts and styles stripped |
| `.png` `.jpg` `.tiff` … | text found by OCR (tesseract) |
| `.zip` `.tar` `.tgz` | a listing of the archive contents |
| `.gz` `.bz2` `.xz` | decompressed, then converted like any other file |
| **anything else** (`.exe`, `.so`, …) | a readable hex dump — *every* file becomes text |

If a format can't be extracted (corrupt file, missing library), Textify never
crashes: it falls back to the hex dump and tells you why in the status bar.

## Setup

### Arch Linux

```bash
sudo pacman -S tk tesseract        # Tk for the GUI; tesseract for image OCR
cd ~/Projects/textify

python -m venv .venv               # Arch blocks system-wide pip installs,
source .venv/bin/activate          # so we use a virtual environment
pip install -r requirements.txt

python main.py
```

Notes:

* `tk` is required for the window; `tesseract` is optional (without it, images
  fall back to a hex dump).
* Other OCR languages: `sudo pacman -S tesseract-data-deu` (etc.) — pytesseract
  defaults to English.
* Next time, you only need `source .venv/bin/activate && python main.py`.

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/) (tick
   *Add to PATH* — Tk is included).
2. For OCR, install [tesseract via UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
   (also included in PATH).
3. Then:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py main.py
```

### Debian / Ubuntu

```bash
sudo apt install python3 python3-venv python3-tk tesseract-ocr
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### macOS

Install Python from python.org (Tk included) and, for OCR,
`brew install tesseract`; then the same venv steps as above.

## Using it

**GUI** — `python main.py`

* *File → Open…* (or `Ctrl+O`) → pick anything; conversion runs in the background
* read the result line by line; toggle line numbers under *View → Line numbers*
* *File → Save as .txt…* (or `Ctrl+S`) — always saves the **full** text, even
  when the on-screen view was truncated for huge files
* change the colours under *View → Theme*
* the slim status bar shows the strategy used and any warnings, with a little
  state dot (grey = idle, blue = converting, green = done, amber = warnings,
  red = error) and the line count / size on the right

There is no toolbar and no scrollbars — everything lives in the menu bar and the
two shortcuts above. The mouse wheel scrolls the text, `Shift`+wheel scrolls it
sideways. On Linux the window is drawn without an OS title bar for a clean,
terminal-like look (see *Frameless window* below).

### Frameless window (Linux)

On Linux the window drops its OS title bar, leaving just the **File / View /
Help** tabs, the text and the status bar. It does this with the
`_MOTIF_WM_HINTS` X11 property, which KWin, mutter, Xfwm and Openbox all honour
for X11 clients (including through XWayland).

Unlike an *override-redirect* window, the window stays fully **managed** by the
desktop: it keeps its taskbar entry, `Alt`+`Tab`, normal click-to-focus and
window raising, and the app's own dialogs (the file picker) still stack on top
of it. That also means the desktop's own gestures keep working — `Alt`+drag to
move, `Alt`+right-drag to resize.

For the gestures a title bar would have provided, Textify adds two of its own:

* **move** — drag the menu bar (the empty space next to the tabs)
* **resize** — drag the grip in the bottom-right corner of the status bar

Set `TEXTIFY_DECORATED=1` in the environment to keep a normal, decorated window
instead. (On Windows and macOS the window is decorated as usual.)

### Themes

Five built-in themes: **Dark** (default), **Light**, **Nord**, **Dracula** and
**Gruvbox**. Pick one under *View → Theme*; the choice is remembered in
`~/.config/textify/config.json` and applied again on the next launch. Delete
that file to go back to the default.

### The file picker

Tk's stock file dialog ignores the app's colours, so Textify ships its own,
fully themed one (for both *Open* and *Save as*):

* **Up** / **Home** buttons, plus an editable path box — type a folder and press
  Enter to jump there
* folders come first and everything is sorted; double-click (or Enter) a folder
  to open it, a file to choose it
* the **eye symbol** on the right toggles hidden (dot) files on and off —
  open eye = showing them, slashed eye = hidden
* *Save as* appends `.txt` when you leave the extension out and asks before
  overwriting an existing file

**Terminal** — handy for scripts:

```bash
python main.py document.pdf            # print the text to stdout
python main.py photo.png -o notes.txt # write it to a file
python main.py --gui document.pdf     # open the GUI with the file loaded
```

## Running the tests

```bash
source .venv/bin/activate
python tests/test_converter.py         # 12 checks for the conversion engine
python tests/test_themes.py            # 6 checks for the themes + settings file
pytest tests/ -q                      # the same tests, if you prefer pytest
```

`tests/smoke_gui.py` and `tests/smoke_filepicker.py` additionally drive the
real Tkinter window and the file picker headlessly (themes, conversion,
rendering, the hidden-files toggle; require a working `tk` install — see the
notes inside those files).

## Known limitations

* Legacy binary Office formats (`.doc`, `.xls`, `.ppt`) can't be extracted —
  you get a hex dump and a hint to re-save them as `.docx`/`.xlsx`/`.pptx`.
* Scanned PDFs contain images, not text — the extractor reports "no text found".
* `.7z` and `.rar` archives are not read (unpack them first).
* Password-protected / encrypted files can't be converted.
* Files larger than 512 MB are refused; the screen shows at most the first
  20,000 lines (saving still writes everything).

## Project layout

```
main.py               entry point — GUI by default, CLI when given a file
gui.py                the Tkinter window (conversion in a worker thread)
filepicker.py         the themed file dialog + hidden-files eye toggle
converter.py          the core: any file → text, with graceful fallbacks
themes.py             colour themes + the ~/.config/textify settings file
requirements.txt      pip dependencies (all optional at runtime)
tests/
  test_converter.py  end-to-end tests for every supported format
  test_themes.py     tests for the palettes and the settings file
  smoke_gui.py       headless smoke test of the window itself
  smoke_filepicker.py  headless smoke test of the file dialog
```
