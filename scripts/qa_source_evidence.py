"""Trusted, hash-bound PDF evidence preparation outside the answer model.

Caches contain complete physical pages, never reader-authored paraphrases.
Prepared page images are separately registered without rewriting the content
author's asset registry. Nothing here opens URLs or accepts external paths.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from annotation_assets import MAX_IMAGE_BYTES, verify_image

EXTRACTION_VERSION = 1
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 1000
MAX_SOURCE_IMAGES = 8
MAX_REQUEST_PAGES = 3
MAX_REQUEST_TEXT_BYTES = 48000


def json_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def readable_value(value):
    """Select prose from legacy rich fields without copying component programs."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [readable_value(item) for item in value]
    if not isinstance(value, dict):
        return None
    fields = ("id", "type", "text", "text_zh", "text_en", "title", "title_cn", "label",
              "summary", "description", "plain_definition", "latex", "caption", "caption_zh",
              "caption_en", "alt", "inputs", "outputs", "meaning", "reason", "value")
    return {key: readable_value(value[key]) for key in fields if key in value}


def readable_block(block):
    """Only visible prose, formulas and figure identities cross the model wire."""
    fields = ("id", "type", "title", "title_cn", "text", "text_zh", "text_en", "latex",
              "label", "summary", "caption", "caption_zh", "caption_en", "alt", "src", "asset",
              "page", "source_refs", "section_source_refs", "evidence_kind", "formula_ref", "source_formula_id",
              "headers", "headers_zh", "headers_en", "rows", "rows_zh", "rows_en", "items",
              "algo_title", "lines", "algo_lines", "algo_lines_latex", "fallback_text", "aid_id")
    result = {key: block[key] if key in ("source_refs", "section_source_refs") else readable_value(block[key])
              for key in fields if key in block}
    if block.get("type") == "code" and isinstance(block.get("code"), str):
        result["code"] = block["code"]  # Escaped static code displayed by the reader, never component programs.
    # Components have readable labels/fallbacks; their datasets, state, HTML,
    # JavaScript and configuration are not part of the reading body.
    aid = block.get("aid")
    if isinstance(aid, dict):
        result["component"] = {key: aid[key] for key in ("id", "title", "fallback_text") if isinstance(aid.get(key), str)}
    return result


def with_section_sources(block, section):
    """Retain inherited evidence for current anchors and image/page selection."""
    refs = section.get("source_refs", [])
    if not isinstance(refs, list) or not refs:
        return block
    merged = list(block.get("source_refs", []))
    for reference in refs:
        if reference not in merged:
            merged.append(reference)
    return {**block, "section_source_refs": refs, "source_refs": merged}


def reader_body(data):
    """Flatten overview/explanation with stable parent IDs, excluding translation."""
    body = []
    for view in ("overview", "explanation"):
        for section in data.get(view, {}).get("sections", []):
            base = {"view": view, "section_id": section.get("id"),
                    "section_title": section.get("title_cn", section.get("title", section.get("title_en", "")))}
            def visit(blocks, parents=()):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    identity = block.get("id")
                    body.append({**base, **readable_block(with_section_sources(block, section)), "parent_ids": list(parents)})
                    if block.get("type") in ("details", "quote"):
                        visit(block.get("blocks", []), (*parents, identity))
                    learning = block.get("learning")
                    if isinstance(learning, dict) and learning.get("presentation") == "narrative":
                        visit(learning.get("blocks", []), (*parents, identity))
            visit(section.get("blocks", []))
    return body


