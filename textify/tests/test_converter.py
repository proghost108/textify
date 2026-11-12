"""
Tests for textify/converter.py (and a smoke test of main.py's CLI mode).

Run either way:

    python tests/test_converter.py          # standalone, prints a report
    pytest tests/ -q                        # if you have pytest installed
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from converter import (  # noqa: E402
    ConversionResult,
    convert_to_text,
    human_size,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_pdf(path: Path, text: str = "Hello Textify PDF") -> None:
    """Write a minimal but valid single-page PDF containing one line of text."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += b"xref\n"
    out += f"0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


def build_samples(directory: Path) -> dict[str, Path]:
    """Create one sample of every interesting file type."""
    samples: dict[str, Path] = {}

    samples["text"] = directory / "hello.txt"
    samples["text"].write_text("Hello, Textify!\nline two\n", encoding="utf-8")

    samples["utf16"] = directory / "utf16.txt"
    samples["utf16"].write_bytes("héllo utf-16\n".encode("utf-16"))

    samples["latin1"] = directory / "latin1.txt"
    samples["latin1"].write_bytes("café au lait, quelques accents\n".encode("latin-1"))

    samples["code"] = directory / "app.py"
    samples["code"].write_text("print('hi')\n", encoding="utf-8")

    samples["json"] = directory / "data.json"
    samples["json"].write_text('{"key": "value"}\n', encoding="utf-8")

    samples["binary"] = directory / "binary.bin"
    samples["binary"].write_bytes(bytes(range(256)) * 8)

    samples["noext_binary"] = directory / "mystery"
    samples["noext_binary"].write_bytes(os.urandom(4096) + b"\x00")

    samples["noext_text"] = directory / "Makefile"
    samples["noext_text"].write_text("all:\n\tgcc main.c\n", encoding="utf-8")

    samples["empty"] = directory / "empty.txt"
    samples["empty"].write_bytes(b"")

    make_pdf(samples.setdefault("pdf", directory / "document.pdf"))

    samples["docx"] = directory / "letter.docx"
    _make_docx(samples["docx"])

    samples["xlsx"] = directory / "sheet.xlsx"
    _make_xlsx(samples["xlsx"])

    samples["pptx"] = directory / "deck.pptx"
    _make_pptx(samples["pptx"])

    samples["html"] = directory / "page.html"
    samples["html"].write_text(
        "<html><head><title>Test Page</title>"
        "<style>.x{color:red}</style>"
        "<script>var secretScript = 1;</script></head>"
        "<body><h1>Big Heading</h1><p>Hello <b>world</b></p>"
        "<ul><li>one</li><li>two</li></ul></body></html>",
        encoding="utf-8",
    )

    samples["image"] = directory / "photo.png"
    _make_image(samples["image"])

    samples["zip"] = directory / "stuff.zip"
    with zipfile.ZipFile(samples["zip"], "w") as archive:
        archive.writestr("readme.txt", "hello")
        archive.writestr("sub/deep.txt", "world")

    samples["tar.gz"] = directory / "archive.tar.gz"
    with tarfile.open(samples["tar.gz"], "w:gz") as archive:
        archive.add(samples["text"], arcname="nested/hello.txt")

    import gzip

    samples["gz_text"] = directory / "notes.txt.gz"
    with gzip.open(samples["gz_text"], "wb") as stream:
        stream.write(b"compressed hello\n" * 5)

    samples["legacy_doc"] = directory / "legacy.doc"
    samples["legacy_doc"].write_bytes(bytes(range(256)) * 4)

    return samples


def _make_docx(path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("Hello from Textify.")
    document.add_paragraph("Second paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "name"
    table.cell(0, 1).text = "score"
    table.cell(1, 0).text = "ada"
    table.cell(1, 1).text = "10"
    document.save(str(path))


def _make_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["name", "score"])
    sheet.append(["ada", 10])
    sheet.append(["grace", 9])
    workbook.save(str(path))


def _make_pptx(path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    slides = Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[5])  # blank
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
    box.text_frame.text = "Slide text here"
    slide.notes_slide.notes_text_frame.text = "speaker notes"
    slides.save(str(path))


def _make_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 160), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=64)       # Pillow >= 10.1
    except TypeError:                                # older Pillow fallback
        font = ImageFont.load_default()
    draw.text((20, 40), "HELLO TEXTIFY", font=font, fill="black")
    image.save(path)


