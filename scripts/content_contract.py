"""Additive Reader content contracts 3.1/3.2. Standard library only; no execution.

Public functions return (errors, warnings) with stable JSON field paths. Legacy
papers without a content_contract_version are not subjected to new authoring
requirements. Rich documents always use the same bounded, safe block contract.
"""
from pathlib import Path, PurePosixPath
import json
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

CONTRACT_VERSION = "3.2"
SUPPORTED_CONTRACT_VERSIONS = frozenset({"3.1", "3.2"})
RICH_TYPES = frozenset({"paragraph", "para", "heading", "list", "figure", "diagram",
    "formula", "table", "algorithm", "code", "quote", "details", "learning_aid", "separator"})
AID_TYPES = frozenset({"concept_cards", "formula_explorer", "steps", "parameter_explorer", "prediction", "lru_cache", "graph_walkthrough", "comparison_chart", "weighted_degree", "method_pipeline"})
EVIDENCE_KINDS = frozenset({"paper", "translation", "general_background", "ai_explanation", "teaching_example"})
FACTUAL_GATES = ("source_accuracy", "evidence_separation", "claim_scope", "prerequisites",
    "formula_correctness", "analogy_and_example")
MAX_DEPTH, MAX_BLOCKS, MAX_AST_DEPTH, MAX_AST_NODES = 12, 1000, 12, 128
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_DANGEROUS_KEY = frozenset({"script", "javascript", "eval", "onclick", "onload", "onerror", "iframe", "html"})
_AID_ALIASES = {"concept_card":"concept_cards", "step_walkthrough":"steps", "cache_walkthrough":"lru_cache"}


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def is_translation_deferred(data):
    """Only an explicit deferred marker opts out of completed translation."""
    translation = data.get("translation") if isinstance(data, dict) else None
    return isinstance(translation, dict) and translation.get("status") == "deferred"


def _translation_state(data, errors, preview):
    """Validate explicit opt-in without changing unmarked legacy validation."""
    translation = data.get("translation")
    status = None
    if "translation" in data:
        if not isinstance(translation, dict) or translation.get("status") not in ("deferred", "ready"):
            errors.append("translation.status: must be deferred or ready")
        else:
            status = translation["status"]
    sections = data.get("sections", [])
    if status == "deferred":
        if sections != []:
            errors.append("sections: deferred translation requires an empty array")
    elif status == "ready" or (data.get("content_contract_version") == "3.2" and not preview):
        if not isinstance(sections, list) or not sections:
            errors.append("sections: completed translation requires non-empty sections; use translation.status deferred to defer it")
    return status


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _text(value, path, errors, required=True):
    if required and not _string(value):
        errors.append(path + ": must be non-empty text")
    elif value is not None and not isinstance(value, str):
        errors.append(path + ": must be text")
    elif isinstance(value, str) and _HTML.search(value):
        errors.append(path + ": raw HTML is not supported; use content blocks; for literal angle-bracket tokens use LaTeX \\lt and \\gt")


