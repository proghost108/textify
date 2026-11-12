"""
textify/converter.py — turn any file into plain text.

The single entry point is `convert_to_text(path)`, which returns a
`ConversionResult` describing what was done.  The strategy is picked from the
file extension first, then refined by sniffing the actual bytes:

* text-ish files ............ decoded as UTF-8 / UTF-16 / latin-1
* PDF ....................... text extraction (pypdf)
* DOCX / XLSX / PPTX ........ extraction (python-docx / openpyxl / python-pptx)
* HTML ...................... tags stripped (stdlib html.parser)
* images .................... OCR (tesseract via pytesseract + Pillow)
* ZIP / TAR / .tgz .......... listing of the archive contents
* gz / bz2 / xz ............. decompressed, then treated like any other file
* everything else ........... a readable hex dump

All third-party libraries are optional: if one is missing (or a specific
file cannot be parsed) the converter degrades gracefully — usually to a hex
dump — and explains itself through `ConversionResult.warnings`.
"""

from __future__ import annotations

import bz2
import codecs
import gzip
import io
import lzma
import re
import shutil
import tarfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------
# Optional third-party dependencies (the app works without any of them)
# --------------------------------------------------------------------------
try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - depends on the environment
    PdfReader = None

try:
    import docx  # python-docx
except ImportError:  # pragma: no cover
    docx = None

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    from pptx import Presentation
except ImportError:  # pragma: no cover
    Presentation = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

# --------------------------------------------------------------------------
# Limits (protect the GUI from huge inputs)
# --------------------------------------------------------------------------
MAX_FILE_BYTES = 512 * 1024 * 1024      # refuse files bigger than 512 MB
SNIFF_BYTES = 64 * 1024                 # sample size for the text/binary sniff
HEX_DUMP_LIMIT = 1 * 1024 * 1024         # hex-dump at most the first 1 MiB
MAX_DECOMPRESSED = 64 * 1024 * 1024      # cap for gzip/bz2/xz expansion

Handler = Callable[[Path], "tuple[str, str, list[str]]"]


class ConversionError(RuntimeError):
    """A file could not be converted at all (e.g. it is too large)."""


class MissingDependency(RuntimeError):
    """A conversion strategy needs a library that is not installed."""

    def __init__(self, package: str, purpose: str) -> None:
        super().__init__(
            f"{purpose} requires the '{package}' package (pip install {package})"
        )


@dataclass
class ConversionResult:
    """Everything the GUI needs to know about one conversion."""

    path: Path
    size: int
    method: str                          # human-readable strategy description
    text: str
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Normalise line endings and make sure the text ends with a newline.
        self.text = self.text.replace("\r\n", "\n").replace("\r", "\n")
        if self.text and not self.text.endswith("\n"):
            self.text += "\n"

    @property
    def line_count(self) -> int:
        return len(self.text.splitlines())


def human_size(count: float) -> str:
    """1234 -> '1.2 KB' — for status bars and error messages."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1000 or unit == "TB":
            return f"{count:,.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1000
    return f"{count:.1f} TB"  # pragma: no cover - unreachable


# --------------------------------------------------------------------------
# Decoding: is this blob of bytes actually text?
# --------------------------------------------------------------------------
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),     # must be checked before UTF-16
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _decode_bytes(data: bytes) -> tuple[str, str] | None:
    """Decode *data* as text.  Returns (text, encoding), or None if it looks
    like binary data."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                return data.decode(encoding, errors="replace"), encoding

    if b"\x00" in data:
        return None                       # NUL bytes → almost certainly binary

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # Not valid UTF-8 and no NUL bytes: accept it as legacy 8-bit text
    # (latin-1 / cp1252) as long as the sample is mostly printable.
    sample = data[:SNIFF_BYTES]
    printable = sum(
        1 for byte in sample if 32 <= byte < 127 or byte in (9, 10, 13, 12, 11)
    )
    if sample and printable / len(sample) >= 0.90:
        return data.decode("latin-1"), "latin-1"
    return None