def requested_pdf_evidence(directory, request, expected_sha256):
    """Bounded, exact current-PDF pages or literal-search excerpts, never paths."""
    if not isinstance(request, dict) or set(request) - {"pages", "query", "include_images"}:
        raise ValueError("source request accepts only pages, query and include_images")
    pages, query = request.get("pages", []), request.get("query", "")
    if (not isinstance(pages, list) or len(pages) > MAX_REQUEST_PAGES
            or any(type(page) is not int for page in pages)):
        raise ValueError("request at most three physical PDF pages")
    if not isinstance(query, str) or len(query) > 160:
        raise ValueError("source query must be a literal string of at most 160 characters")
    query = query.strip()
    if bool(pages) == bool(query):
        raise ValueError("request either specific physical pages or a literal query")
    if type(request.get("include_images", False)) is not bool:
        raise ValueError("include_images must be boolean")
    evidence = pdf_text_evidence(directory)
    if evidence.get("status") != "complete":
        raise ValueError("source.pdf is unavailable; do not infer missing original evidence")
    if not expected_sha256 or evidence["pdf_sha256"] != expected_sha256:
        raise ValueError("source.pdf changed since this question; no mismatched source evidence supplied")
    if any(not 1 <= page <= evidence["page_count"] for page in pages):
        raise ValueError("source request page outside current verified PDF")
    excerpts = []
    for original in evidence["text_pages"]:
        text = original["text"]
        if pages:
            if original["page"] not in pages:
                continue
            start, end, coverage = 0, len(text), "complete physical page"
        else:
            # Literal matches preserve original characters and exact offsets.
            match = re.search(re.escape(query), text, re.I)
            if not match:
                continue
            start, end, coverage = max(0, match.start() - 600), min(len(text), match.end() + 600), "literal-search excerpt"
        excerpt = text[start:end]
        excerpts.append({"source_id": "paper", "page": original["page"], "content_id": "", "url": "",
                         "label": "Original paper", "locator": original["locator"] + " chars " + str(start) + ":" + str(end),
                         "coverage": coverage, "start_char": start, "end_char": end,
                         "text": excerpt, "text_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest()})
        if query and len(excerpts) == MAX_REQUEST_PAGES:
            break
    result = {"pdf_sha256": expected_sha256, "source_id": "paper", "text_pages": excerpts,
              "status": "verified_subset" if excerpts else "no_literal_match",
              "ocr_performed": False, "images": []}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_REQUEST_TEXT_BYTES:
        raise ValueError("requested pages exceed the 48KB evidence bound; request a literal excerpt or fewer pages")
    if request.get("include_images"):
        result["images"] = [_page_image(Path(directory), evidence, item["page"]) for item in excerpts]
    if digest_file(confined(directory, "source.pdf")) != expected_sha256:
        raise ValueError("source.pdf changed during source request")
    return result


def confined(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or ":" in relative or Path(relative).is_absolute():
        raise ValueError("evidence path must be relative to this paper")
    path = (root / relative.replace("\\", "/")).resolve()
    if not path.is_relative_to(root):
        raise ValueError("evidence path outside paper directory")
    return path


def digest_file(path):
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def pdf_text_evidence(directory):
    """Missing PDF is explicit for legitimate teaching fixtures; bad PDFs fail."""
    directory = Path(directory).resolve()
    source = confined(directory, "source.pdf")
    if not source.exists():
        return {"status": "unavailable", "reason": "source.pdf is absent; supplied reader content is not a verified PDF transcription",
                "page_count": 0, "covered_pages": [], "text_pages": [], "empty_text_pages": []}
    if source.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("source.pdf exceeds the 100 MiB extraction limit")
    digest = digest_file(source)
    cache = confined(directory, "intermediate/qa-evidence/text-" + digest + ".json")
    try:
        value = json.loads(cache.read_text(encoding="utf-8"))
        pages = value["text_pages"]
        page_count = value["page_count"]
        valid = (value.get("version") == EXTRACTION_VERSION and value.get("pdf_sha256") == digest
                 and type(page_count) is int and 0 < page_count <= MAX_PDF_PAGES
                 and isinstance(pages, list) and len(pages) == page_count
                 and all(isinstance(page, dict) and page.get("page") == i + 1
                         and isinstance(page.get("text"), str) for i, page in enumerate(pages)))
        if not valid:
            raise ValueError("invalid evidence cache")
    except (OSError, ValueError, KeyError, TypeError):
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError as exc:
                raise ValueError("PyMuPDF is required to verify the complete source.pdf evidence") from exc
        with fitz.open(source) as document:
            if document.needs_pass or not 0 < len(document) <= MAX_PDF_PAGES:
                raise ValueError("source.pdf must be readable and contain 1..1000 physical pages")
            pages = [{"source_id": "paper", "page": i + 1,
                      "locator": "source.pdf physical page " + str(i + 1),
                      "text": document[i].get_text("text", sort=True)} for i in range(len(document))]
            value = {"version": EXTRACTION_VERSION, "pdf_sha256": digest, "page_count": len(document), "text_pages": pages}
        if digest_file(source) != digest:
            raise ValueError("source.pdf changed during evidence extraction; retry with the current PDF")
        atomic_json(cache, value)
    if digest_file(source) != digest:
        raise ValueError("source.pdf changed during evidence verification")
    return {**value, "status": "complete", "extraction": "PyMuPDF text extraction; page images are authoritative for layout/math",
            "covered_pages": list(range(1, value["page_count"] + 1)),
            "empty_text_pages": [page["page"] for page in value["text_pages"] if not page["text"].strip()],
            "ocr_performed": False}


def walk_blocks(blocks):
    for block in blocks:
        if not isinstance(block, dict):
            continue
        yield block
        if isinstance(block.get("blocks"), list):
            yield from walk_blocks(block["blocks"])
        learning = block.get("learning")
        if isinstance(learning, dict) and isinstance(learning.get("blocks"), list):
            yield from walk_blocks(learning["blocks"])


def _registry(directory):
    path = confined(directory, "asset-registry.json")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = raw if isinstance(raw, list) else raw.get("assets", []) if isinstance(raw, dict) else []
    return {item["src"]: item for item in entries if isinstance(item, dict) and isinstance(item.get("src"), str)}


def _page_image(directory, source, page):
    """Render one original physical page and bind the registered bitmap to PDF."""
    if type(page) is not int or not 1 <= page <= source.get("page_count", 0):
        raise ValueError("source image page outside verified PDF")
    digest = source["pdf_sha256"]
    prefix = "assets/qa-source-pages/" + digest + "-p" + str(page)
    manifest = confined(directory, "intermediate/qa-evidence/page-images-" + digest + ".json")
    try:
        registry = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        registry = {"pdf_sha256": digest, "images": {}}
    if registry.get("pdf_sha256") != digest or not isinstance(registry.get("images"), dict):
        registry = {"pdf_sha256": digest, "images": {}}
    existing = registry["images"].get(str(page))
    if isinstance(existing, dict) and existing.get("src") == prefix + ".png":
        path = confined(directory, existing["src"])
        if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES and digest_file(path) == existing.get("sha256"):
            verify_image(path.read_bytes(), "image/png")
            return existing
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    pdf = confined(directory, "source.pdf")
    if digest_file(pdf) != digest:
        raise ValueError("source.pdf changed before page rendering")
    with fitz.open(pdf) as document:
        original = document[page - 1]
        for scale in (1.5, 1.1, .8):
            pixels = original.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
            if len(pixels) <= MAX_IMAGE_BYTES:
                verify_image(pixels, "image/png")
                break
        else:
            raise ValueError("original page image exceeds the verified image input limit")
    if digest_file(pdf) != digest:
        raise ValueError("source.pdf changed during page rendering")
    path = confined(directory, prefix + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(pixels)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    image = {"src": path.relative_to(directory).as_posix(), "sha256": hashlib.sha256(pixels).hexdigest(),
             "media_type": "image/png", "pdf_sha256": digest, "page": page,
             "source_id": "paper", "kind": "original_pdf_page",
             "label": "source.pdf physical page " + str(page), "render_scale": scale}
    registry["images"][str(page)] = image
    atomic_json(manifest, registry)
    return image


def selected_images(directory, source, selected_blocks, all_blocks, payload):
    """Select current anchors and explicit original figure/equation references."""
    directory = Path(directory).resolve()
    registry = _registry(directory)
    selected = list(selected_blocks)
    question = " ".join(str(payload.get(key, "")) for key in ("user_note", "question", "selected_text"))
    # Labels on the anchored current block are trusted source links, whereas
    # arbitrary user-supplied disk paths are never used to locate evidence.
    question += " " + " ".join(str(ref.get("label", "")) for block in selected_blocks
                               for ref in block.get("source_refs", []) if isinstance(ref, dict))
    figure_numbers = set(re.findall(r"(?:\bfigure\s*|\bfig\.?\s*|图\s*)(\d+)(?!\d)", question, re.I))
    equation_numbers = set(re.findall(r"(?:\bequation\s*|\beq\.?\s*|公式\s*|式\s*)[（(]?(\d+)(?!\d)", question, re.I))
    matched_figure_numbers = set()
    for block in selected:
        src = block.get("src", block.get("asset"))
        if block.get("type") in ("figure", "diagram") and type(registry.get(src, {}).get("page")) is int:
            labels = " ".join(str(block.get(k, "")) for k in ("caption", "caption_en", "caption_zh", "alt", "label"))
            matched_figure_numbers.update(re.findall(r"(?:\bfigure\s*|\bfig\.?\s*|图\s*)(\d+)(?!\d)", labels, re.I))
    for block in all_blocks:
        if block.get("type") in ("figure", "diagram") and figure_numbers:
            label = " ".join(str(block.get(k, "")) for k in ("caption", "caption_en", "caption_zh", "alt", "label"))
            numbers = set(re.findall(r"(?:\bfigure\s*|\bfig\.?\s*|图\s*)(\d+)(?!\d)", label, re.I))
            if numbers & (figure_numbers - matched_figure_numbers):
                selected.append(block)
                matched_figure_numbers.update(numbers & figure_numbers)
        if block.get("type") == "formula" and equation_numbers:
            labels = " ".join(str(block.get(k, "")) for k in ("label", "text_en", "text_zh"))
            if any(re.search(r"[（(]" + re.escape(n) + r"[）)]", labels) for n in equation_numbers):
                selected.append(block)
    images, metadata, seen_src, page_requests = [], [], set(), set()
    for block in selected:
        src = block.get("src", block.get("asset"))
        if block.get("type") in ("figure", "diagram") and isinstance(src, str) and src not in seen_src:
            seen_src.add(src)
            registered = registry.get(src)
            if registered:
                path = confined(directory, src)
                expected = registered.get("sha256")
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    metadata.append({"src": src, "reason": "registered asset has no verified SHA-256"})
                elif not path.is_file() or digest_file(path) != expected:
                    raise ValueError("selected registered image changed or is missing: " + src)
                else:
                    images.append({**registered, "content_id": block.get("id"),
                                   "caption": block.get("caption_zh", block.get("caption_en", block.get("caption", "")))})
            else:
                metadata.append({"src": src, "reason": "image is not in the trusted asset registry"})
        needs_page = block.get("type") == "formula" or (block.get("type") in ("figure", "diagram") and (not src or src not in registry))
        if needs_page:
            refs = block.get("source_refs", [])
            pages = [ref.get("page") for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []
            pages.append(block.get("page"))
            for page in pages:
                if type(page) is int:
                    if source.get("status") == "complete" and not 1 <= page <= source["page_count"]:
                        raise ValueError("selected source reference is outside the current PDF")
                    page_requests.add(page)
    if source.get("status") == "complete":
        for page in sorted(page_requests):
            if len(images) >= MAX_SOURCE_IMAGES:
                metadata.append({"page": page, "reason": "source-image per-turn limit; complete page text is still supplied"})
                continue
            images.append(_page_image(directory, source, page))
    elif page_requests:
        metadata.append({"pages": sorted(page_requests), "reason": "source.pdf is absent; no original page pixels prepared"})
    if len(images) > MAX_SOURCE_IMAGES:
        metadata.extend({"src": item["src"], "reason": "source-image per-turn limit"} for item in images[MAX_SOURCE_IMAGES:])
        images = images[:MAX_SOURCE_IMAGES]
    return images, metadata
