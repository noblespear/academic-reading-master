"""Read-only gate for fulfillment of the explicit visual-plan in a new draft.

This checks declared commitments and their location/type, plus declared
learning-task/media consistency when interaction reviews are supplied or required.
It does not prove task classification or complete task coverage. Full
content/schema and browser QA remain separate.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


INTERACTIVE_TYPES = frozenset({
    "graph_walkthrough", "formula_explorer", "parameter_explorer",
    "prediction", "lru_cache", "weighted_degree", "method_pipeline", "steps",
})
ALIASES = {"cache_walkthrough": "lru_cache", "step_walkthrough": "steps"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_plan(path):
    """Require exactly one fenced visual-plan JSON array; never execute Markdown."""
    source = Path(path).read_text(encoding="utf-8-sig")
    openings = re.findall(r"^```visual-plan[ \t]*$", source, re.M)
    fences = re.findall(r"^```visual-plan[ \t]*\r?\n(.*?)^```[ \t]*$", source, re.M | re.S)
    if len(openings) != 1 or len(fences) != 1:
        raise ValueError("reading-plan must contain exactly one closed ```visual-plan JSON fence")
    plan = json.loads(fences[0])
    if not isinstance(plan, list) or not plan:
        raise ValueError("visual-plan must be a non-empty JSON array")
    return plan


def read_data(path):
    source = Path(path).read_text(encoding="utf-8-sig").strip()
    if source.startswith("window.PAPER_DATA"):
        source = re.sub(r"^window\.PAPER_DATA\s*=\s*", "", source, count=1)
        if source.endswith(";"):
            source = source[:-1].rstrip()
    data = json.loads(source)
    if not isinstance(data, dict):
        raise ValueError("paper data must be a JSON object")
    return data


def aid_for(block, catalog):
    if isinstance(block.get("aid"), dict) and not block.get("aid_id"):
        return block["aid"]
    if not block.get("aid") and nonempty(block.get("aid_id")):
        matches = [a for a in catalog if isinstance(a, dict) and a.get("id") == block["aid_id"]]
        if len(matches) == 1:
            return matches[0]
    return None


def interactive_problem(aid):
    """Reject known static/text-only substitutes; full config validation is separate."""
    if not isinstance(aid, dict):
        return "learning_aid has no unique inline/catalog component"
    kind = aid.get("type")
    if not isinstance(kind, str):
        return "interactive component type must be text"
    kind = ALIASES.get(kind, kind)
    if kind not in INTERACTIVE_TYPES:
        return "interactive requires a supported state-changing component; got " + str(kind)
    config = aid.get("config")
    if not isinstance(config, dict) or not config:
        return "interactive component is missing its configuration"
    if kind == "steps":
        steps = config.get("steps")
        states = set()
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            blocks = step.get("blocks")
            visuals = []
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("figure", "diagram"):
                    source = block.get("src", block.get("asset"))
                    if nonempty(source):
                        visuals.append(("image", source))
                elif block.get("type") == "table":
                    rows = block.get("rows_zh", block.get("rows"))
                    if isinstance(rows, list) and rows:
                        visuals.append(("table", rows))
            if visuals:
                states.add(json.dumps(visuals, sort_keys=True))
        if len(states) < 2:
            return "steps needs at least two different graphical states; text/formula-only or repeated-image pages do not qualify"
    elif kind == "graph_walkthrough":
        stages = config.get("stages", [])
        if not config.get("nodes") or not isinstance(stages, list) or len(stages) < 2:
            return "graph_walkthrough needs nodes and at least two graphical stages"
        graphical = ("active_node_ids", "active_edge_ids", "highlight_node_ids", "highlight_edge_ids", "node_annotations", "edge_annotations")
        states = {json.dumps({k: s.get(k) for k in graphical}, sort_keys=True)
                  for s in stages if isinstance(s, dict)}
        if len(states) < 2:
            return "graph_walkthrough only changes text; graphical stages must change"
    elif kind == "method_pipeline":
        nodes, edges = config.get("nodes"), config.get("edges")
        if not isinstance(nodes, list) or len(nodes) < 2 or not isinstance(edges, list) or not edges:
            return "method_pipeline needs at least two selectable stages and their relationships"
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                return f"method_pipeline node[{index}] must be an object"
            for field in ("summary", "inputs", "outputs"):
                if not nonempty(node.get(field)):
                    return f"method_pipeline node[{index}].{field} must be non-empty text for node selection"
        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                return f"method_pipeline edge[{index}] must be an object"
            for field in ("flow", "cause"):
                if not nonempty(edge.get(field)):
                    return f"method_pipeline edge[{index}].{field} must be non-empty text for relationship selection"
    elif kind in {"formula_explorer", "parameter_explorer"}:
        parameters = config.get("parameters")
        if not isinstance(parameters, list) or not parameters or not ("expression" in config or config.get("model") == "ratio"):
            return "explorer needs parameters and an expression"
    elif kind == "prediction":
        if not isinstance(config.get("choices"), list) or len(config["choices"]) < 2:
            return "prediction needs at least two choices"
    elif kind == "lru_cache":
        if not config.get("sequence", config.get("requests")):
            return "lru_cache needs a request sequence"
    elif kind == "weighted_degree":
        if not config.get("nodes") or not config.get("edges") or not nonempty(config.get("adjustable_edge_id")):
            return "weighted_degree needs a graph and adjustable edge"
    return None


def _visible_plain_blocks(blocks):
    # Only sibling narrative blocks are visible in the anchor's learning card.
    # Do not treat details/fallback/other learning cards as fulfillment targets.
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []


def _required_method_pipeline(plan, data, catalog):
    """Opt-in new-reader entrance gate, independent of scaffold use."""
    label = "learning_requirements.overview_framework"
    requirements = data.get("learning_requirements")
    framework = requirements.get("overview_framework") if isinstance(requirements, dict) else None
    if not isinstance(framework, dict) or framework.get("kind") != "interactive_pipeline":
        return [label + ": --require-method-pipeline requires kind interactive_pipeline"]
    if any(not isinstance(framework.get(k), str) or not ID_PATTERN.fullmatch(framework[k]) for k in ("section_id", "block_id")):
        return [label + ": valid section_id and block_id are required"]
    errors = []
    matching_plan = any(isinstance(row, dict) and row.get("view") == "overview"
        and row.get("section_id") == framework["section_id"] and row.get("layer") == "body"
        and row.get("decision") == "interactive" and row.get("status") == "done"
        and isinstance(row.get("target_block_ids"), list) and framework["block_id"] in row["target_block_ids"]
        for row in plan)
    if not matching_plan:
        errors.append(label + ": registered block_id must have an overview/body/interactive/done visual-plan entry in the registered section")
    overview = data.get("overview", {})
    sections = overview.get("sections", []) if isinstance(overview, dict) else []
    sections = sections if isinstance(sections, list) else []
    sections = [s for s in sections if isinstance(s, dict) and s.get("id") == framework["section_id"]]
    blocks = _visible_plain_blocks(sections[0].get("blocks")) if len(sections) == 1 else []
    blocks = [b for b in blocks if b.get("id") == framework["block_id"]]
    if len(blocks) != 1:
        errors.append(label + ": block_id must identify one top-level block in the registered overview section")
    else:
        block = blocks[0]
        aid = aid_for(block, catalog) if block.get("type") == "learning_aid" else None
        if block.get("placement") != "body" or not isinstance(aid, dict) or aid.get("type") != "method_pipeline":
            errors.append(label + ": registered block must be a placement body method_pipeline learning_aid")
    return errors


def _interaction_review_errors(row, label, required):
    """Check declared task/media consistency, without inferring learning tasks."""
    if "interaction_review" not in row:
        return [label + ": interaction_review is required"] if required else []
    review = row["interaction_review"]
    label += ".interaction_review"
    if not isinstance(review, dict):
        return [label + ": must be an object"]
    errors = []
    task = review.get("task")
    if not isinstance(task, str) or task not in ("observe", "trace", "change"):
        errors.append(label + ".task must be observe, trace, or change")
    if not nonempty(review.get("reason")):
        errors.append(label + ".reason must be non-empty text")
    if task in ("trace", "change") and row.get("decision") != "interactive":
        errors.append(label + f": task {task} requires decision interactive")
    if task in ("trace", "change") or (task == "observe" and row.get("decision") == "interactive"):
        for field in ("action", "observable_change"):
            if not nonempty(review.get(field)):
                errors.append(label + "." + field + " must be non-empty text for an interactive learning task")
    return errors


def validate_visual_plan(plan, data, require_method_pipeline=False, require_interaction_review=False):
    """Return errors for fulfillment and supplied/required task/media consistency.

    Interaction reviews declare tasks; this gate does not establish that their
    classifications are correct or that the plan covers all learning tasks.
    """
    errors = []
    if not isinstance(plan, list) or not plan:
        return ["visual-plan must be a non-empty JSON array"]
    if not isinstance(data, dict):
        return ["paper data must be an object"]
    catalog = data.get("learning_catalog", {})
    catalog = catalog.get("aids", []) if isinstance(catalog, dict) else []
    catalog = catalog if isinstance(catalog, list) else []
    if require_method_pipeline:
        errors.extend(_required_method_pipeline(plan, data, catalog))
    seen = set()
    for index, row in enumerate(plan):
        label = f"visual-plan[{index}]"
        if not isinstance(row, dict):
            errors.append(label + ": entry must be an object")
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            errors.append(label + ": invalid id")
        elif identifier in seen:
            errors.append(label + ": duplicate id " + identifier)
        else:
            seen.add(identifier)
        label += " (" + str(identifier) + ")"
        view, layer, decision = (row.get(k) for k in ("view", "layer", "decision"))
        if view not in ("overview", "explanation"):
            errors.append(label + ": view must be overview or explanation")
        if layer not in ("body", "plain"):
            errors.append(label + ": layer must be body or plain")
        if decision not in ("interactive", "static", "table", "text"):
            errors.append(label + ": invalid decision")
        errors.extend(_interaction_review_errors(row, label, require_interaction_review))
        if not nonempty(row.get("reason")):
            errors.append(label + ": reason must be non-empty text")
        if row.get("status") != "done":
            errors.append(label + ": status must be done; commitment remains " + str(row.get("status")))
        targets = row.get("target_block_ids")
        if not isinstance(targets, list) or any(not isinstance(t, str) or not ID_PATTERN.fullmatch(t) for t in targets):
            errors.append(label + ": target_block_ids must be an array of valid stable IDs")
            targets = []
        elif len(set(targets)) != len(targets):
            errors.append(label + ": duplicate target_block_ids")
        if decision != "text" and not targets:
            errors.append(label + ": visual/table commitment has no target_block_ids")
        if any(not isinstance(row.get(k), str) or not ID_PATTERN.fullmatch(row[k]) for k in ("section_id", "anchor_block_id")):
            errors.append(label + ": section_id and anchor_block_id must be valid stable IDs")
            continue
        view_data = data.get(view, {}) if isinstance(view, str) else {}
        sections = view_data.get("sections", []) if isinstance(view_data, dict) else []
        sections = sections if isinstance(sections, list) else []
        sections = [s for s in sections if isinstance(s, dict) and s.get("id") == row["section_id"]]
        if len(sections) != 1:
            errors.append(label + ": section does not exist uniquely in the specified view")
            continue
        body = _visible_plain_blocks(sections[0].get("blocks"))
        anchors = [(i, b) for i, b in enumerate(body) if b.get("id") == row["anchor_block_id"]]
        if len(anchors) != 1:
            errors.append(label + ": anchor does not exist uniquely in the specified section body")
            continue
        anchor_index, anchor = anchors[0]
        if layer not in ("body", "plain"):
            continue
        learning = anchor.get("learning", {})
        learning = learning if isinstance(learning, dict) else {}
        scope = body if layer == "body" else _visible_plain_blocks(learning.get("blocks"))
        aid_ids = learning.get("aid_ids", [])
        aid_ids = aid_ids if isinstance(aid_ids, list) else []
        for target in targets:
            candidates = [(i, b) for i, b in enumerate(scope) if b.get("id") == target]
            # Registered catalog IDs may be targets only for this anchor's aid_ids.
            if not candidates and layer == "plain" and target in aid_ids:
                candidates = [(None, {"type": "learning_aid", "aid_id": target})]
            if len(candidates) != 1:
                errors.append(label + ": target " + target + " does not exist uniquely in the specified section/layer/anchor")
                continue
            position, block = candidates[0]
            if layer == "body":
                if block.get("type") == "learning_aid" and block.get("placement") != "body":
                    errors.append(label + ": target " + target + " is hidden; body learning_aid requires placement body")
                lo, hi = sorted((anchor_index, position))
                if any(b.get("type") == "heading" for b in body[lo + 1:hi + 1]):
                    errors.append(label + ": target " + target + " crosses a heading from its anchor")
            kind = block.get("type")
            problem = None
            if decision == "interactive":
                problem = interactive_problem(aid_for(block, catalog)) if kind == "learning_aid" else "interactive cannot be fulfilled by " + str(kind)
            elif decision == "static" and kind not in ("figure", "diagram"):
                problem = "static requires a figure or diagram; got " + str(kind)
            elif decision == "static" and not nonempty(block.get("src", block.get("asset"))):
                problem = "static target is an empty placeholder without a local src/asset"
            elif decision == "table" and kind != "table":
                problem = "table requires a table block; got " + str(kind)
            elif decision == "table" and not (block.get("rows") or block.get("rows_zh") or nonempty(block.get("src", block.get("asset")))):
                problem = "table target is an empty placeholder without rows or a local src/asset"
            if problem:
                errors.append(label + ": target " + target + ": " + problem)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="reading-plan.md with one visual-plan fence")
    parser.add_argument("--data", required=True, help="paper-data.js or JSON")
    parser.add_argument("--require-method-pipeline", action="store_true",
                        help="new full reader: require registered overview method_pipeline and its fulfilled interactive plan")
    parser.add_argument("--require-interaction-review", action="store_true",
                        help="require each entry's learning task/media review; checks declared consistency, not correct task classification or complete task coverage")
    args = parser.parse_args(argv)
    try:
        plan, data = read_plan(args.plan), read_data(args.data)
        errors = validate_visual_plan(plan, data, require_method_pipeline=args.require_method_pipeline,
                                      require_interaction_review=args.require_interaction_review)
    except (OSError, ValueError) as exc:
        print("FAIL: " + str(exc))
        return 1
    counts = Counter(r.get("status", "missing") if isinstance(r, dict) and isinstance(r.get("status", "missing"), str) else "invalid" for r in plan)
    decisions = Counter(r.get("decision", "missing") if isinstance(r, dict) and isinstance(r.get("decision", "missing"), str) else "invalid" for r in plan)
    print(f"Visual plan: {len(plan)} entries; status={dict(counts)}; decisions={dict(decisions)}")
    for error in errors:
        print("FAIL: " + error)
    print(f"{'FAIL' if errors else 'PASS'}: {len(errors)} validation error(s). Checks declared commitments and supplied learning-task/media consistency; does not prove task classification or complete task coverage.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
