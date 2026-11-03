#!/usr/bin/env python3
"""Render a local, script-free Chinese method figure from JSON using only stdlib.

The input is {title, nodes: [{id, label, detail?, glyph?, column?, row?}],
edges: [{source, target, kind?, label?}], groups?: [{id, label, nodes: [id]}],
theme?: {numeric token: value}}. Explicit grid coordinates must be complete.
Feedback is excluded from DAG layering; no scientific meaning is inferred.

CLI: python render_method_figure.py --input figure.json --output figure.svg
Issues describe detectable geometry/legibility constraints, not scientific
correctness or a guarantee of publication quality. No browser is required.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import heapq
import html
import json
import math
from pathlib import Path
import sys
import unicodedata


# These are reader engineering defaults in SVG user units, not journal rules.
# At the default 960 px display width a diagram of up to four columns is 1:1.
THEME = {
    "node_width": 180.0, "node_height": 120.0,
    "column_gutter": 56.0, "row_gutter": 72.0,
    "margin": 36.0, "content_top": 104.0, "canvas_min_width": 960.0,
    "label_font": 16.0, "detail_font": 13.0, "title_font": 22.0,
    "edge_font": 13.0, "group_font": 13.0,
    "line_width": 1.5, "arrow_length": 7.0, "arrow_width": 7.0,
    "port_stub": 24.0, "port_gap": 3.0, "obstacle_clearance": 7.0,
    "min_visible_shaft": 12.0, "padding": 16.0, "glyph_size": 30.0,
    "lane_gap": 24.0, "bend_cost": 8.0,
}
KINDS = {"flow", "branch", "feedback", "dependency"}
GLYPHS = {"none", "mesh", "matrix", "feature", "input", "output", "input-output"}
COLORS = {"flow": "#3d566c", "branch": "#356b88", "feedback": "#8a617c", "dependency": "#75808a"}
FONT = "'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC', sans-serif"
EPS = 1e-7


def _xml(value):
    # XML 1.0 rejects control characters and unpaired surrogates even escaped.
    text = str(value)
    text = "".join(c for c in text if c in "\t\n\r" or
                   0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                   0x10000 <= ord(c) <= 0x10FFFF)
    return html.escape(text, quote=True)


def _num(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _issue(issues, code, message, severity="warning", **context):
    issues.append({"code": code, "severity": severity, "message": message, **context})


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def expanded(self, amount):
        return Rect(self.x - amount, self.y - amount,
                    self.width + 2 * amount, self.height + 2 * amount)


def segment_crosses_rect(a, b, rect):
    """True only for intersection with a rectangle's open interior."""
    if abs(a[1] - b[1]) < EPS:
        return rect.y + EPS < a[1] < rect.bottom - EPS and \
            max(min(a[0], b[0]), rect.x) < min(max(a[0], b[0]), rect.right) - EPS
    if abs(a[0] - b[0]) < EPS:
        return rect.x + EPS < a[0] < rect.right - EPS and \
            max(min(a[1], b[1]), rect.y) < min(max(a[1], b[1]), rect.bottom) - EPS
    raise ValueError("routing produced a non-orthogonal segment")


def _rects_overlap(a, b):
    return max(a.x, b.x) < min(a.right, b.right) - EPS and \
        max(a.y, b.y) < min(a.bottom, b.bottom) - EPS


def _simplify(points):
    result = []
    for point in points:
        point = tuple(point)
        if result and point == result[-1]:
            continue
        if len(result) >= 2:
            a, b = result[-2:]
            if (abs(a[0] - b[0]) < EPS and abs(b[0] - point[0]) < EPS and
                    (b[1] - a[1]) * (point[1] - b[1]) >= 0) or \
               (abs(a[1] - b[1]) < EPS and abs(b[1] - point[1]) < EPS and
                    (b[0] - a[0]) * (point[0] - b[0]) >= 0):
                result.pop()
        result.append(point)
    return result


def _text_width(text, font_size):
    return sum(0 if unicodedata.combining(c) else
               (1 if unicodedata.east_asian_width(c) in "WF" else 0.56)
               for c in text) * font_size