# --------------------------------------------------------------------------
# Hex dump fallback (works for literally any file)
# --------------------------------------------------------------------------
def _hex_dump_bytes(data: bytes, total_size: int | None = None) -> str:
    total = len(data) if total_size is None else total_size
    shown = data[:HEX_DUMP_LIMIT]
    lines = [f"Hex dump — showing {len(shown):,} of {total:,} bytes"]
    for offset in range(0, len(shown), 16):
        chunk = shown[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hex_part:<47}  |{ascii_part}|")
    return "\n".join(lines)


def _hex_dump_file(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(HEX_DUMP_LIMIT)
    return _hex_dump_bytes(data, total_size=path.stat().st_size)


# --------------------------------------------------------------------------
# Format-specific handlers — each returns (text, method, warnings)
# --------------------------------------------------------------------------
def _extract_pdf(path: Path) -> tuple[str, str, list[str]]:
    if PdfReader is None:
        raise MissingDependency("pypdf", "PDF text extraction")
    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:  # many PDFs are "encrypted" with an empty password
            reader.decrypt("")
        except Exception:
            pass  # page access below will raise, and the caller falls back
    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        extracted = (page.extract_text() or "").strip()
        if extracted:
            pages.append(f"--- Page {number} ---\n{extracted}")
    if not pages:
        return (
            "(no extractable text found in this PDF)",
            "PDF text extraction (pypdf) — no text found",
            ["the PDF may contain scanned images instead of text"],
        )
    return "\n\n".join(pages), "PDF text extraction (pypdf)", []


def _extract_docx(path: Path) -> tuple[str, str, list[str]]:
    if docx is None:
        raise MissingDependency("python-docx", "Word (.docx) extraction")
    document = docx.Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
    return text, "Word text extraction (python-docx)", []


def _extract_xlsx(path: Path) -> tuple[str, str, list[str]]:
    if openpyxl is None:
        raise MissingDependency("openpyxl", "Excel (.xlsx) extraction")
    workbook = openpyxl.load_workbook(
        filename=str(path), read_only=True, data_only=True
    )
    try:
        chunks: list[str] = []
        for sheet in workbook.worksheets:
            chunks.append(f"=== Sheet: {sheet.title} ===")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value) for value in row]
                if any(cell.strip() for cell in cells):
                    chunks.append("\t".join(cells))
    finally:
        workbook.close()
    return "\n".join(chunks), "Excel extraction (openpyxl)", []


def _extract_pptx(path: Path) -> tuple[str, str, list[str]]:
    if Presentation is None:
        raise MissingDependency("python-pptx", "PowerPoint (.pptx) extraction")
    slides = Presentation(str(path))
    chunks: list[str] = []
    for number, slide in enumerate(slides.slides, start=1):
        chunks.append(f"=== Slide {number} ===")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                chunks.append(shape.text_frame.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    chunks.append("\t".join(cell.text for cell in row.cells))
        if getattr(slide, "has_notes_slide", False):
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                chunks.append(f"[notes] {note}")
    return "\n".join(chunks), "PowerPoint extraction (python-pptx)", []


class _HTMLTextExtractor(HTMLParser):
    """Collect the visible text of an HTML document, one block per line."""

    _BREAK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
        "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav",
        "ol", "p", "pre", "section", "table", "tr", "title", "ul",
    }
    _SKIP_TAGS = {"script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP_TAGS:
            self._skipping += 1
        elif tag in self._BREAK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:  # e.g. <br/>
        if tag in self._BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self._BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skipping or not data.strip():
            return
        self._parts.append(data)

    @property
    def text(self) -> str:
        lines = [line.strip() for line in "".join(self._parts).splitlines()]
        cleaned: list[str] = []
        for line in lines:
            if not line and (not cleaned or not cleaned[-1]):
                continue                  # collapse runs of blank lines
            cleaned.append(line)
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        return "\n".join(cleaned)


def _extract_html(path: Path) -> tuple[str, str, list[str]]:
    decoded = _decode_bytes(path.read_bytes())
    if decoded is None:
        raise ValueError("file does not look like text")
    source, _ = decoded
    parser = _HTMLTextExtractor()
    parser.feed(source)
    parser.close()
    if not parser.text.strip():
        return "(no visible text in this HTML document)", "HTML tag stripping", []
    return parser.text, "HTML tag stripping (html.parser)", []


# --- images (OCR) ----------------------------------------------------------

# formats tesseract's leptonica backend reads natively — anything else gets
# re-encoded to PNG in memory first
_NATIVE_OCR_FORMATS = {
    "PNG", "JPEG", "JPG", "TIFF", "TIF", "BMP", "GIF",
    "PGM", "PPM", "PNM", "WEBP", "JP2",
}


def _ocr_image(path: Path) -> tuple[str, str, list[str]]:
    if Image is None or pytesseract is None:
        raise MissingDependency("pytesseract + Pillow", "image OCR")
    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "the 'tesseract' program was not found (Arch: pacman -S tesseract, "
            "Debian/Ubuntu: apt install tesseract-ocr)"
        )
    with Image.open(path) as image:
        image.load()
        details = f"{image.format or 'image'}, {image.width}x{image.height}px"
        if (image.format or "").upper() not in _NATIVE_OCR_FORMATS:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            image = Image.open(buffer)
        ocr_text = pytesseract.image_to_string(image).strip()
    if ocr_text:
        return ocr_text, f"OCR (tesseract) — {details}", []
    return (
        f"(no text detected in this {details} — it may be a photo or drawing)",
        f"OCR (tesseract) — {details}, no text detected",
        [],
    )


