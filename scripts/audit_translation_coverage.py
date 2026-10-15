#!/usr/bin/env python3
"""Read-only PDF/text_en coverage candidates for human review, never accuracy certification.

Exit 0: no candidates found (NOT full-translation approval); 1: needs review;
2: input/extraction error. All top-level sections are translation content;
overview and explanation are excluded. Extractable English captions count,
but image pixels do not.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata


def normalize(text):
    # Comparison only: never rewrite the PDF, translation, or report excerpts.
    # Some PDF fonts emit a spacing diaeresis in Bj¨orn/M¨uller while the
    # translation contains the precomposed ö/ü. Canonical decomposition gives
    # both forms the same base letters without splitting a person's name.
    text = re.sub(r"(?<=[A-Za-z])\s*[¨´ˇ˘ˆ˚]\s*(?=[A-Za-z])", "", text)
    text = unicodedata.normalize("NFKD", text).replace("\u00ad", "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"(?<=[A-Za-z])[-‐‑–]\s*\n\s*(?=[A-Za-z])", "", text)
    # Treat spelling variants such as text-to-image / texttoimage alike.
    text = re.sub(r"(?<=[A-Za-z])[-‐‑–_](?=[A-Za-z])", "", text)
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower().replace("’", "'"))


def grams(tokens):
    return set(tuple(tokens[i:i + 3]) for i in range(len(tokens) - 2))


def load_data(path):
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if text.startswith("window.PAPER_DATA"):
        text = text.split("=", 1)[1].strip().rstrip(";")
    data = json.loads(text)
    entries = []

    def english_strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for item in value for text in english_strings(item)]
        return []

    for section in data.get("sections", []):
        for block in section.get("blocks", []):
            texts = [text for field in ("text_en", "caption_en", "headers_en", "rows_en")
                     for text in english_strings(block.get(field)) if text.strip()]
            if not texts:
                continue
            pages = {r["page"] for r in block.get("source_refs", [])
                     if isinstance(r.get("page"), int)}
            if not pages and isinstance(section.get("coverage", {}).get("page"), int):
                pages.add(section["coverage"]["page"])
            entries.append({"id": block.get("id"), "pages": pages,
                            "text": " ".join(texts),
                            "tokens": normalize(" ".join(texts)),
                            "grams": grams(normalize(" ".join(texts)))})
    return entries


def _translation_boundaries(entries, pages):
    """Match continuous English split into adjacent translated blocks.

    Boundary spans carry only the union of the two actual source-page lists,
    never the entire neighboring block. Joined words need PDF context support.
    """
    source_grams = set().union(*(grams(normalize(block[4])) for blocks in pages for block in blocks))
    boundaries, notes = [], []
    for left, right in zip(entries, entries[1:]):
        if not left["tokens"] or not right["tokens"]:
            continue
        tail = re.search(r"([A-Za-z]+)[-‐‑–]\s*$", left["text"])
        head = re.match(r"\s*([A-Za-z]+)\b", right["text"])
        word = None
        if tail and head:
            word = tail[1] + head[1]
            context = grams(normalize(left["text"][:tail.start()])[-2:] + normalize(word) +
                normalize(right["text"][head.end():])[:2])
            if not context or not context <= source_grams:
                continue
        else:
            if re.search(r"[.!?;]\s*$", left["text"]):
                continue
            context = grams(left["tokens"][-2:] + right["tokens"][:2]) & source_grams
            if not context:
                continue
        ids = [left["id"], right["id"]]
        boundaries.append({"id": "boundary:" + str(left["id"]) + ":" + str(right["id"]),
            "pages":left["pages"] | right["pages"], "grams":context, "joined_blocks":ids})
        notes.append({"kind":"translation_block_boundary", "matching_blocks":ids,
            "source_pages":sorted(left["pages"] | right["pages"]), "comparison_word":word})
    return boundaries, notes


def _repair_block_boundaries(pages, entries):
    """Rejoin an attested line-end word across PDF blocks or physical pages.

    Require the full surrounding three-gram context in a translation block.
    Ambiguous pairings stay unchanged and therefore remain review candidates.
    Source-page attribution is still checked separately at the same 90% bar.
    """
    notes = []
    proposals = []
    for page_index, blocks in enumerate(pages):
        for block_index, block in enumerate(blocks):
            tail = re.search(r"([A-Za-z]+)[-‐‑–]\s*$", block[4])
            if not tail:
                continue
            neighbors = []
            if block_index + 1 < len(blocks):
                neighbors.append((page_index, block_index + 1, blocks[block_index + 1]))
            # A next page may begin with a figure or running header. Context,
            # rather than extraction order alone, must identify continuation.
            if page_index + 1 < len(pages):
                neighbors.extend((page_index + 1, i, item) for i, item in enumerate(pages[page_index + 1]))
            for next_page, next_index, following in neighbors:
                head = re.match(r"\s*([A-Za-z]+)\b", following[4])
                if not head:
                    continue
                joined = tail[1] + head[1]
                left = normalize(block[4][:tail.start()])[-2:]
                right = normalize(following[4][head.end():])[:2]
                context = grams(left + normalize(joined) + right)
                if len(left) < 2 or len(right) < 2 or not context:
                    continue
                matching = [entry for entry in entries if context <= entry["grams"]]
                if matching:
                    proposals.append(((page_index, block_index), (next_page, next_index), tail, head, joined, matching))
    outgoing, incoming = {}, {}
    for proposal in proposals:
        outgoing.setdefault(proposal[0], []).append(proposal)
        incoming.setdefault(proposal[1], []).append(proposal)
    repairs = {}
    for previous, following, tail, head, joined, matches in proposals:
        if len(outgoing[previous]) != 1 or len(incoming[following]) != 1:
            continue
        repairs.setdefault(previous, []).append((tail.start(), tail.end(), joined))
        repairs.setdefault(following, []).append((head.start(), head.end(), joined))
        notes.append({"kind":"attested_boundary_word", "source_pages":sorted({previous[0]+1, following[0]+1}),
            "source_blocks":[previous[1]+1, following[1]+1], "comparison_word":joined,
            "matching_blocks":[entry["id"] for entry in matches]})
    normalized = {}
    for position, replacements in repairs.items():
        text = pages[position[0]][position[1]][4]
        for start, end, replacement in sorted(replacements, reverse=True):
            text = text[:start] + replacement + text[end:]
        normalized[position] = text
    return normalized, notes


def audit(pdf_path, data_path):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    entries = load_data(data_path)
    candidates, skipped, pages_without_text = [], [], []
    checked = 0
    with fitz.open(pdf_path) as doc:
        page_count = len(doc)
        pages = [[b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()] for page in doc]
        boundaries, boundary_notes = _translation_boundaries(entries, pages)
        matching_entries = entries + boundaries
        global_grams = set().union(*(e["grams"] for e in matching_entries))
        comparisons, normalizations = _repair_block_boundaries(pages, matching_entries)
        normalizations.extend(boundary_notes)
        for page_number, text_blocks in enumerate(pages, 1):
            local_grams = set().union(*(e["grams"] for e in matching_entries if page_number in e["pages"]))
            if not text_blocks:
                pages_without_text.append(page_number)
            for block_index, block in enumerate(text_blocks, 1):
                # Keep physical block coordinates; split sentences, not extraction lines,
                # so wrapped prose and hyphenated words are compared continuously.
                original_parts = re.split(r"(?<=[.!?;])\s+", block[4])
                text = comparisons.get((page_number-1, block_index-1), block[4])
                for part_index, part in enumerate(re.split(r"(?<=[.!?;])\s+", text), 1):
                    tokens = normalize(part)
                    unit = {"id": f"p{page_number:03d}-b{block_index:03d}-u{part_index:03d}",
                            "page": page_number, "bbox": list(block[:4]), "text": original_parts[part_index-1].strip()}
                    letters = sum(c.isalpha() and c.isascii() for c in part)
                    visible = sum(not c.isspace() for c in part)
                    formula_like = "=" in part and len(tokens) < 20
                    numeric_table = len(re.findall(r"\d+(?:\.\d+)?", part)) >= max(3, len(tokens) / 2)
                    if (len(tokens) < 3 or letters < 12 or letters / max(visible, 1) < .45
                            or formula_like or numeric_table):
                        skipped.append({**unit, "reason": "short_label_number_or_formula_like"})
                        continue
                    checked += 1
                    source_grams = grams(tokens)
                    found = source_grams & global_grams
                    score = len(found) / len(source_grams)
                    local_score = len(source_grams & local_grams) / len(source_grams)
                    if score >= .90 and local_score >= .90:
                        continue
                    if score >= .90:
                        kind = "source_page_mismatch"
                    else:
                        kind = "unmatched_text" if not found else "partial_coverage"
                    matches = sorted(matching_entries, key=lambda e: len(source_grams & e["grams"]), reverse=True)
                    matches = [e for e in matches[:3] if source_grams & e["grams"]]
                    missing = [" ".join(g) for g in sorted(source_grams - global_grams)]
                    candidates.append({**unit, "kind": kind, "ngram_coverage": round(score, 4),
                                       "same_page_coverage": round(local_score, 4),
                                       "missing_trigrams": missing,
                                       "matching_blocks": [{"id": e["id"], "source_pages": sorted(e["pages"]),
                                           **({"joined_blocks":e["joined_blocks"]} if "joined_blocks" in e else {})}
                                                           for e in matches]})
    needs_review = bool(candidates or pages_without_text or not checked or not entries)
    return {
        "schema_version": 1,
        "status": "needs_review" if needs_review else "no_candidates_found",
        "notice": "Candidates require human PDF review. No candidates does not certify complete or accurate translation.",
        "source_pdf": str(Path(pdf_path).resolve()),
        "source_pdf_sha256": hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest(),
        "data_path": str(Path(data_path).resolve()),
        "data_sha256": hashlib.sha256(Path(data_path).read_bytes()).hexdigest(),
        "method": "English normalized trigram coverage across all top-level sections: text_en, caption_en, headers_en and rows_en per block plus PDF-attested adjacent boundary spans; candidate below 90% or matched outside source page.",
        "comparison_normalizations": normalizations,
        "comparison_notice":"Unicode/diacritics, Latin hyphens/subscript spelling and attested boundary words are normalized only for matching. Original excerpts and both input files remain unchanged; 90% and physical-page attribution are retained.",
        "limits": ["Only extractable English text is checked; no OCR or image-only labels.",
                   "Short labels, isolated numbers and formula-like units are skipped and listed.",
                   "Chinese accuracy, formulas, numeric tables, styling, links and reading order are not certified.",
                   "English captions/rows/headers in top-level sections count; overview/explanation and image pixels never count.",
                   "Reordered phrases, paraphrases, citations and extraction artifacts can cause false positives; repeated phrases can hide omissions."],
        "page_count": page_count, "checked_units": checked,
        "translation_text_blocks": len(entries), "pages_without_extractable_text": pages_without_text,
        "candidate_count": len(candidates), "candidates": candidates,
        "skipped_unit_count": len(skipped), "skipped_units": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True, help="JSON candidate report; never modifies PDF or reader data")
    args = parser.parse_args()
    output = Path(args.output)
    if output.resolve() in {Path(args.pdf).resolve(), Path(args.data).resolve()}:
        parser.error("--output must differ from both input files")
    try:
        report = audit(args.pdf, args.data)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        print(f"Coverage audit error: {error}")
        return 2
    print(json.dumps({"status": report["status"], "candidate_count": report["candidate_count"],
                      "notice": report["notice"]}))
    return 1 if report["status"] == "needs_review" else 0


if __name__ == "__main__":
    raise SystemExit(main())
