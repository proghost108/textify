#!/usr/bin/env python3
"""
Textify — convert any file on your PC into text and read it line by line.

Run the GUI (the usual way):

    python main.py

Or convert straight from the terminal:

    python main.py document.pdf
    python main.py photo.png -o notes.txt
    python main.py --gui document.pdf     # open the GUI with the file loaded
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="textify",
        description="Convert any file into text (documents, images, archives, "
        "even binary files).",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="file to convert (omit it to launch the GUI)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="TXT",
        help="write the converted text to this file instead of stdout",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="open the GUI even when a file is given",
    )
    return parser


def run_cli(args: argparse.Namespace) -> int:
    from converter import convert_to_text

    try:
        result = convert_to_text(args.file)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"# {result.path.name}: {result.method}, "
        f"{result.line_count:,} lines",
        file=sys.stderr,
    )

    if args.output:
        Path(args.output).write_text(result.text, encoding="utf-8")
        print(f"Wrote {result.line_count:,} lines to {args.output}")
    else:
        sys.stdout.write(result.text)
        if not result.text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.file and not args.gui:
        return run_cli(args)

    try:
        from gui import run
    except Exception as error:
        print(
            f"cannot start the GUI: {error}\n"
            "Hint: on Arch Linux install Tk with:  sudo pacman -S tk",
            file=sys.stderr,
        )
        return 1
    run(start_file=args.file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