# --- archives --------------------------------------------------------------
def _list_zip(path: Path) -> tuple[str, str, list[str]]:
    entries: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            year, month, day, hour, minute, _second = info.date_time
            stamp = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
            entries.append(f"{info.file_size:>12,}  {stamp}  {info.filename}")
    header = f"Contents of {path.name} — {len(entries):,} entries"
    return "\n".join([header] + entries), "archive listing (zipfile)", []


def _list_tar(path: Path) -> tuple[str, str, list[str]]:
    entries: list[str] = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(member.mtime))
            if member.isdir():
                kind = "dir"
            elif member.issym() or member.islnk():
                kind = "link"
            else:
                kind = "file"
            entries.append(
                f"{member.size:>12,}  {stamp}  {kind:<4} {member.name}"
            )
    header = f"Contents of {path.name} — {len(entries):,} entries"
    return "\n".join([header] + entries), "archive listing (tarfile)", []


_COMPRESSORS = {".gz": gzip, ".bz2": bz2, ".xz": lzma}


def _read_compressed(path: Path) -> tuple[str, str, list[str]]:
    ext = path.suffix.lower()
    opener = _COMPRESSORS[ext]
    with opener.open(path, "rb") as stream:
        data = stream.read(MAX_DECOMPRESSED + 1)
    warnings: list[str] = []
    if len(data) > MAX_DECOMPRESSED:
        warnings.append(
            f"decompressed output is very large — limited to the first "
            f"{MAX_DECOMPRESSED:,} bytes"
        )
        data = data[:MAX_DECOMPRESSED]
    decoded = _decode_bytes(data)
    if decoded is not None:
        text, encoding = decoded
        return text, f"decompressed {ext[1:]} → plain text ({encoding})", warnings
    warnings.append("decompressed contents are binary")
    dump = _hex_dump_bytes(data)
    return dump, f"decompressed {ext[1:]} → hex dump", warnings


def _handle_compressed(path: Path) -> tuple[str, str, list[str]]:
    """A .gz/.bz2/.xz is either a compressed tar or a single compressed file."""
    try:
        return _list_tar(path)
    except tarfile.ReadError:
        return _read_compressed(path)


# --------------------------------------------------------------------------
# Extension tables
# --------------------------------------------------------------------------
_TEXT_EXTENSIONS = {
    ".txt", ".text", ".md", ".markdown", ".mdown", ".rst", ".log", ".nfo",
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".xml", ".svg", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".env",
    ".editorconfig", ".gitignore", ".gitattributes", ".npmrc",
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".cs", ".go", ".rs",
    ".rb", ".php", ".pl", ".pm", ".lua", ".r", ".swift", ".kt", ".kts",
    ".scala", ".dart", ".vue", ".svelte", ".css", ".scss", ".sass", ".less",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
    ".sql", ".graphql", ".proto", ".tf", ".hcl", ".mk", ".patch", ".diff",
    ".srt", ".vtt", ".ass", ".sub", ".lrc", ".tex", ".bib", ".org", ".wiki",
}