# --------------------------------------------------------------------------
# the tests (plain asserts so they run standalone AND under pytest)
# --------------------------------------------------------------------------
def test_text_files(tmp_dir: Path) -> None:
    samples = build_samples(tmp_dir)

    result = convert_to_text(samples["text"])
    assert "plain text (utf-8)" == result.method
    assert "Hello, Textify!" in result.text
    assert result.line_count == 2

    result = convert_to_text(samples["utf16"])
    assert result.method.startswith("plain text")
    assert "héllo utf-16" in result.text

    result = convert_to_text(samples["latin1"])
    assert "plain text (latin-1)" == result.method
    assert "café au lait" in result.text

    assert "print('hi')" in convert_to_text(samples["code"]).text
    assert '"key": "value"' in convert_to_text(samples["json"]).text

    result = convert_to_text(samples["noext_text"])  # no extension → sniffing
    assert result.method.startswith("plain text")
    assert "gcc main.c" in result.text


def test_empty_file(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["empty"])
    assert result.method == "empty file"
    assert result.line_count == 1


def test_binary_falls_back_to_hex_dump(tmp_dir: Path) -> None:
    samples = build_samples(tmp_dir)

    result = convert_to_text(samples["binary"])
    assert result.method == "hex dump"
    assert result.text.startswith("Hex dump — showing")
    assert "00000000  00 01 02 03" in result.text     # offset + hex column
    assert "|................|" in result.text          # non-printables → dots
    assert not result.warnings

    result = convert_to_text(samples["noext_binary"])  # no extension → sniffing
    assert result.method == "hex dump"
    assert result.text.startswith("Hex dump — showing")

    # a fake legacy Word file: hex dump + a helpful warning
    result = convert_to_text(samples["legacy_doc"])
    assert result.method == "hex dump"
    assert any("legacy Word" in warning for warning in result.warnings)


def test_pdf(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["pdf"])
    assert result.method.startswith("PDF text extraction")
    assert "Hello Textify PDF" in result.text
    assert "--- Page 1 ---" in result.text


def test_docx(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["docx"])
    assert result.method.startswith("Word text extraction")
    assert "Hello from Textify." in result.text
    assert "ada\t10" in result.text  # table rows joined by tabs


def test_xlsx(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["xlsx"])
    assert result.method.startswith("Excel extraction")
    assert "=== Sheet: Data ===" in result.text
    assert "name\tscore" in result.text
    assert "ada\t10" in result.text


def test_pptx(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["pptx"])
    assert result.method.startswith("PowerPoint extraction")
    assert "=== Slide 1 ===" in result.text
    assert "Slide text here" in result.text
    assert "[notes] speaker notes" in result.text


def test_html(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["html"])
    assert result.method.startswith("HTML tag stripping")
    assert "Big Heading" in result.text
    assert "Hello world" in result.text
    assert "one" in result.text and "two" in result.text
    assert "secretScript" not in result.text      # scripts are skipped
    assert "color:red" not in result.text         # styles are skipped
    assert "<p>" not in result.text               # no raw tags remain


def test_image_ocr(tmp_dir: Path) -> None:
    result = convert_to_text((build_samples(tmp_dir))["image"])
    if result.method.startswith("OCR"):
        assert "HELLO TEXTIFY" in result.text
    else:
        # tesseract not usable in this environment — must degrade gracefully
        assert result.method == "hex dump"
        assert any("tesseract" in warning for warning in result.warnings)


def test_archives(tmp_dir: Path) -> None:
    samples = build_samples(tmp_dir)

    result = convert_to_text(samples["zip"])
    assert result.method.startswith("archive listing")
    assert "readme.txt" in result.text
    assert "sub/deep.txt" in result.text

    result = convert_to_text(samples["tar.gz"])
    assert result.method.startswith("archive listing")
    assert "nested/hello.txt" in result.text

    result = convert_to_text(samples["gz_text"])
    assert result.method.startswith("decompressed gz")
    assert result.text.count("compressed hello") == 5


def test_human_size() -> None:
    assert human_size(0) == "0 B"
    assert human_size(123) == "123 B"
    assert human_size(1234) == "1.2 KB"
    assert human_size(5 * 1024 * 1024) == "5.2 MB"


def test_cli(tmp_dir: Path) -> None:
    samples = build_samples(tmp_dir)
    output = tmp_dir / "out.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "main.py"),
            str(samples["docx"]),
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Hello from Textify." in output.read_text(encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), str(samples["text"])],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Hello, Textify!" in completed.stdout


# --------------------------------------------------------------------------
# standalone runner
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
        with tempfile.TemporaryDirectory(prefix="textify-test-") as raw_dir:
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