def _safe_tree(value, path, errors, depth=0):
    """Reject executable fields/HTML and non-JSON, non-finite payloads everywhere."""
    if depth > 32:
        errors.append(path + ": JSON nesting exceeds 32")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(path + ": object keys must be strings")
                continue
            if key.lower() in _DANGEROUS_KEY or key.lower().startswith("on") and key.lower() in {"onmouseover", "onfocus", "onpointerdown"}:
                errors.append(path + "." + key + ": executable/HTML field is not allowed")
            _safe_tree(item, path + "." + key, errors, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            errors.append(path + ": array exceeds 10000 entries")
            return
        for i, item in enumerate(value):
            _safe_tree(item, f"{path}.{i}", errors, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        errors.append(path + ": number must be finite")
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        errors.append(path + ": not a JSON value")


def _id(value, path, errors, seen=None):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        errors.append(path + ": invalid stable ID")
    elif seen is not None:
        if value in seen:
            errors.append(path + ": duplicate ID " + value)
        seen.add(value)


def _local_asset(src, path, errors, paper_dir=None):
    if not isinstance(src, str) or not src.strip():
        errors.append(path + ": local asset path is required")
        return
    # Both platform separators are checked even on POSIX test hosts. Reject
    # encoded traversal rather than relying on a future URL decode order.
    normalized = src.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (normalized.startswith(("/", "//")) or ":" in normalized or "?" in normalized
            or "#" in normalized or "%" in normalized or any(p in (".", "..") or p.startswith(".") for p in parts)
            or not parts or normalized.endswith("/")):
        errors.append(path + ": asset must be a confined relative path")
        return
    if PurePosixPath(normalized).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        errors.append(path + ": unsupported image asset extension")
        return
    if paper_dir is not None:
        base = Path(paper_dir).resolve()
        target = (base / Path(*parts)).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            errors.append(path + ": asset escapes paper directory")
            return
        if not target.is_file():
            errors.append(path + ": asset does not exist: " + src)
        elif target.suffix.lower() == ".svg":
            try:
                source = target.read_text(encoding="utf-8")
                if len(source) > 1024 * 1024 or "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
                    raise ValueError("oversized or entity-bearing SVG")
                root = ET.fromstring(source)
                if root.tag.rsplit("}", 1)[-1].lower() != "svg":
                    raise ValueError("not an SVG root")
                allowed = {"svg","g","path","rect","circle","ellipse","line","polyline","polygon","text","tspan","defs","marker","title","desc","clipPath"}
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1] not in allowed:
                        name = element.tag.rsplit("}", 1)[-1]
                        raise ValueError("unsupported SVG element: " + name + ("; use presentation attributes instead of a style element" if name == "style" else ""))
                    for key, value in element.attrib.items():
                        name = key.rsplit("}", 1)[-1].lower()
                        if name.startswith("on") or name in {"href","src"} or "javascript:" in value.lower() or re.search(r"url\(\s*[^#]", value, re.I):
                            raise ValueError("unsafe SVG attribute")
            except (ValueError, ET.ParseError, OSError, UnicodeError) as error:
                errors.append(path + ": SVG must contain only static safe primitives (" + str(error) + ")")


def _latex(value, path, errors, allow_multiline=True):
    _text(value, path, errors)
    if not isinstance(value, str):
        return
    # Formula blocks may span lines; symbol labels must stay on one line.
    # Check decoded characters, including accidental Python escape sequences.
    allowed_controls = "\t\n\r" if allow_multiline else ""
    bad_controls = sorted({ord(char) for char in value
        if ord(char) < 32 and char not in allowed_controls})
    if bad_controls:
        codes = ", ".join(f"U+{code:04X}" for code in bad_controls)
        errors.append(path + ": invalid LaTeX control character(s): " + codes)
    balance = 0
    for match in re.finditer(r"(?<!\\)[{}]", value):
        balance += 1 if match[0] == "{" else -1
        if balance < 0:
            break
    if balance:
        errors.append(path + ": unbalanced LaTeX braces")
    for left, right in ((r"\left", r"\right"), (r"\(", r"\)"), (r"\[", r"\]")):
        pattern = lambda command: re.escape(command) + (r"(?![A-Za-z])" if command in (r"\left", r"\right") else "")
        if len(re.findall(pattern(left), value)) != len(re.findall(pattern(right), value)):
            errors.append(path + ": unbalanced LaTeX delimiters")
    if re.search(r"\\(?:href|url|html\w*|includegraphics|input|write|def|newcommand)\b", value):
        errors.append(path + ": unsafe LaTeX command")


def _refs(refs, path, errors, known=None):
    if not isinstance(refs, list):
        errors.append(path + ": must be an array")
        return
    for i, ref in enumerate(refs):
        rp = f"{path}.{i}"
        if not isinstance(ref, dict):
            errors.append(rp + ": must be an object")
            continue
        if ref.get("page") is not None and (type(ref["page"]) is not int or ref["page"] < 1):
            errors.append(rp + ".page: must be a positive physical page")
        for key in ("block_id", "content_id"):
            if key in ref and ref[key] is not None and not isinstance(ref[key], str):
                errors.append(rp + "." + key + ": source block reference must be a string")
            elif ref.get(key) and known is not None and ref[key] not in known:
                errors.append(rp + "." + key + ": unknown block " + str(ref[key]))
        if ref.get("url") and not re.match(r"^https?://[^\s]+$", str(ref["url"])):
            errors.append(rp + ".url: only HTTP(S) source links are allowed")


def _ids(values, path, errors, known=None):
    if not isinstance(values, list):
        errors.append(path + ": must be an array of IDs")
        return
    seen = set()
    for i, item in enumerate(values):
        _id(item, f"{path}.{i}", errors, seen)
        if known is not None and isinstance(item, str) and item not in known:
            errors.append(f"{path}.{i}: unknown reference {item}")


def _ast(node, params, path, errors, depth=0, counter=None):
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > MAX_AST_DEPTH or counter[0] > MAX_AST_NODES:
        errors.append(path + ": expression exceeds depth/node limit")
        return
    if _number(node):
        return
    if not isinstance(node, dict):
        errors.append(path + ": expression must be a finite number or fixed arithmetic node")
        return
    if set(node) == {"parameter"}:
        if not isinstance(node["parameter"], str) or node["parameter"] not in params:
            errors.append(path + ".parameter: unknown parameter")
        return
    op, args = node.get("op"), node.get("args")
    if set(node) != {"op", "args"} or not isinstance(op, str) or op not in {"add", "subtract", "multiply", "divide", "power", "sum", "mean"}:
        errors.append(path + ": unknown arithmetic node (no custom code)")
        return
    if not isinstance(args, list) or not args or len(args) > 32:
        errors.append(path + ".args: requires 1..32 operands")
        return
    if op in ("subtract", "divide", "power") and len(args) != 2:
        errors.append(path + ".args: operation requires exactly two operands")
    for i, arg in enumerate(args):
        _ast(arg, params, f"{path}.args.{i}", errors, depth + 1, counter)


def _evaluate(node, params):
    """Only validator default-input checks; never executes supplied text."""
    if _number(node):
        return node
    if "parameter" in node:
        return params[node["parameter"]]
    values = [_evaluate(x, params) for x in node["args"]]
    op = node["op"]
    if op in ("sum", "add", "mean"):
        result = sum(values) / (len(values) if op == "mean" else 1)
    elif op == "subtract":
        result = values[0] - values[1]
    elif op == "multiply":
        result = math.prod(values)
    elif op == "divide":
        result = values[0] / values[1]
    else:
        if abs(values[1]) > 1024:
            raise ValueError("exponent exceeds 1024")
        result = values[0] ** values[1]
    if not _number(result):
        raise ValueError("result must be finite and real")
    return result


def _aid(aid, path, errors, warnings, paper_dir=None, catalog=None, depth=0):
    if not isinstance(aid, dict):
        errors.append(path + ": component must be an object")
        return
    if depth > MAX_DEPTH:
        errors.append(path + ": component nesting exceeds limit")
        return
    _id(aid.get("id"), path + ".id", errors)
    if type(aid.get("version")) is not int or aid["version"] != 1:
        errors.append(path + ".version: only component version 1 is supported")
    is_paper_visual = isinstance(aid.get("type"), str) and aid["type"] in {"comparison_chart", "method_pipeline"} and aid.get("evidence_kind") == "paper"
    if is_paper_visual:
        if not aid.get("source_refs"):
            errors.append(path + ".source_refs: paper results require explicit sources")
        _refs(aid.get("source_refs", []), path + ".source_refs", errors)
    elif aid.get("evidence_kind") != "teaching_example":
        errors.append(path + ".evidence_kind: component outputs must be marked teaching_example")
    raw_kind = aid.get("type")
    kind = _AID_ALIASES.get(raw_kind, raw_kind) if isinstance(raw_kind, str) else None
    if kind not in AID_TYPES:
        errors.append(path + ".type: unregistered learning component")
        return
    config = aid.get("config")
    if not isinstance(config, dict):
        errors.append(path + ".config: must be an object")
        return
    cp = path + ".config"
    if aid.get("fallback") is not None:
        _rich(aid["fallback"], path + ".fallback", errors, warnings, paper_dir, catalog, depth + 1)
    elif _string(aid.get("fallback_text")):
        _text(aid["fallback_text"], path + ".fallback_text", errors)
    else:
        errors.append(path + ".fallback: complete static rich fallback (or fallback_text) is required")
    if kind == "method_pipeline":
        _method_pipeline_config(config, cp, errors)
    elif kind == "weighted_degree":
        _weighted_degree_config(config, cp, errors)
    elif kind in ("graph_walkthrough", "comparison_chart"):
        _visual_config(kind, config, cp, errors)
    elif kind == "concept_cards":
        known = {x.get("id") for x in (catalog or {}).get("concepts", []) if isinstance(x, dict) and isinstance(x.get("id"), str)} if catalog is not None else None
        _ids(config.get("concept_ids"), cp + ".concept_ids", errors, known)
        if not config.get("concept_ids"):
            errors.append(cp + ".concept_ids: select at least one concept")
    elif kind == "steps":
        steps = config.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 200:
            errors.append(cp + ".steps: requires 1..200 steps")
        else:
            for i, step in enumerate(steps):
                sp = f"{cp}.steps.{i}"
                if not isinstance(step, dict):
                    errors.append(sp + ": must be an object")
                    continue
                _text(step.get("title"), sp + ".title", errors)
                if not _string(step.get("text")) and not step.get("blocks"):
                    errors.append(sp + ": requires text or blocks")
                if "text" in step:
                    _text(step["text"], sp + ".text", errors)
                if "blocks" in step:
                    _rich({"version":1,"blocks":step["blocks"]}, sp, errors, warnings, paper_dir, catalog, depth + 1)
            initial = config.get("initial_step", 0)
            if type(initial) is not int or not 0 <= initial < len(steps):
                errors.append(cp + ".initial_step: outside steps")
    elif kind in ("formula_explorer", "parameter_explorer"):
        params, defaults = set(), {}
        parameters = config.get("parameters")
        if not isinstance(parameters, list) or not 1 <= len(parameters) <= 8:
            errors.append(cp + ".parameters: requires 1..8 parameters")
            parameters = []
        for i, parameter in enumerate(parameters):
            pp = f"{cp}.parameters.{i}"
            if not isinstance(parameter, dict):
                errors.append(pp + ": must be an object")
                continue
            _id(parameter.get("id"), pp + ".id", errors, params)
            _text(parameter.get("label"), pp + ".label", errors)
            for field in ("min", "max", "step", "value"):
                if not _number(parameter.get(field)):
                    errors.append(pp + "." + field + ": finite number required")
            if all(_number(parameter.get(x)) for x in ("min", "max", "step", "value")):
                if not parameter["min"] <= parameter["value"] <= parameter["max"] or parameter["min"] >= parameter["max"] or parameter["step"] <= 0:
                    errors.append(pp + ": invalid range, step, or default")
                if parameter["step"] > parameter["max"] - parameter["min"]:
                    errors.append(pp + ".step: larger than parameter range")
                if isinstance(parameter.get("id"), str):
                    defaults[parameter["id"]] = parameter["value"]
        expression = config.get("expression")
        if expression is None and config.get("model") == "ratio":
            def operand(x):
                return {"parameter":x} if isinstance(x, str) else x
            expression = {"op":"divide","args":[operand(config.get("numerator")),operand(config.get("denominator"))]}
        before = len(errors)
        _ast(expression, params, cp + ".expression", errors)
        if len(errors) == before and len(defaults) == len(params):
            try:
                _evaluate(expression, defaults)
            except (ValueError, ZeroDivisionError, OverflowError, TypeError, KeyError):
                errors.append(cp + ".expression: invalid, non-finite or undefined result at default inputs")
        _text(config.get("result_label"), cp + ".result_label", errors)
        if "latex" in config:
            _latex(config["latex"], cp + ".latex", errors)
    elif kind == "prediction":
        _text(config.get("question"), cp + ".question", errors)
        choices = config.get("choices")
        ids = set()
        if not isinstance(choices, list) or not 2 <= len(choices) <= 20:
            errors.append(cp + ".choices: requires 2..20 choices")
        else:
            for i, choice in enumerate(choices):
                p = f"{cp}.choices.{i}"
                if not isinstance(choice, dict):
                    errors.append(p + ": must be an object")
                    continue
                _id(choice.get("id"), p + ".id", errors, ids)
                _text(choice.get("text", choice.get("label")), p + ".text", errors)
                _text(choice.get("feedback"), p + ".feedback", errors)
        correct = config.get("correct_choice_id", config.get("correct_id"))
        if not isinstance(correct, str) or correct not in ids:
            errors.append(cp + ".correct_choice_id: unknown choice")
    elif kind == "lru_cache":
        sequence = config.get("sequence", config.get("requests"))
        if not isinstance(sequence, list) or not 1 <= len(sequence) <= 200 or any(not _string(x) or len(x) > 100 for x in sequence):
            errors.append(cp + ".sequence: requires 1..200 short item names")
        lower, upper, value = config.get("min_capacity", 1), config.get("max_capacity", 8), config.get("capacity")
        if any(type(x) is not int for x in (lower, upper, value)) or not 1 <= lower <= value <= upper <= 64:
            errors.append(cp + ".capacity: integer capacities must satisfy 1 <= min <= value <= max <= 64")


def _method_pipeline_config(config, path, errors):
    """Bounded directed method map; layout and reading order are not execution."""
    allowed = {"summary", "nodes", "edges", "reading_order"}
    for key in config.keys() - allowed:
        errors.append(path + "." + str(key) + ": unsupported method_pipeline field")
    _text(config.get("summary"), path + ".summary", errors)
    if isinstance(config.get("summary"), str) and len(config["summary"]) > 4000:
        errors.append(path + ".summary: method summary exceeds 4000 characters")
    nodes, node_ids, cells = config.get("nodes"), set(), set()
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 40:
        errors.append(path + ".nodes: requires 2..40 stages")
        nodes = []
    for i, node in enumerate(nodes):
        np = f"{path}.nodes.{i}"
        if not isinstance(node, dict):
            errors.append(np + ": object required")
            continue
        for key in node.keys() - {"id", "label", "summary", "inputs", "outputs", "role", "why_needed", "handoff", "column", "row"}:
            errors.append(np + "." + str(key) + ": unsupported stage field")
        _id(node.get("id"), np + ".id", errors, node_ids)
        for key in ("label", "summary", "inputs", "outputs", "role", "why_needed", "handoff"):
            if key in {"role", "why_needed", "handoff"} and key not in node:
                continue
            _text(node.get(key), np + "." + key, errors)
            if isinstance(node.get(key), str) and len(node[key]) > (80 if key == "label" else 4000):
                errors.append(np + "." + key + ": stage text exceeds length limit")
        valid_cell = True
        for key, upper in (("column", 39), ("row", 39)):
            if type(node.get(key)) is not int or not 0 <= node[key] <= upper:
                errors.append(np + "." + key + f": integer grid position 0..{upper} required")
                valid_cell = False
        if valid_cell:
            cell = (node["column"], node["row"])
            if cell in cells:
                errors.append(np + ": stages cannot share a grid position")
            cells.add(cell)
    edges, edge_ids, pairs = config.get("edges"), set(), set()
    if not isinstance(edges, list) or not 1 <= len(edges) <= 120:
        errors.append(path + ".edges: requires 1..120 directed relationships")
        edges = []
    for i, edge in enumerate(edges):
        ep = f"{path}.edges.{i}"
        if not isinstance(edge, dict):
            errors.append(ep + ": object required")
            continue
        for key in edge.keys() - {"id", "from", "to", "kind", "label", "flow", "cause"}:
            errors.append(ep + "." + str(key) + ": unsupported relationship field")
        _id(edge.get("id"), ep + ".id", errors, edge_ids)
        for key in ("from", "to"):
            if not isinstance(edge.get(key), str) or edge[key] not in node_ids:
                errors.append(ep + "." + key + ": unknown stage")
        if isinstance(edge.get("from"), str) and isinstance(edge.get("to"), str):
            pair = (edge["from"], edge["to"])
            if pair in pairs or pair[0] == pair[1]:
                errors.append(ep + ": no self relationship or repeated directed pair")
            pairs.add(pair)
        if not isinstance(edge.get("kind"), str) or edge["kind"] not in {"flow", "branch", "feedback", "dependency"}:
            errors.append(ep + ".kind: flow, branch, feedback or dependency required")
        _text(edge.get("label"), ep + ".label", errors)
        if isinstance(edge.get("label"), str) and len(edge["label"]) > 240:
            errors.append(ep + ".label: relationship text exceeds 240 characters")
        for key in ("flow", "cause"):
            _text(edge.get(key), ep + "." + key, errors)
            if isinstance(edge.get(key), str) and len(edge[key]) > 4000:
                errors.append(ep + "." + key + ": relationship explanation exceeds 4000 characters")
    if "reading_order" in config:
        _ids(config["reading_order"], path + ".reading_order", errors, node_ids)
        if isinstance(config["reading_order"], list) and set(x for x in config["reading_order"] if isinstance(x, str)) != node_ids:
            errors.append(path + ".reading_order: must contain every stage exactly once; only controls reading navigation")


def _weighted_degree_config(config, path, errors):
    """One small, finite degree calculation; no configurable code or ranking rule."""
    allowed = {"nodes", "edges", "adjustable_edge_id", "selected_node_id", "initial_mode", "tie_break"}
    for key in config.keys() - allowed:
        errors.append(path + "." + str(key) + ": unsupported weighted_degree field")
    nodes, node_ids = config.get("nodes"), set()
    if not isinstance(nodes, list) or len(nodes) != 4:
        errors.append(path + ".nodes: requires exactly four nodes")
        nodes = []
    for i, node in enumerate(nodes):
        np = f"{path}.nodes.{i}"
        if not isinstance(node, dict):
            errors.append(np + ": object required")
            continue
        for key in node.keys() - {"id", "label"}:
            errors.append(np + "." + str(key) + ": unsupported node field")
        _id(node.get("id"), np + ".id", errors, node_ids)
        _text(node.get("label"), np + ".label", errors)
        if isinstance(node.get("label"), str) and len(node["label"]) > 12:
            errors.append(np + ".label: use at most 12 characters for the small graph")
    edges, edge_ids, pairs, adjustable = config.get("edges"), set(), set(), None
    if not isinstance(edges, list) or not 1 <= len(edges) <= 6:
        errors.append(path + ".edges: requires 1..6 edges")
        edges = []
    for i, edge in enumerate(edges):
        ep = f"{path}.edges.{i}"
        if not isinstance(edge, dict):
            errors.append(ep + ": object required")
            continue
        for key in edge.keys() - {"id", "from", "to", "weight"}:
            errors.append(ep + "." + str(key) + ": unsupported edge field")
        _id(edge.get("id"), ep + ".id", errors, edge_ids)
        for endpoint in ("from", "to"):
            if not isinstance(edge.get(endpoint), str) or edge[endpoint] not in node_ids:
                errors.append(ep + "." + endpoint + ": unknown node")
        if all(isinstance(edge.get(k), str) for k in ("from", "to")):
            pair = tuple(sorted((edge["from"], edge["to"])))
            if edge["from"] == edge["to"] or pair in pairs:
                errors.append(ep + ": no self edge or repeated node pair")
            pairs.add(pair)
        weight = edge.get("weight")
        if not _number(weight) or not 0 <= weight <= 1:
            errors.append(ep + ".weight: finite weight in [0,1] required")
        elif abs(weight * 10 - round(weight * 10)) > 1e-8:
            errors.append(ep + ".weight: use increments of 0.1")
        if edge.get("id") == config.get("adjustable_edge_id"):
            adjustable = edge
    if not isinstance(config.get("adjustable_edge_id"), str) or adjustable is None:
        errors.append(path + ".adjustable_edge_id: unknown edge")
    if "selected_node_id" in config and (not isinstance(config["selected_node_id"], str) or config["selected_node_id"] not in node_ids):
        errors.append(path + ".selected_node_id: unknown node")
    if config.get("initial_mode", "all") not in ("all", "prune_one"):
        errors.append(path + ".initial_mode: requires all or prune_one")
    if config.get("tie_break") != "node_order":
        errors.append(path + ".tie_break: requires explicit node_order teaching rule")


def _visual_config(kind, config, path, errors):
    """Validate bounded declarative visual states, not executable paper code."""
    def records(key, limit):
        values = config.get(key)
        if not isinstance(values, list) or not 1 <= len(values) <= limit:
            errors.append(f"{path}.{key}: requires 1..{limit} entries")
            return [], set()
        ids = set()
        for i, value in enumerate(values):
            vp = f"{path}.{key}.{i}"
            if not isinstance(value, dict):
                errors.append(vp + ": object required")
                continue
            _id(value.get("id"), vp + ".id", errors, ids)
            _text(value.get("title" if key == "stages" else "label"), vp + ".label", errors)
        return [v for v in values if isinstance(v, dict)], ids

    if kind == "graph_walkthrough":
        nodes, node_ids = records("nodes", 60)
        edge_values = config.get("edges", [])
        edge_ids, edges = set(), {}
        if not isinstance(edge_values, list) or len(edge_values) > 200:
            errors.append(path + ".edges: requires at most 200 entries")
            edge_values = []
        for i, edge in enumerate(edge_values):
            ep = f"{path}.edges.{i}"
            if not isinstance(edge, dict):
                errors.append(ep + ": object required")
                continue
            _id(edge.get("id"), ep + ".id", errors, edge_ids)
            for endpoint in ("from", "to"):
                if not isinstance(edge.get(endpoint), str) or edge[endpoint] not in node_ids:
                    errors.append(ep + "." + endpoint + ": unknown node")
            if edge.get("kind", "intra") not in ("intra", "inter"):
                errors.append(ep + ".kind: requires intra or inter")
            if isinstance(edge.get("id"), str):
                edges[edge["id"]] = edge
        stages, stage_ids = records("stages", 40)
        for i, stage in enumerate(stages):
            sp = f"{path}.stages.{i}"
            _text(stage.get("text"), sp + ".text", errors)
            for field, available in (("active_node_ids", node_ids), ("active_edge_ids", edge_ids),
                                     ("highlight_node_ids", node_ids), ("highlight_edge_ids", edge_ids)):
                _ids(stage.get(field, []), sp + "." + field, errors, available)
            active_nodes = stage.get("active_node_ids", [])
            active_edges = stage.get("active_edge_ids", [])
            if isinstance(active_nodes, list) and isinstance(active_edges, list):
                for eid in active_edges:
                    edge = edges.get(eid) if isinstance(eid, str) else None
                    if edge and (edge.get("from") not in active_nodes or edge.get("to") not in active_nodes):
                        errors.append(sp + ".active_edge_ids: active edge endpoints must both be active")
            for field, ref_field, available in (("node_annotations", "node_id", node_ids), ("edge_annotations", "edge_id", edge_ids)):
                values = stage.get(field, [])
                if not isinstance(values, list) or len(values) > 200:
                    errors.append(sp + "." + field + ": bounded array required")
                    continue
                for j, item in enumerate(values):
                    ip = f"{sp}.{field}.{j}"
                    if not isinstance(item, dict):
                        errors.append(ip + ": object required")
                        continue
                    if not isinstance(item.get(ref_field), str) or item[ref_field] not in available:
                        errors.append(ip + ": unknown reference")
                    _text(item.get("text"), ip + ".text", errors)
        initial = config.get("initial_stage", 0)
        if type(initial) is not int or not 0 <= initial < len(stages):
            errors.append(path + ".initial_stage: outside stages")
    else:
        _text(config.get("metric_label"), path + ".metric_label", errors)
        if config.get("unit") != "%":
            errors.append(path + ".unit: comparison chart currently requires percentage values")
        models, model_ids = records("models", 30)
        tasks, task_ids = records("tasks", 100)
        series, series_ids = records("series", 12)
        for i, task in enumerate(tasks):
            for key in ("description", "denominator"):
                _text(task.get(key), f"{path}.tasks.{i}.{key}", errors)
        for field, available in (("default_model_id", model_ids), ("default_task_id", task_ids), ("baseline_series_id", series_ids)):
            if field in config and (not isinstance(config[field], str) or config[field] not in available):
                errors.append(path + "." + field + ": unknown reference")
        values, seen = config.get("observations"), set()
        if not isinstance(values, list) or not 1 <= len(values) <= 10000:
            errors.append(path + ".observations: requires 1..10000 records")
            values = []
        for i, item in enumerate(values):
            ip = f"{path}.observations.{i}"
            if not isinstance(item, dict):
                errors.append(ip + ": object required")
                continue
            for field, available in (("model_id", model_ids), ("task_id", task_ids), ("series_id", series_ids)):
                if not isinstance(item.get(field), str) or item[field] not in available:
                    errors.append(ip + "." + field + ": unknown reference")
            value = item.get("value")
            if not _number(value) or not 0 <= value <= 100:
                errors.append(ip + ".value: percentage must be finite and in [0,100]")
            key = tuple(item.get(k) for k in ("model_id", "task_id", "series_id"))
            if all(isinstance(k, str) for k in key):
                if key in seen:
                    errors.append(ip + ": duplicate observation")
                seen.add(key)
        if len(model_ids) * len(task_ids) * len(series_ids) != len(seen):
            errors.append(path + ".observations: provide one observation for each model/task/series combination")


def _placement(block, path, errors, allow_body=False):
    if "placement" not in block:
        return
    if block["placement"] != "body" or block.get("type") != "learning_aid":
        errors.append(path + ".placement: only learning_aid placement body is supported")
    if not allow_body:
        errors.append(path + ".placement: body is only allowed on top-level overview/explanation aids")


def _nested_placements(block, path, errors, depth=0):
    if depth > MAX_DEPTH:
        return  # The normal rich validator owns the existing nesting limit.
    containers=[(block.get("blocks"), path + ".blocks")]
    aid=block.get("aid")
    if isinstance(aid, dict) and isinstance(aid.get("fallback"), dict):
        containers.append((aid["fallback"].get("blocks"), path + ".aid.fallback.blocks"))
    for children, child_path in containers:
        if isinstance(children, list):
            for i, child in enumerate(children):
                if isinstance(child, dict):
                    cp=f"{child_path}.{i}"
                    _placement(child, cp, errors)
                    _nested_placements(child, cp, errors, depth + 1)


def _rich(document, path, errors, warnings, paper_dir=None, catalog=None, depth=0, seen=None, counter=None, allow_body=False):
    if depth > MAX_DEPTH:
        errors.append(path + ": rich block nesting exceeds limit")
        return
    if not isinstance(document, dict) or type(document.get("version")) is not int or document.get("version") != 1:
        errors.append(path + ": requires rich document {version:1,blocks:[...]}")
        return
    blocks = document.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append(path + ".blocks: must be a non-empty block array")
        return
    seen = set() if seen is None else seen
    counter = [0] if counter is None else counter
    for i, block in enumerate(blocks):
        counter[0] += 1
        if counter[0] > MAX_BLOCKS:
            errors.append(path + ": more than 1000 rich blocks")
            return
        bp = f"{path}.blocks.{i}"
        if not isinstance(block, dict):
            errors.append(bp + ": must be an object")
            continue
        _id(block.get("id"), bp + ".id", errors, seen)
        kind = block.get("type")
        _placement(block, bp, errors, allow_body)
        if not isinstance(kind, str) or kind not in RICH_TYPES:
            errors.append(bp + ".type: unknown rich block type")
            continue
        if block.get("evidence_kind") is not None and (not isinstance(block["evidence_kind"], str) or block["evidence_kind"] not in EVIDENCE_KINDS):
            errors.append(bp + ".evidence_kind: unknown provenance")
        if "source_refs" in block:
            _refs(block["source_refs"], bp + ".source_refs", errors)
        for field in ("text", "text_zh", "text_en", "title", "title_cn", "caption", "caption_zh", "caption_en", "summary"):
            if field in block:
                _text(block[field], bp + "." + field, errors, required=False)
        if kind in ("paragraph", "para", "heading", "quote") and not any(_string(block.get(f)) for f in ("text", "text_zh", "text_en", "title", "title_cn")) and not (kind == "quote" and block.get("blocks")):
            errors.append(bp + ": text content required")
        if kind in ("figure", "diagram"):
            _local_asset(block.get("src", block.get("asset")), bp + ".src", errors, paper_dir)
            # Inline SVG is deliberately omitted from the new rich document
            # contract. Legacy paper SVG remains handled by its old sanitizer.
            if "svg" in block:
                errors.append(bp + ".svg: use a registered local static asset")
        if kind == "formula":
            _latex(block.get("latex"), bp + ".latex", errors)
        if kind == "list":
            items = block.get("items")
            if not isinstance(items, list) or not items:
                errors.append(bp + ".items: non-empty list required")
            else:
                for j, item in enumerate(items):
                    val = item if isinstance(item, str) else item.get("text_zh", item.get("text", item.get("text_en"))) if isinstance(item, dict) else None
                    _text(val, f"{bp}.items.{j}", errors)
        if kind == "table":
            rows = block.get("rows", block.get("rows_zh", block.get("rows_en")))
            if not isinstance(rows, list) or not rows or any(not isinstance(row, list) for row in rows):
                errors.append(bp + ".rows: structured non-empty row arrays required")
            elif sum(len(row) for row in rows) > 10000:
                errors.append(bp + ".rows: table exceeds 10000 cells")
            else:
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        if not isinstance(cell, (str, int, float)) or isinstance(cell, bool):
                            errors.append(f"{bp}.rows.{ri}.{ci}: cell must be text or finite number")
                        if isinstance(cell, str):
                            _text(cell, f"{bp}.rows.{ri}.{ci}", errors, required=False)
            if "table_html" in block:
                errors.append(bp + ".table_html: rich documents require structured rows")
        if kind in ("algorithm", "code"):
            lines = block.get("lines", block.get("algo_lines"))
            code = block.get("code", block.get("text"))
            if lines is not None:
                if not isinstance(lines, list) or any(not isinstance(x, (str, dict)) for x in lines):
                    errors.append(bp + ".lines: text step array required")
            elif not _string(code):
                errors.append(bp + ": code/text or lines required")
        if kind == "details":
            _text(block.get("summary", block.get("title")), bp + ".summary", errors)
            _rich({"version":1,"blocks":block.get("blocks")}, bp, errors, warnings, paper_dir, catalog, depth + 1, seen, counter)
        if kind == "quote" and "blocks" in block:
            _rich({"version":1,"blocks":block["blocks"]}, bp, errors, warnings, paper_dir, catalog, depth + 1, seen, counter)
        if kind == "learning_aid":
            if bool(block.get("aid")) == bool(block.get("aid_id")):
                errors.append(bp + ": provide exactly one aid or aid_id")
            elif block.get("aid"):
                _aid(block["aid"], bp + ".aid", errors, warnings, paper_dir, catalog, depth + 1)
            else:
                _id(block.get("aid_id"), bp + ".aid_id", errors)
                if catalog is None:
                    errors.append(bp + ".aid_id: referenced component needs a learning catalog")
                elif isinstance(block["aid_id"], str) and block["aid_id"] not in {a.get("id") for a in catalog.get("aids", []) if isinstance(a, dict) and isinstance(a.get("id"), str)}:
                    errors.append(bp + ".aid_id: unknown catalog component")


def validate_rich_document(document, paper_dir=None, catalog=None):
    """Validate a user note/answer/fallback without mutating or executing it."""
    errors, warnings = [], []
    if catalog is not None and not isinstance(catalog, dict):
        errors.append("catalog: must be a learning catalog object")
        catalog = {}
    _safe_tree(document, "document", errors)
    _rich(document, "document", errors, warnings, paper_dir, catalog)
    return errors, warnings


def _example(value, path, errors):
    if not isinstance(value, dict):
        errors.append(path + ": complete example object required")
        return
    if value.get("evidence_kind") != "teaching_example":
        errors.append(path + ".evidence_kind: teaching numbers/objects must be marked teaching_example")
    for key in ("input", "result", "interpretation"):
        _text(value.get(key), path + "." + key, errors)
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(path + ".steps: show intermediate calculation/reasoning")
    else:
        for i, step in enumerate(steps):
            sp = f"{path}.steps.{i}"
            if isinstance(step, str):
                _text(step, sp, errors)
            elif isinstance(step, dict):
                _text(step.get("text"), sp + ".text", errors)
                if "latex" in step:
                    _latex(step["latex"], sp + ".latex", errors)
            else:
                errors.append(sp + ": text or reasoning step required")


def _formula(value, path, errors, formulas, known):
    if not isinstance(value, dict):
        errors.append(path + ": formula explanation must be an object")
        return
    _id(value.get("id"), path + ".id", errors)
    ref = value.get("formula_ref", value.get("source_formula_id"))
    if not isinstance(ref, str) or ref not in formulas:
        errors.append(path + ".formula_ref: must reference an existing formula block")
    for field in ("purpose", "variation", "boundary", "paper_connection"):
        _text(value.get(field), path + "." + field, errors)
    symbols = value.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        errors.append(path + ".symbols: explain variables, indices and required notation")
    else:
        for i, symbol in enumerate(symbols):
            if not isinstance(symbol, dict):
                errors.append(f"{path}.symbols.{i}: object required")
                continue
            _latex(symbol.get("symbol"), f"{path}.symbols.{i}.symbol", errors, allow_multiline=False)
            _text(symbol.get("meaning"), f"{path}.symbols.{i}.meaning", errors)
    operations = value.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append(path + ".operations: explain what each central operation does")
    else:
        for i, operation in enumerate(operations):
            if not isinstance(operation, dict):
                errors.append(f"{path}.operations.{i}: object required")
                continue
            for field in ("operator", "meaning"):
                _text(operation.get(field), f"{path}.operations.{i}.{field}", errors)
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(path + ".steps: calculation or reasoning steps required")
    else:
        for i, step in enumerate(steps):
            sp = f"{path}.steps.{i}"
            if not isinstance(step, dict):
                errors.append(sp + ": step object required")
                continue
            _text(step.get("text"), sp + ".text", errors)
            if "latex" in step:
                _latex(step["latex"], sp + ".latex", errors)
    _example(value.get("example"), path + ".example", errors)
    if "source_refs" in value:
        _refs(value["source_refs"], path + ".source_refs", errors, known)


def _overview_framework(requirement, sections, catalog, errors, paper_dir=None):
    """Check the declared method figure's location and renderable shape only.

    The registration declares which overview section covers the method. Whether
    the figure covers the complete method and agrees with the prose remains a
    human review; IDs and a graph component cannot certify those semantics.
    """
    path = "learning_requirements.overview_framework"
    if not isinstance(requirement, dict):
        errors.append(path + ": object with section_id and block_id required")
        return
    before = len(errors)
    kind = requirement.get("kind")
    if kind is not None and kind != "interactive_pipeline":
        errors.append(path + ".kind: only interactive_pipeline is supported when provided")
    for field in ("section_id", "block_id"):
        _id(requirement.get(field), path + "." + field, errors)
    if len(errors) != before:
        return
    matches = [(i, section) for i, section in enumerate(sections if isinstance(sections, list) else [])
        if isinstance(section, dict) and section.get("id") == requirement["section_id"]]
    if len(matches) != 1:
        errors.append(path + ".section_id: must identify exactly one overview method section")
        return
    si, section = matches[0]
    blocks = section.get("blocks")
    matches = [(i, block) for i, block in enumerate(blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict) and block.get("id") == requirement["block_id"]]
    if len(matches) != 1:
        errors.append(path + ".block_id: must identify one top-level block in the registered overview method section")
        return
    bi, block = matches[0]
    if block.get("type") in ("figure", "diagram"):
        if kind == "interactive_pipeline":
            errors.append(path + ".block_id: interactive_pipeline requires a method_pipeline learning_aid; a static figure cannot satisfy it")
        # Apply the shared asset contract also to an empty scaffold placeholder;
        # the ordinary top-level legacy checks only inspect non-empty assets.
        bp = f"overview.sections.{si}.blocks.{bi}"
        _local_asset(block.get("src", block.get("asset")), bp + ".src", errors, paper_dir)
        if "svg" in block:
            errors.append(bp + ".svg: use a registered local static asset")
        return
    if block.get("type") != "learning_aid":
        errors.append(path + ".block_id: framework must be a figure, diagram, or directed method learning_aid")
        return
    if block.get("placement") != "body":
        errors.append(path + ".block_id: framework learning_aid requires placement body to stay visible by default")
    aid = block.get("aid")
    if not aid and isinstance(block.get("aid_id"), str):
        aid = next((item for item in catalog.get("aids", [])
            if isinstance(item, dict) and item.get("id") == block["aid_id"]), None)
    allowed_aids = {"method_pipeline"} if kind == "interactive_pipeline" else {"graph_walkthrough", "method_pipeline"}
    if not isinstance(aid, dict) or not isinstance(aid.get("type"), str) or aid["type"] not in allowed_aids:
        errors.append(path + ".block_id: framework learning_aid must resolve to " + ("method_pipeline" if kind == "interactive_pipeline" else "graph_walkthrough or method_pipeline"))
    # Ordinary body-aid validation checks the component config,
    # fallback, exactly-one reference rule, and catalog/asset safety.


def _reject_learning_layers(value, path, errors, depth=0):
    """A 3.2 body has one reading line, including its nested content."""
    if depth > 32:
        return  # The safe-tree validator owns the JSON nesting bound.
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "learning":
                errors.append(path + ".learning: contract 3.2 forbids a second learning layer; 请将必要讲解并入正文")
            else:
                _reject_learning_layers(item, path + "." + str(key), errors, depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_learning_layers(item, f"{path}.{index}", errors, depth + 1)


def validate_learning_content(data, paper_dir=None, preview=False):
    """Validate active content without treating metadata as semantic certification.

    Existing v3 without the marker is valid through the legacy deployment path.
    Legacy learning fields remain checked for safety and consistency. Contract
    3.2 rejects learning layers and requires evidence_kind on each actual body
    block, including details/quote children. Those IDs share one paper scope;
    component fallback documents keep their local rich-document ID scope.
    Retired default_annotations metadata is ignored without parsing or rendering.
    3.2 checks formula references, while humans review the actual body explanation.
    Preview skips only publication readiness and completed review, never safety.
    """
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["paper: object required"], warnings
    version = data.get("content_contract_version")
    strict = version is not None
    modern = version == "3.2"
    if strict and (not isinstance(version, str) or version not in SUPPORTED_CONTRACT_VERSIONS):
        errors.append("content_contract_version: unsupported contract " + str(version))
    if strict and data.get("schema_version") != 3:
        errors.append("schema_version: contract " + str(version) + " requires additive schema_version 3")
    translation_status = _translation_state(data, errors, preview)
    if modern or preview:
        _safe_tree({key: value for key, value in data.items() if key != "default_annotations"}, "paper", errors)
        _text(data.get("content_version"), "content_version", errors)
        _id(data.get("id"), "id", errors)
    if strict:
        if not preview and data.get("content_status") != "ready":
            errors.append("content_status: unfinished scaffold cannot deploy; complete content and review, then set ready")
        elif preview and data.get("content_status") not in (None, "incomplete", "ready"):
            errors.append("content_status: must be incomplete or ready")
        review = data.get("content_review")
        if review is None and preview:
            pass
        elif not isinstance(review, dict):
            errors.append("content_review: required semantic review record")
        else:
            # Preserve historical score records without using them as a
            # semantic review or deployment threshold.
            if "plain_language_scores" in review:
                scores = review["plain_language_scores"]
                if not isinstance(scores, list) or len(scores) != 10 or any(type(x) is not int or not 0 <= x <= 2 for x in scores):
                    errors.append("content_review.plain_language_scores: when provided, requires ten integer 0/1/2 scores")
            gates = review.get("factual_gates")
            if preview:
                if gates is not None and (not isinstance(gates, dict) or any(type(value) is not bool for value in gates.values())):
                    errors.append("content_review.factual_gates: when provided, gate values must be booleans")
                _text(review.get("reviewer"), "content_review.reviewer", errors, required=False)
            elif not isinstance(gates, dict) or any(gates.get(key) is not True for key in FACTUAL_GATES):
                errors.append("content_review.factual_gates: all six factual gates must pass")
            if not preview:
                _text(review.get("reviewer"), "content_review.reviewer", errors)
    catalog = data.get("learning_catalog", {})
    if not isinstance(catalog, dict):
        return errors + ["learning_catalog: object required"], warnings
    if modern:
        _reject_learning_layers(catalog, "learning_catalog", errors)
    if version == "3.1" and not all(key in catalog for key in ("concepts", "aids")):
        errors.append("learning_catalog: concepts and aids arrays required (may be empty when justified)")
    _safe_tree(catalog, "learning_catalog", errors)
    concepts, aids = catalog.get("concepts", []), catalog.get("aids", [])
    if not isinstance(concepts, list) or not isinstance(aids, list):
        return errors + ["learning_catalog: concepts/aids must be arrays"], warnings
    concept_ids, aid_ids, graph = set(), set(), {}
    for i, concept in enumerate(concepts):
        cp = f"learning_catalog.concepts.{i}"
        if not isinstance(concept, dict):
            errors.append(cp + ": object required")
            continue
        _id(concept.get("id"), cp + ".id", errors, concept_ids)
        for key in ("label", "context_meaning", "plain_definition"):
            _text(concept.get(key), cp + "." + key, errors)
        _ids(concept.get("prerequisite_ids", []), cp + ".prerequisite_ids", errors)
        if isinstance(concept.get("id"), str):
            graph[concept["id"]] = concept.get("prerequisite_ids", [])
    for i, concept in enumerate(concepts):
        if isinstance(concept, dict):
            _ids(concept.get("prerequisite_ids", []), f"learning_catalog.concepts.{i}.prerequisite_ids", errors, concept_ids)
    active, done = set(), set()
    def visit(cid):
        if cid in active:
            errors.append("learning_catalog.concepts: prerequisite cycle through " + str(cid))
            return
        if cid in done or cid not in graph:
            return
        active.add(cid)
        deps = graph[cid]
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, str):
                    visit(dep)
        active.remove(cid)
        done.add(cid)
    # Bound recursive DFS before adversarial catalogs can exhaust Python stack.
    if len(graph) > 500:
        errors.append("learning_catalog.concepts: maximum 500 concepts")
    else:
        for cid in graph:
            visit(cid)
    for i, aid in enumerate(aids):
        if isinstance(aid, dict):
            _id(aid.get("id"), f"learning_catalog.aids.{i}.id", errors, aid_ids)
        _aid(aid, f"learning_catalog.aids.{i}", errors, warnings, paper_dir, catalog)
    views = {"overview":data.get("overview", {}).get("sections", []) if isinstance(data.get("overview"), dict) else [],
        "explanation":data.get("explanation", {}).get("sections", []) if isinstance(data.get("explanation"), dict) else [], "translation":data.get("sections", [])}
    known, formulas, blocks, nested_blocks, section_refs = set(), {}, [], [], []
    def collect_body_children(view, block, path, depth=0):
        # Only actual details/quote body blocks belong to paper anchors.
        # Component fallbacks and configuration IDs remain local to their aid.
        if depth > MAX_DEPTH or block.get("type") not in ("details", "quote"):
            return
        children = block.get("blocks")
        if not isinstance(children, list):
            return  # The rich validator reports malformed/deep containers.
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_path = f"{path}.blocks.{index}"
            _id(child.get("id"), child_path + ".id", errors, known)
            nested_blocks.append((view, child, child_path))
            if child.get("type") == "formula" and isinstance(child.get("id"), str):
                formulas[child["id"]] = child
            collect_body_children(view, child, child_path, depth + 1)
    for view, sections in views.items():
        if not isinstance(sections, list):
            continue
        if modern:
            _reject_learning_layers(sections, view + ".sections", errors)
        section_ids = set()
        for si, section in enumerate(sections):
            if not isinstance(section, dict) or not isinstance(section.get("blocks", []), list):
                continue
            if modern or preview or (view == "translation" and translation_status == "ready"):
                _id(section.get("id"), f"{view}.sections.{si}.id", errors, section_ids)
                if not section.get("blocks"):
                    errors.append(f"{view}.sections.{si}.blocks: non-empty block array required")
            if "source_refs" in section:
                section_refs.append((section["source_refs"], f"{view}.sections.{si}.source_refs"))
            for bi, block in enumerate(section.get("blocks", [])):
                if not isinstance(block, dict):
                    continue
                path = f"{view}.sections.{si}.blocks.{bi}"
                _id(block.get("id"), path + ".id", errors, known)
                blocks.append((view, block, path))
                if modern:
                    collect_body_children(view, block, path)
                if block.get("type") == "formula":
                    if isinstance(block.get("id"), str):
                        formulas[block["id"]] = block
    for refs, path in section_refs:
        _refs(refs, path, errors, known)
    if translation_status == "ready":
        for view, block, path in blocks + nested_blocks:
            if view == "translation" and block.get("type") in ("para", "paragraph", "heading"):
                for field in ("text_en", "text_zh"):
                    _text(block.get(field), path + "." + field, errors)
    for view, block, path in nested_blocks:
        if not isinstance(block.get("evidence_kind"), str) or block["evidence_kind"] not in EVIDENCE_KINDS:
            errors.append(path + ".evidence_kind: source classification required on each body block (no implicit inheritance)")
        if "source_refs" in block:
            _refs(block["source_refs"], path + ".source_refs", errors, known)
        if "key_formula" in block and type(block["key_formula"]) is not bool:
            errors.append(path + ".key_formula: must be boolean")
    explained, learning_ids, formula_ids = set(), set(), set()
    for view, block, bp in blocks:
        _placement(block, bp, errors, view in ("overview", "explanation"))
        _nested_placements(block, bp, errors)
        if modern or preview:
            # The body uses the same safe primitives as notes and answers.
            # Legacy note is a text alias; do not alter the stored block.
            active = dict(block, type="paragraph") if block.get("type") == "note" else block
            _rich({"version":1,"blocks":[active]}, bp, errors, warnings, paper_dir, catalog,
                allow_body=view in ("overview", "explanation"))
            if "key_formula" in block and type(block["key_formula"]) is not bool:
                errors.append(bp + ".key_formula: must be boolean")
        elif block.get("type") == "learning_aid" and block.get("placement") == "body" and view != "translation":
            _safe_tree(block, bp, errors)
            _rich({"version":1,"blocks":[block]}, bp, errors, warnings, paper_dir, catalog, allow_body=True)
        if strict and (not isinstance(block.get("evidence_kind"), str) or block["evidence_kind"] not in EVIDENCE_KINDS):
            errors.append(bp + ".evidence_kind: source classification required")
        if "source_refs" in block:
            _refs(block["source_refs"], bp + ".source_refs", errors, known)
        for asset_field in ("src", "asset"):
            if block.get(asset_field):
                _local_asset(block[asset_field], bp + "." + asset_field, errors, paper_dir)
        learning = block.get("learning")
        if modern and "learning" in block:
            continue  # Reported above; obsolete fields never become a 3.2 layer.
        if view == "translation" and learning is not None:
            errors.append(bp + ".learning: automatic learning layer is excluded from translation")
            continue
        if learning is None:
            continue
        lp = bp + ".learning"
        if not isinstance(learning, dict):
            errors.append(lp + ": object required")
            continue
        _safe_tree(learning, lp, errors)
        if "presentation" in learning and learning["presentation"] != "narrative":
            errors.append(lp + ".presentation: only narrative is supported for new presentation")
        if learning.get("presentation") == "narrative":
            narrative = learning.get("blocks")
            if not isinstance(narrative, list) or not any(isinstance(b, dict) and b.get("type") in ("paragraph", "para") for b in narrative):
                errors.append(lp + ".blocks: narrative explanation needs connected prose paragraphs")
        _id(learning.get("id"), lp + ".id", errors, learning_ids)
        _text(learning.get("plain_summary"), lp + ".plain_summary", errors, required=not modern)
        _ids(learning.get("concept_ids", []), lp + ".concept_ids", errors, concept_ids)
        _ids(learning.get("aid_ids", []), lp + ".aid_ids", errors, aid_ids)
        explanation_list = learning.get("formula_explanations", [])
        if not isinstance(explanation_list, list):
            errors.append(lp + ".formula_explanations: array required")
        else:
            for i, explanation in enumerate(explanation_list):
                fp = f"{lp}.formula_explanations.{i}"
                _formula(explanation, fp, errors, formulas, known)
                if isinstance(explanation, dict):
                    _id(explanation.get("id"), fp + ".id", errors, formula_ids)
                    formula_ref = explanation.get("formula_ref", explanation.get("source_formula_id"))
                    if isinstance(formula_ref, str):
                        explained.add(formula_ref)
        if "example" in learning:
            _example(learning["example"], lp + ".example", errors)
        if "analogy" in learning:
            analogy = learning["analogy"]
            if not isinstance(analogy, dict):
                errors.append(lp + ".analogy: object required")
            else:
                for field in ("text", "boundary"):
                    _text(analogy.get(field), lp + ".analogy." + field, errors)
                mappings = analogy.get("mappings")
                if not isinstance(mappings, list) or not mappings:
                    errors.append(lp + ".analogy.mappings: explicit everyday/paper correspondences required")
                else:
                    for i, mapping in enumerate(mappings):
                        for field in ("everyday", "paper"):
                            _text(mapping.get(field) if isinstance(mapping, dict) else None, f"{lp}.analogy.mappings.{i}.{field}", errors)
        if "blocks" in learning:
            _rich({"version":1,"blocks":learning["blocks"]}, lp, errors, warnings, paper_dir, catalog)
    requirements = data.get("learning_requirements", {})
    if isinstance(requirements, dict) and "overview_framework" in requirements:
        # Full modern paper readers keep the user's five overview sections.
        # Drafts may still be incomplete; legacy and isolated field examples
        # retain their existing contract without a new marker or role system.
        if modern and not preview:
            if len(views["overview"]) != 5:
                errors.append("overview.sections: 完整论文必须保留五个独立概览章节：问题背景、方法框架、创新点、实验与效果、开销与论文边界；不能合并")
            elif (isinstance(requirements["overview_framework"], dict) and
                    isinstance(views["overview"][1], dict) and
                    views["overview"][1].get("id") != requirements["overview_framework"].get("section_id")):
                errors.append("learning_requirements.overview_framework.section_id: 方法框架必须位于五段概览的第二节")
        _overview_framework(requirements["overview_framework"], views["overview"],
            catalog, errors, paper_dir)
    if strict:
        for bid, block in formulas.items():
            if not modern and block.get("key_formula") is True and bid not in explained:
                errors.append("formula " + str(bid) + ": key_formula requires a complete nearby explanation")
        if not isinstance(requirements, dict):
            errors.append("learning_requirements: object required")
        else:
            # Legacy concept requirements remain references, not a mandate to
            # manufacture a glossary, automatic notes, or one note per term.
            _ids(requirements.get("required_concept_ids", []), "learning_requirements.required_concept_ids", errors, concept_ids)
            values = requirements.get("required_formula_ids", [])
            _ids(values, "learning_requirements.required_formula_ids", errors, set(formulas))
            if not modern and isinstance(values, list):
                for item in values:
                    if isinstance(item, str) and item not in explained:
                        errors.append("learning_requirements.required_formula_ids: required understanding dependency lacks nearby explanation: " + item)
    return errors, warnings


def rich_to_text(document):
    """Deterministic readable fallback/export, retaining teaching labels and LaTeX.

    Callers validate first. Unknown future blocks still preserve their textual
    fields rather than executing, rendering HTML, or silently losing evidence.
    """
    def scalar(value):
        return str(value) if value is not None else ""
    def text_of(block):
        return scalar(block.get("text_zh", block.get("text", block.get("text_en", ""))))
    def aid_text(aid):
        parts = [("[论文结果] " if aid.get("evidence_kind") == "paper" else "[教学示例] ") + scalar(aid.get("title", aid.get("id", "学习演示")))]
        if aid.get("fallback"):
            parts.append(rich_to_text(aid["fallback"]))
        elif aid.get("fallback_text"):
            parts.append(scalar(aid["fallback_text"]))
        return "\n".join(parts)
    def blocks_text(blocks, depth=0):
        if depth > MAX_DEPTH or not isinstance(blocks, list):
            return []
        result = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind, parts = block.get("type"), []
            title = block.get("title_cn", block.get("title", block.get("algo_title")))
            if title:
                parts.append(scalar(title))
            if block.get("evidence_kind") == "teaching_example":
                parts.insert(0, "[教学示例]")
            if kind == "formula":
                parts.append(r"\[" + scalar(block.get("latex")) + r"\]")
                if text_of(block):
                    parts.append(text_of(block))
            elif kind == "list":
                for item in block.get("items", []):
                    parts.append("- " + (text_of(item) if isinstance(item, dict) else scalar(item)))
            elif kind == "table":
                headers = block.get("headers", block.get("headers_zh", []))
                if isinstance(headers, list) and headers:
                    parts.append(" | ".join(map(scalar, headers)))
                rows = block.get("rows", block.get("rows_zh", block.get("rows_en", [])))
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, list):
                        parts.append(" | ".join(map(scalar, row)))
            elif kind in ("figure", "diagram"):
                caption = block.get("caption_zh", block.get("caption", block.get("caption_en", block.get("alt", ""))))
                parts.append("[图像: " + scalar(block.get("src", block.get("asset", ""))) + "] " + scalar(caption))
            elif kind in ("algorithm", "code"):
                lines = block.get("lines", block.get("algo_lines"))
                if isinstance(lines, list):
                    parts.extend(text_of(x) if isinstance(x, dict) else scalar(x) for x in lines)
                else:
                    parts.append(scalar(block.get("code", text_of(block))))
            elif kind == "details":
                if block.get("summary"):
                    parts.append(scalar(block["summary"]))
                parts.extend(blocks_text(block.get("blocks"), depth + 1))
            elif kind == "quote":
                if text_of(block):
                    parts.append("> " + text_of(block))
                parts.extend("> " + x for x in blocks_text(block.get("blocks"), depth + 1))
            elif kind == "separator":
                parts.append("---")
            elif kind == "learning_aid":
                if isinstance(block.get("aid"), dict):
                    parts.append(aid_text(block["aid"]))
                else:
                    parts.append("[教学组件引用: " + scalar(block.get("aid_id")) + "]")
                    if block.get("fallback_text"):
                        parts.append(scalar(block["fallback_text"]))
            else:
                parts.append(text_of(block))
            if "caption_zh" in block and kind not in ("figure", "diagram"):
                parts.append(scalar(block["caption_zh"]))
            if block.get("source_refs"):
                refs = []
                for ref in block["source_refs"]:
                    if isinstance(ref, dict):
                        url = ref.get("url")
                        if "url" in ref and (not isinstance(url, str) or not re.fullmatch(r"https?://[^\s/?#]+(?:[^\s]*)?", url)):
                            continue
                        if url:
                            try:
                                parsed_url = urlsplit(url)
                                if not parsed_url.hostname:
                                    continue
                                parsed_url.port  # Reject malformed ports as well as missing hosts.
                            except ValueError:
                                continue
                        citation = scalar(ref.get("label", ref.get("block_id", ref.get("content_id", "")))) + (" p." + scalar(ref["page"]) if ref.get("page") else "")
                        refs.append(citation + ((" " if citation else "") + url if url else ""))
                if any(refs):
                    parts.append("[来源: " + "; ".join(refs) + "]")
            result.append("\n".join(x for x in parts if x))
        return result
    if not isinstance(document, dict):
        return ""
    return "\n\n".join(x for x in blocks_text(document.get("blocks")) if x)