def _wrap(text, width, font_size, limit):
    """Wrap by approximate CJK advances; never decrease a font to fit text."""
    lines = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for char in paragraph:
            if current and _text_width(current + char, font_size) > width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current += char
        lines.append(current.rstrip())
    overflow = len(lines) > limit or any(_text_width(line, font_size) > width for line in lines)
    lines = lines[:limit]
    if overflow and lines:
        while lines[-1] and _text_width(lines[-1] + "…", font_size) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines, overflow


def _validate(value):
    if not isinstance(value, dict):
        raise ValueError("figure must be a JSON object")
    if not isinstance(value.get("title"), str) or not value["title"].strip():
        raise ValueError("title must be a non-empty string")
    nodes, edges = value.get("nodes"), value.get("edges", [])
    if not isinstance(nodes, list) or not nodes or any(not isinstance(n, dict) for n in nodes):
        raise ValueError("nodes must be a non-empty array of objects")
    ids = [n.get("id") for n in nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("nodes require unique, non-empty string IDs")
    if len(nodes) > 128:
        raise ValueError("a single static figure supports at most 128 nodes; split it into panels")
    for n in nodes:
        if not isinstance(n.get("label"), str) or not n["label"].strip():
            raise ValueError("each node requires a non-empty label")
        detail = n.get("detail", "")
        if not isinstance(detail, str) and not (isinstance(detail, list) and all(isinstance(d, str) for d in detail)):
            raise ValueError("node detail must be a string or an array of strings")
        if n.get("glyph", "none") not in GLYPHS:
            raise ValueError("unknown glyph: " + str(n.get("glyph")))
    grid_flags = [("column" in n, "row" in n) for n in nodes]
    explicit = all(all(flags) for flags in grid_flags)
    if any(any(flags) for flags in grid_flags) and not explicit:
        raise ValueError("partial grid: supply column and row for every node, or omit both everywhere")
    if explicit:
        for n in nodes:
            if any(isinstance(n[k], bool) or not isinstance(n[k], int) or n[k] < 0 for k in ("column", "row")):
                raise ValueError("grid coordinates must be non-negative integers")
        if len({(n["column"], n["row"]) for n in nodes}) != len(nodes):
            raise ValueError("multiple nodes occupy the same grid cell")
    if not isinstance(edges, list) or any(not isinstance(e, dict) for e in edges):
        raise ValueError("edges must be an array of objects")
    normalized = []
    edge_ids = set()
    for index, edge in enumerate(edges):
        e = {**edge, "id": edge.get("id", f"edge-{index + 1}"), "kind": edge.get("kind", "flow")}
        if not isinstance(e["id"], str) or not e["id"] or e["id"] in edge_ids:
            raise ValueError("edge IDs must be unique non-empty strings")
        edge_ids.add(e["id"])
        if e.get("source") not in ids or e.get("target") not in ids or e["source"] == e["target"]:
            raise ValueError("each edge must connect two different existing node IDs")
        if e["kind"] not in KINDS or not isinstance(e.get("label", ""), str):
            raise ValueError("edge kind must be flow, branch, feedback or dependency; label must be a string")
        normalized.append(e)
    groups = value.get("groups", [])
    if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
        raise ValueError("groups must be an array of objects")
    group_ids = set()
    for index, group in enumerate(groups):
        gid = group.get("id", f"group-{index + 1}")
        if not isinstance(gid, str) or not gid or gid in group_ids:
            raise ValueError("group IDs must be unique non-empty strings")
        group_ids.add(gid)
        if not isinstance(group.get("label"), str) or not isinstance(group.get("nodes"), list) or not group["nodes"]:
            raise ValueError("each group requires a label and a non-empty nodes array")
        if any(n not in ids for n in group["nodes"]):
            raise ValueError("group nodes must refer to existing node IDs")
    theme = dict(THEME)
    overrides = value.get("theme", {})
    if not isinstance(overrides, dict) or set(overrides) - set(THEME):
        raise ValueError("theme must contain only known numeric tokens: " + ", ".join(THEME))
    for name, number in overrides.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError("theme tokens must be finite positive numbers: " + name)
        theme[name] = float(number)
    return nodes, normalized, groups, theme, explicit


def _grid(nodes, edges, explicit):
    ids = [n["id"] for n in nodes]
    outgoing, indegree, depth = {i: [] for i in ids}, dict.fromkeys(ids, 0), dict.fromkeys(ids, 0)
    for edge in edges:
        if edge["kind"] != "feedback":
            outgoing[edge["source"]].append(edge["target"])
            indegree[edge["target"]] += 1
    ready = [i for i in ids if indegree[i] == 0]
    visited = []
    while ready:
        current = ready.pop(0)
        visited.append(current)
        for target in outgoing[current]:
            depth[target] = max(depth[target], depth[current] + 1)
            indegree[target] -= 1
        ready = [i for i in ids if indegree[i] == 0 and i not in visited]
    if len(visited) != len(nodes):
        raise ValueError("non-feedback cycle: mark only genuine feedback edges as feedback")
    if explicit:
        return {n["id"]: (n["column"], n["row"]) for n in nodes}
    lanes = defaultdict(int)
    cells = {}
    for node in nodes:
        column = depth[node["id"]]
        cells[node["id"]] = (column, lanes[column])
        lanes[column] += 1
    return cells


def _sides(edge, cells):
    if edge["kind"] == "feedback":
        return "bottom", "bottom"
    source, target = cells[edge["source"]], cells[edge["target"]]
    if target[0] != source[0]:
        return ("right", "left") if target[0] > source[0] else ("left", "right")
    return ("bottom", "top") if target[1] > source[1] else ("top", "bottom")


def _port(rect, side, index, count, theme):
    horizontal = side in {"top", "bottom"}
    span = rect.width if horizontal else rect.height
    step = min(18.0, max(0.0, span - 2 * theme["padding"]) / max(1, count - 1))
    offset = (index - (count - 1) / 2) * step
    if side == "right":
        point, direction = (rect.right, rect.y + rect.height / 2 + offset), (1, 0)
    elif side == "left":
        point, direction = (rect.x, rect.y + rect.height / 2 + offset), (-1, 0)
    elif side == "top":
        point, direction = (rect.x + rect.width / 2 + offset, rect.y), (0, -1)
    else:
        point, direction = (rect.x + rect.width / 2 + offset, rect.bottom), (0, 1)
    tip = tuple(point[k] + direction[k] * theme["port_gap"] for k in (0, 1))
    stub = tuple(point[k] + direction[k] * theme["port_stub"] for k in (0, 1))
    return tip, stub


def _route(start, end, obstacles, theme, extra_y=()):
    """A* on an orthogonal visibility grid, with clearance around every node."""
    if start == end:
        return [start]
    outer = theme["lane_gap"]
    xs = {start[0], end[0]}
    ys = {start[1], end[1], *extra_y}
    for rect in obstacles:
        xs.update((rect.x, rect.right))
        ys.update((rect.y, rect.bottom))
    xs.update((min(xs) - outer, max(xs) + outer))
    ys.update((min(ys) - outer, max(ys) + outer))
    xs, ys = sorted(xs), sorted(ys)
    source = (xs.index(start[0]), ys.index(start[1]), 0)
    goal = (xs.index(end[0]), ys.index(end[1]))
    queue = [(0.0, 0.0, source)]
    costs, previous = {source: 0.0}, {}
    collision_cache = {}
    while queue:
        _, cost, state = heapq.heappop(queue)
        if cost > costs[state] + EPS:
            continue
        ix, iy, direction = state
        if (ix, iy) == goal:
            points = []
            while True:
                points.append((xs[state[0]], ys[state[1]]))
                if state not in previous:
                    return _simplify(list(reversed(points)))
                state = previous[state]
        for nx, ny, nd in ((ix - 1, iy, 1), (ix + 1, iy, 1), (ix, iy - 1, 2), (ix, iy + 1, 2)):
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            a, b = (xs[ix], ys[iy]), (xs[nx], ys[ny])
            key = tuple(sorted((a, b)))
            if key not in collision_cache:
                collision_cache[key] = any(segment_crosses_rect(a, b, rect) for rect in obstacles)
            if collision_cache[key]:
                continue
            next_state = (nx, ny, nd)
            new_cost = cost + abs(a[0] - b[0]) + abs(a[1] - b[1]) + \
                (theme["bend_cost"] if direction and direction != nd else 0)
            if new_cost + EPS < costs.get(next_state, math.inf):
                costs[next_state] = new_cost
                previous[next_state] = state
                heuristic = abs(b[0] - end[0]) + abs(b[1] - end[1])
                heapq.heappush(queue, (new_cost + heuristic, new_cost, next_state))
    raise ValueError("cannot find a clear route; increase node spacing or change the explicit grid")


def _glyph(kind, x, y, size):
    if kind == "none":
        return ""
    pieces = []
    if kind == "matrix":
        for row in range(3):
            for column in range(3):
                pieces.append(f'<rect x="{column * 10}" y="{row * 10}" width="7" height="7" rx="1" fill="#a8bed0" stroke="none"/>')
    elif kind == "mesh":
        pieces.append('<path d="M 1 25 L 5 4 L 18 1 L 29 12 L 24 28 Z M 5 4 L 24 28 M 1 25 L 29 12 M 18 1 L 15 17 L 1 25 M 15 17 L 24 28"/>')
    elif kind == "feature":
        # Equal-height schematic marks encode no invented measurements.
        for offset in (0, 10, 20):
            pieces.append(f'<rect x="{offset}" y="3" width="6" height="24" rx="2" fill="#afc7b8"/>')
    else:
        pieces.append('<rect x="1" y="2" width="27" height="26" rx="4" fill="#e5edf3"/>')
        if kind in {"input", "input-output"}:
            pieces.append('<path d="M -3 15 H 12 M 7 10 L 12 15 L 7 20"/>')
        if kind in {"output", "input-output"}:
            pieces.append('<path d="M 18 15 H 33 M 28 10 L 33 15 L 28 20"/>')
    return f'<g transform="translate({_num(x)} {_num(y)}) scale({_num(size / 30)})" fill="none" stroke="#56738a" stroke-width="1.2" stroke-linejoin="round" aria-hidden="true">' + "".join(pieces) + "</g>"


def _label_placement(edge, rects, occupied, theme):
    label = edge.get("label", "")
    if not label:
        return None
    width = _text_width(label, theme["edge_font"]) + 10
    height = theme["edge_font"] + 8
    segments = list(zip(edge["points"], edge["points"][1:]))
    segments.sort(key=lambda pair: (pair[0][1] != pair[1][1],
                                    -math.dist(pair[0], pair[1])))
    for a, b in segments:
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        for sign in (-1, 1):
            box = Rect(x - width / 2, y + sign * (height / 2 + 4) - height / 2, width, height)
            if box.x >= 4 and box.y >= 54 and not any(_rects_overlap(box, r.expanded(3)) for r in rects) and \
                    not any(_rects_overlap(box, r.expanded(3)) for r in occupied):
                occupied.append(box)
                return box
    return None


def render_method_figure(value, display_width=960):
    """Return (safe SVG, compact mechanical-check summary), without I/O."""
    if isinstance(display_width, bool) or not isinstance(display_width, (int, float)) or not math.isfinite(display_width) or display_width <= 0:
        raise ValueError("display width must be a finite positive number")
    nodes, edges, groups, theme, explicit = _validate(value)
    issues = []
    cells = _grid(nodes, edges, explicit)
    columns = max(c[0] for c in cells.values()) + 1
    rows = max(c[1] for c in cells.values()) + 1
    if columns > 4:
        _issue(issues, "too_many_columns", "超过默认4列；请分面板或改为有语义的多行布局，避免整图缩小。", columns=columns)
    if len(nodes) > 16:
        _issue(issues, "dense_figure", "节点较多；请检查是否应按科学含义分区。", nodes=len(nodes))
    terminal_min = max(2 * theme["arrow_length"], theme["arrow_length"] + theme["min_visible_shaft"])
    minimum_stub = terminal_min + theme["port_gap"]
    if theme["port_stub"] < minimum_stub:
        theme["port_stub"] = minimum_stub
        _issue(issues, "stub_expanded", "已扩大端口轴干以保留箭头头部和可见末段。", severity="info")
    for token in ("column_gutter", "row_gutter"):
        required = 2 * theme["port_stub"] + 2 * theme["port_gap"]
        if theme[token] < required:
            theme[token] = required
            _issue(issues, "gutter_expanded", "已扩大连接走廊，避免短边被箭头占满。", severity="info", token=token)
    rects = {}
    for node in nodes:
        column, row = cells[node["id"]]
        rects[node["id"]] = Rect(theme["margin"] + column * (theme["node_width"] + theme["column_gutter"]),
                                  theme["content_top"] + row * (theme["node_height"] + theme["row_gutter"]),
                                  theme["node_width"], theme["node_height"])
    connections = defaultdict(list)
    for index, edge in enumerate(edges):
        for role, side in zip(("source", "target"), _sides(edge, cells)):
            connections[(edge[role], side)].append((index, role))
    ports = {}
    for (node_id, side), members in connections.items():
        for index, member in enumerate(members):
            ports[member] = _port(rects[node_id], side, index, len(members), theme)
    obstacles = [rect.expanded(theme["obstacle_clearance"]) for rect in rects.values()]
    routes = []
    feedback_index = 0
    for index, edge in enumerate(edges):
        start, source_stub = ports[(index, "source")]
        end, target_stub = ports[(index, "target")]
        if edge["kind"] == "feedback":
            lane = max(r.bottom for r in rects.values()) + theme["lane_gap"] * (feedback_index + 2)
            feedback_index += 1
            left, right = (source_stub[0], lane), (target_stub[0], lane)
            middle = _route(source_stub, left, obstacles, theme, (lane,)) + [right] + \
                _route(right, target_stub, obstacles, theme, (lane,))
        else:
            middle = _route(source_stub, target_stub, obstacles, theme)
        points = _simplify([start, source_stub, *middle, target_stub, end])
        routes.append({**edge, "points": points})
        final_length = math.dist(points[-2], points[-1])
        if final_length + EPS < terminal_min:
            _issue(issues, "short_terminal", "末段不足以容纳箭头和可见轴干；请增大走廊或调整布局。", "error", edge=edge["id"], length=round(final_length, 3))
        for node_id, rect in rects.items():
            if node_id not in (edge["source"], edge["target"]) and any(segment_crosses_rect(a, b, rect) for a, b in zip(points, points[1:])):
                _issue(issues, "edge_node_collision", "连线穿过无关节点。", "error", edge=edge["id"], node=node_id)
    group_boxes = []
    for group in groups:
        members = [rects[node_id] for node_id in group["nodes"]]
        box = Rect(min(r.x for r in members) - 12, min(r.y for r in members) - 32,
                   max(r.right for r in members) - min(r.x for r in members) + 24,
                   max(r.bottom for r in members) - min(r.y for r in members) + 44)
        group_boxes.append((group, box))
        if any(_rects_overlap(box, rect) for node_id, rect in rects.items() if node_id not in group["nodes"]):
            _issue(issues, "group_encloses_unrelated_node", "分区包围盒包含了未归入分区的节点；请调整网格或分组。", group=group.get("id", group["label"]))
    occupied_labels = []
    labels = []
    for edge in routes:
        box = _label_placement(edge, list(rects.values()), occupied_labels, theme)
        if edge.get("label") and box is None:
            _issue(issues, "edge_label_no_space", "边标签没有可用空白区；可见标签已省略，原文保留在SVG title中。", "error", edge=edge["id"])
        if box:
            labels.append((edge, box))
    all_x = [r.right for r in rects.values()] + [p[0] for e in routes for p in e["points"]] + [b.right for _, b in group_boxes] + [b.right for _, b in labels]
    all_y = [r.bottom for r in rects.values()] + [p[1] for e in routes for p in e["points"]] + [b.bottom for _, b in group_boxes] + [b.bottom for _, b in labels]
    # Rebase rare outside routes rather than clipping or hiding a collision.
    minimum_x = min([r.x for r in rects.values()] + [p[0] for e in routes for p in e["points"]] + [b.x for _, b in group_boxes] + [b.x for _, b in labels])
    minimum_y = min([r.y for r in rects.values()] + [p[1] for e in routes for p in e["points"]] + [b.y for _, b in group_boxes])
    natural_width = max(all_x) - minimum_x + 2 * theme["margin"]
    width = max(theme["canvas_min_width"], natural_width)
    # The minimum canvas keeps typography at its intended scale for small
    # graphs; distribute its extra room evenly rather than leaving a blank
    # right half. A 640px three-column recipe is node_width=160, margin=24,
    # column_gutter=54, canvas_min_width=640. No font shrinking is involved.
    shift_x = theme["margin"] - minimum_x + (width - natural_width) / 2
    shift_y = max(0.0, 64.0 - minimum_y)
    height = max(all_y) + theme["margin"] + shift_y
    scale = display_width / width
    screen_fonts = {name: round(theme[name] * scale, 3) for name in ("label_font", "detail_font", "title_font", "edge_font", "group_font")}
    used_fonts = ["label_font", "title_font"]
    if any(n.get("detail") for n in nodes):
        used_fonts.append("detail_font")
    if labels:
        used_fonts.append("edge_font")
    if groups:
        used_fonts.append("group_font")
    for name in used_fonts:
        minimum = 14 if name == "title_font" else 12
        if theme[name] * scale + EPS < minimum:
            _issue(issues, "small_screen_text", "目标显示宽度下字号不足；请分区或改布局，不要继续缩字。", "error", token=name, screen_px=screen_fonts[name], minimum_px=minimum)
    screen_stroke = theme["line_width"] * scale
    if not 1.2 - EPS <= screen_stroke <= 1.8 + EPS:
        _issue(issues, "screen_stroke_range", "主连线超出阅读器建议的1.2–1.8屏幕像素范围。", screen_px=round(screen_stroke, 3))
    if not 6 - EPS <= theme["arrow_length"] * scale <= 8 + EPS:
        _issue(issues, "screen_arrow_range", "箭头头长超出阅读器建议的6–8屏幕像素范围。", screen_px=round(theme["arrow_length"] * scale, 3))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(display_width)}" height="{_num(height * scale)}" viewBox="0 0 {_num(width)} {_num(height)}" role="img" aria-labelledby="figure-title figure-desc">',
           f'<title id="figure-title">{_xml(value["title"])}</title>',
           '<desc id="figure-desc">静态方法示意图。节点及连线由输入声明；符号为示意，不编码额外数据。</desc>',
           '<defs>']
    for kind in sorted(KINDS):
        length, half = theme["arrow_length"], theme["arrow_width"] / 2
        out.append(f'<marker id="arrow-{kind}" markerUnits="userSpaceOnUse" markerWidth="{_num(length)}" markerHeight="{_num(2 * half)}" viewBox="0 {_num(-half)} {_num(length)} {_num(2 * half)}" refX="{_num(length)}" refY="0" orient="auto"><path d="M 0 {_num(-half)} L {_num(length)} 0 L 0 {_num(half)} Z" fill="{COLORS[kind]}"/></marker>')
    out.extend(['</defs>', f'<rect width="{_num(width)}" height="{_num(height)}" fill="#ffffff"/>',
                f'<g font-family="{_xml(FONT)}" fill="#253747">',
                f'<text x="{_num(theme["margin"])}" y="37" font-size="{_num(theme["title_font"])}" font-weight="600">{_xml(value["title"])}</text>',
                f'<g transform="translate({_num(shift_x)} {_num(shift_y)})">'])
    if _text_width(value["title"], theme["title_font"]) > width - 2 * theme["margin"]:
        _issue(issues, "title_overflow", "标题超过可用宽度；请缩短标题。", "error")
    for group, box in group_boxes:
        out.append(f'<g data-group="{_xml(group.get("id", group["label"]))}"><rect x="{_num(box.x)}" y="{_num(box.y)}" width="{_num(box.width)}" height="{_num(box.height)}" rx="12" fill="#f5f8fa" stroke="#d4dfe6"/><text x="{_num(box.x + 12)}" y="{_num(box.y + 21)}" font-size="{_num(theme["group_font"])}" fill="#576b7a">{_xml(group["label"])}</text></g>')
        if _text_width(group["label"], theme["group_font"]) > box.width - 24:
            _issue(issues, "group_label_overflow", "分区标题超过包围盒宽度。", "error", group=group.get("id", group["label"]))
    for edge in routes:
        d = "M " + " L ".join(f"{_num(x)} {_num(y)}" for x, y in edge["points"])
        dash = ' stroke-dasharray="5 4"' if edge["kind"] in {"feedback", "dependency"} else ""
        out.append(f'<path data-edge="{_xml(edge["id"])}" data-source="{_xml(edge["source"])}" data-target="{_xml(edge["target"])}" data-kind="{edge["kind"]}" d="{d}" fill="none" stroke="{COLORS[edge["kind"]]}" stroke-width="{_num(theme["line_width"])}" stroke-linejoin="round" marker-end="url(#arrow-{edge["kind"]})"{dash}><title>{_xml(edge.get("label", ""))}</title></path>')
    for index, node in enumerate(nodes):
        rect = rects[node["id"]]
        glyph = node.get("glyph", "none")
        inset = theme["padding"] + (theme["glyph_size"] + 10 if glyph != "none" else 0)
        label_width = rect.width - inset - theme["padding"]
        label_lines, overflow = _wrap(node["label"], label_width, theme["label_font"], 2)
        detail = node.get("detail", "")
        detail = "\n".join(detail) if isinstance(detail, list) else detail
        detail_lines, detail_overflow = _wrap(detail, rect.width - 2 * theme["padding"], theme["detail_font"], 2)
        if overflow or detail_overflow:
            _issue(issues, "node_text_overflow", "节点文字超过两行容量；请缩短标签/细节或分区。可见文字已省略，原文保留在SVG title中。", "error", node=node["id"])
        out.append(f'<g id="node-{index}" data-node="{_xml(node["id"])}"><title>{_xml(node["label"] + ("：" + detail if detail else ""))}</title><rect data-node-box="{_xml(node["id"])}" x="{_num(rect.x)}" y="{_num(rect.y)}" width="{_num(rect.width)}" height="{_num(rect.height)}" rx="9" fill="#ffffff" stroke="#a9bccb" stroke-width="1.1"/>')
        glyph_y = rect.y + 21 if detail else rect.y + (rect.height - theme["glyph_size"]) / 2
        out.append(_glyph(glyph, rect.x + theme["padding"], glyph_y, theme["glyph_size"]))
        label_y = rect.y + (34 if detail else rect.height / 2 - (len(label_lines) - 1) * theme["label_font"] * 0.68 + theme["label_font"] * 0.35)
        for line_index, line in enumerate(label_lines):
            out.append(f'<text x="{_num(rect.x + inset)}" y="{_num(label_y + line_index * theme["label_font"] * 1.35)}" font-size="{_num(theme["label_font"])}" font-weight="600">{_xml(line)}</text>')
        for line_index, line in enumerate(detail_lines if detail else []):
            out.append(f'<text x="{_num(rect.x + theme["padding"])}" y="{_num(rect.bottom - 34 + line_index * theme["detail_font"] * 1.4)}" font-size="{_num(theme["detail_font"])}" fill="#536776">{_xml(line)}</text>')
        if label_width <= 0 or rect.height < (2 * theme["label_font"] * 1.35 + 2 * theme["detail_font"] * 1.4 + 24 if detail else 2 * theme["label_font"] * 1.35 + 24):
            _issue(issues, "node_internal_space", "节点内部空间不足以容纳当前字号；请增加节点尺寸。", "error", node=node["id"])
        out.append('</g>')
    for edge, box in labels:
        out.append(f'<g data-edge-label="{_xml(edge["id"])}"><rect x="{_num(box.x)}" y="{_num(box.y)}" width="{_num(box.width)}" height="{_num(box.height)}" rx="3" fill="#ffffff"/><text x="{_num(box.x + box.width / 2)}" y="{_num(box.y + box.height / 2 + theme["edge_font"] * 0.35)}" text-anchor="middle" font-size="{_num(theme["edge_font"])}" fill="{COLORS[edge["kind"]]}">{_xml(edge["label"])}</text></g>')
    out.append('</g></g></svg>')
    summary = {
        "nodes": len(nodes), "edges": len(edges), "grid": {"columns": columns, "rows": rows},
        "view_box": [0, 0, round(width, 3), round(height, 3)], "display_width": display_width,
        "screen": {"fonts_px": screen_fonts, "line_width_px": round(screen_stroke, 3),
                   "arrow_length_px": round(theme["arrow_length"] * scale, 3)},
        "issue_count": len(issues), "error_count": sum(i["severity"] == "error" for i in issues),
        "issues": issues,
    }
    return "\n".join(out) + "\n", summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="local JSON method figure")
    parser.add_argument("--output", required=True, type=Path, help="static SVG destination")
    parser.add_argument("--display-width", type=float, default=960, help="intended displayed width in CSS pixels (default: 960)")
    parser.add_argument("--strict", action="store_true", help="write SVG but exit 2 if mechanical errors are reported")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.input.read_text(encoding="utf-8-sig"))
        svg, summary = render_method_figure(value, args.display_width)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(1, "render_method_figure: " + str(error) + "\n")
    # ASCII escaping makes stdout JSON portable on legacy Windows consoles.
    print(json.dumps({"output": str(args.output), **summary}, ensure_ascii=True, separators=(",", ":")))
    return 2 if args.strict and summary["error_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