_OFFICE_HANDLERS: dict[str, Handler] = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    ".pptx": _extract_pptx,
}

_HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}

_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".jp2", ".bmp", ".gif", ".tif",
    ".tiff", ".webp", ".pbm", ".pgm", ".ppm", ".pnm",
}

_ARCHIVE_HANDLERS: dict[str, Handler] = {
    ".zip": _list_zip,
    ".tar": _list_tar,
    ".tgz": _list_tar,
    ".tbz2": _list_tar,
    ".txz": _list_tar,
    ".gz": _handle_compressed,
    ".bz2": _handle_compressed,
    ".xz": _handle_compressed,
}

# extra context appended as a warning when these show up
_UNSUPPORTED_HINTS = {
    ".doc": "legacy Word format — re-save as .docx to extract its text",
    ".xls": "legacy Excel format — re-save as .xlsx to extract its text",
    ".ppt": "legacy PowerPoint format — re-save as .pptx to extract its text",
    ".7z": "7-Zip archives are not supported — unpack the file first",
    ".rar": "RAR archives are not supported — unpack the file first",
}


def _extension_handler(ext: str) -> Handler | None:
    if ext in _OFFICE_HANDLERS:
        return _OFFICE_HANDLERS[ext]
    if ext in _HTML_EXTENSIONS:
        return _extract_html
    if ext in _IMAGE_EXTENSIONS:
        return _ocr_image
    return _ARCHIVE_HANDLERS.get(ext)


# --------------------------------------------------------------------------
# The main entry point
# --------------------------------------------------------------------------
def convert_to_text(path: str | Path) -> ConversionResult:
    """Convert any file at *path* into text.

    Raises FileNotFoundError for missing files and ConversionError when a
    file cannot be handled at all (e.g. it is too large).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")

    size = path.stat().st_size
    if size == 0:
        return ConversionResult(path, 0, "empty file", "(this file is empty)")
    if size > MAX_FILE_BYTES:
        raise ConversionError(
            f"{path.name} is {human_size(size)} — larger than the "
            f"{human_size(MAX_FILE_BYTES)} limit"
        )

    ext = path.suffix.lower()
    warnings: list[str] = []

    # 1) Is there a dedicated handler for this extension?
    handler = _extension_handler(ext)
    if handler is not None:
        try:
            text, method, extra = handler(path)
            return ConversionResult(path, size, method, text, warnings + extra)
        except MissingDependency as error:
            warnings.append(f"{error} — falling back to raw inspection")
        except Exception as error:
            warnings.append(
                f"could not extract {ext or 'this file'} ({error}) — "
                f"falling back to raw inspection"
            )

    hint = _UNSUPPORTED_HINTS.get(ext)
    if hint:
        warnings.append(hint)

    # 2) Maybe it is just plain text after all.
    decoded: tuple[str, str] | None = None
    if ext in _TEXT_EXTENSIONS:
        decoded = _decode_bytes(path.read_bytes())
        if decoded is None:
            warnings.append(
                "contents look binary despite the text extension — "
                "showing a hex dump"
            )
    else:
        with path.open("rb") as stream:
            sample = stream.read(SNIFF_BYTES)
        decoded = _decode_bytes(sample)
        if decoded is not None:  # promising — decode the whole file
            decoded = _decode_bytes(path.read_bytes())
            if decoded is None:
                warnings.append(
                    "the file starts as text but contains binary data — "
                    "showing a hex dump"
                )
    if decoded is not None:
        text, encoding = decoded
        return ConversionResult(path, size, f"plain text ({encoding})", text, warnings)

    # 3) Last resort: a hex dump.  Works for literally anything.
    return ConversionResult(path, size, "hex dump", _hex_dump_file(path), warnings)
