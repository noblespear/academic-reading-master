#!/usr/bin/env python3
"""Extract a reproducible page/column reading view without altering the PDF.

The original positioned spans and page renders remain available for checking
equations, tables, and column ordering; plain text is never treated as a
faithful replacement for these visual elements.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata


def normalized(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=[A-Za-z])-\n\s*(?=[a-z])", "", text)
    return text


def extract(pdf_path, output_dir, scale=1.5, two_column_pages=None,
            margin=0.0, footer_margin=0.0, first_page_top=0.0):
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as error:
            raise SystemExit("PyMuPDF is required: use a Python environment with pymupdf installed") from error
    pdf_path, output_dir = Path(pdf_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    index = {"source_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
             "page_count": len(doc), "pages": []}
    for number, page in enumerate(doc, 1):
        page_id = f"p{number:03d}"
        width, height = page.rect.width, page.rect.height
        words = page.get_text("words")
        middle = width / 2
        crossing = sum(word[0] < middle < word[2] for word in words)
        left = sum(word[2] <= middle for word in words)
        right = sum(word[0] >= middle for word in words)
        two_columns = (number in two_column_pages if two_column_pages is not None
                       else len(words) > 300 and min(left, right) > len(words) * .25
                       and crossing < len(words) * .02)
        top = first_page_top if number == 1 else margin
        bottom = height - footer_margin
        clips = ([fitz.Rect(margin, top, middle, bottom),
                  fitz.Rect(middle + 3, top, width - margin, bottom)]
                 if two_columns else [page.rect])
        columns = []
        for col, clip in enumerate(clips, 1):
            value = normalized(page.get_text("text", clip=clip, sort=True))
            columns.append({"column": col, "bbox": list(clip), "text": value})
            (output_dir / f"{page_id}-col{col}.txt").write_text(value, encoding="utf-8")
        raw = page.get_text("dict")
        positioned = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            positioned.append({"bbox": block["bbox"], "lines": [
                {"bbox": line["bbox"], "spans": [
                    {key: span.get(key) for key in ("text", "bbox", "font", "size", "flags")}
                    for span in line.get("spans", [])]}
                for line in block.get("lines", [])]})
        data = {"page": number, "page_size": [width, height], "columns": columns,
                "positioned_blocks": positioned,
                "warning": "Equations use legacy font encodings; verify symbols against the rendered page."}
        (output_dir / f"{page_id}-source.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        page.get_pixmap(matrix=fitz.Matrix(scale, scale)).save(output_dir / f"{page_id}.png")
        index["pages"].append({"page": number, "words": len(page.get_text("words")),
                               "characters": len(page.get_text()), "column_count": len(columns)})
    (output_dir / "extraction-index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("output_dir")
    parser.add_argument("--scale", type=float, default=1.5)
    parser.add_argument("--two-column-pages", help="Explicit page numbers/ranges, e.g. 1-8,10; otherwise detect columns")
    parser.add_argument("--margin", type=float, default=0)
    parser.add_argument("--footer-margin", type=float, default=0)
    parser.add_argument("--first-page-top", type=float, default=0)
    args = parser.parse_args()
    page_numbers = None
    if args.two_column_pages is not None:
        page_numbers = set()
        for item in args.two_column_pages.split(","):
            parts = item.split("-")
            if len(parts) == 1:
                page_numbers.add(int(parts[0]))
            elif len(parts) == 2:
                page_numbers.update(range(int(parts[0]), int(parts[1]) + 1))
            else:
                parser.error("Invalid page range")
    index = extract(args.pdf, args.output_dir, args.scale, page_numbers,
                    args.margin, args.footer_margin, args.first_page_top)
    print(json.dumps({"page_count": index["page_count"], "source_sha256": index["source_sha256"]}))


if __name__ == "__main__":
    main()
