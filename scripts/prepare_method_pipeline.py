#!/usr/bin/env python3
"""Fill the existing v1 method_pipeline grid from a directed module skeleton.

Accept a component (aid) or its config as JSON. Output has the same shape and
is ready for the ordinary component validator; this is not a second DSL.
Feedback edges are excluded from topological layering. An unmarked cycle is
an error. A complete author grid is retained; partial grids are rejected.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

from content_contract import validate_rich_document


def prepare_method_pipeline(value):
    """Return an independent component/config with deterministic row/column."""
    result = copy.deepcopy(value)
    if not isinstance(result, dict):
        raise ValueError("input must be a method_pipeline component or config object")
    is_aid = "config" in result
    if is_aid and (result.get("type") != "method_pipeline" or result.get("version", 1) != 1):
        raise ValueError("component must use type method_pipeline and version 1")
    config = result.get("config") if is_aid else result
    if not isinstance(config, dict) or not isinstance(config.get("nodes"), list) or not config["nodes"]:
        raise ValueError("config.nodes must contain the module skeleton")
    nodes = config["nodes"]
    if any(not isinstance(node, dict) for node in nodes):
        raise ValueError("every node must be an object")
    ids = [node.get("id") for node in nodes]
    if any(not isinstance(node_id, str) or not node_id for node_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("nodes require unique non-empty IDs")
    if not isinstance(config.get("edges"), list):
        raise ValueError("config.edges must be an array")
    outgoing = {node_id: [] for node_id in ids}
    indegree = dict.fromkeys(ids, 0)
    for edge in config["edges"]:
        if not isinstance(edge, dict) or edge.get("from") not in outgoing or edge.get("to") not in outgoing:
            raise ValueError("every edge must refer to existing nodes")
        if edge.get("kind") not in {"flow", "branch", "dependency", "feedback"}:
            raise ValueError("edges require flow, branch, dependency or feedback kind")
        if edge["from"] == edge["to"]:
            raise ValueError("a node cannot connect to itself")
        if edge["kind"] != "feedback":
            outgoing[edge["from"]].append(edge["to"])
            indegree[edge["to"]] += 1
    # Stable Kahn ordering: the author node array breaks ties, never edges or
    # IDs. Longest predecessor depth puts a join after all its prerequisites.
    depth = dict.fromkeys(ids, 0)
    ready = [node_id for node_id in ids if indegree[node_id] == 0]
    visited = []
    while ready:
        current = ready.pop(0)
        visited.append(current)
        for target in outgoing[current]:
            depth[target] = max(depth[target], depth[current] + 1)
            indegree[target] -= 1
        ready = [node_id for node_id in ids if indegree[node_id] == 0 and node_id not in visited]
    if len(visited) != len(ids):
        cycle = ", ".join(node_id for node_id in ids if indegree[node_id] > 0)
        raise ValueError("non-feedback cycle: " + cycle + "; mark only genuine feedback edges as feedback")
    grid = [("row" in node, "column" in node) for node in nodes]
    if any(any(cell) for cell in grid) and not all(all(cell) for cell in grid):
        raise ValueError("partial explicit grid: provide row and column for every node, or omit both everywhere")
    if not all(all(cell) for cell in grid):
        lanes = {}
        for node in nodes:
            column = depth[node["id"]]
            node["column"] = column
            node["row"] = lanes.get(column, 0)
            lanes[column] = node["row"] + 1
    aid = result if is_aid else {"id": "prepared-method-pipeline", "type": "method_pipeline", "version": 1,
        "evidence_kind": "teaching_example", "config": config, "fallback_text": config.get("summary", "")}
    errors, _ = validate_rich_document({"version": 1, "blocks": [{"id": "prepared-map", "type": "learning_aid", "aid": aid}]})
    if errors:
        raise ValueError("invalid final method_pipeline: " + "; ".join(errors))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSON component or config; nodes may omit row/column")
    parser.add_argument("--output", required=True, type=Path, help="validated JSON of the same shape, with a complete grid")
    args = parser.parse_args(argv)
    try:
        prepared = prepare_method_pipeline(json.loads(args.input.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as error:
        parser.exit(1, "prepare_method_pipeline: " + str(error) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
