"""Mechanical contracts of the local static SVG renderer, not a beauty score."""
import copy
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from render_method_figure import Rect, THEME, render_method_figure, segment_crosses_rect

NS = {"s": "http://www.w3.org/2000/svg"}


def chain(size):
    return {"title": "逐阶段处理示意", "nodes": [
        {"id": f"n{i}", "label": ["输入网格", "局部编码", "特征融合", "输出结果"][i % 4],
         "detail": "方法中声明的处理步骤", "glyph": ["mesh", "matrix", "feature", "output"][i % 4]}
        for i in range(size)],
        "edges": [{"source": f"n{i}", "target": f"n{i + 1}", "kind": "flow"} for i in range(size - 1)]}


def parallel():
    return {"title": "并行编码与反馈示意", "nodes": [
        {"id": "input", "label": "输入样本", "detail": "已声明的观测", "glyph": "input"},
        {"id": "shape", "label": "几何编码", "glyph": "mesh"},
        {"id": "texture", "label": "纹理编码", "glyph": "matrix"},
        {"id": "join", "label": "合并表征", "glyph": "feature"},
        {"id": "output", "label": "输出", "glyph": "input-output"}], "edges": [
        {"source": "input", "target": "shape", "kind": "branch"},
        {"source": "input", "target": "texture", "kind": "branch"},
        {"source": "shape", "target": "join", "kind": "flow"},
        {"source": "texture", "target": "join", "kind": "flow"},
        {"source": "join", "target": "output", "kind": "flow"},
        {"source": "output", "target": "input", "kind": "feedback", "label": "迭代"}]}


def parse(svg):
    root = ET.fromstring(svg)
    boxes = {r.attrib["data-node-box"]: Rect(*[float(r.attrib[k]) for k in ("x", "y", "width", "height")])
             for r in root.findall(".//s:rect[@data-node-box]", NS)}
    paths = []
    for element in root.findall(".//s:path[@data-edge]", NS):
        values = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", element.attrib["d"])]
        paths.append((element.attrib, list(zip(values[::2], values[1::2]))))
    return root, boxes, paths


class StaticMethodFigureTests(unittest.TestCase):
    def assert_routes_clear_and_terminal_visible(self, svg, arrow_length=7):
        _, boxes, paths = parse(svg)
        for edge, points in paths:
            self.assertGreaterEqual(math.dist(points[-2], points[-1]), max(2 * arrow_length, arrow_length + 12) - 0.001)
            for node_id, rect in boxes.items():
                if node_id not in (edge["data-source"], edge["data-target"]):
                    self.assertFalse(any(segment_crosses_rect(a, b, rect) for a, b in zip(points, points[1:])), (edge, node_id))

    def test_absolute_arrow_dimensions_do_not_multiply_with_stroke_width(self):
        marker_attributes = []
        for line_width in (1.2, 1.5, 3.5):
            figure = chain(2)
            figure["theme"] = {"line_width": line_width}
            svg, _ = render_method_figure(figure)
            root, _, paths = parse(svg)
            markers = root.findall("s:defs/s:marker", NS)
            self.assertEqual(len(markers), 4)
            for marker in markers:
                self.assertEqual(marker.attrib["markerUnits"], "userSpaceOnUse")
                self.assertEqual(float(marker.attrib["markerWidth"]), 7)
                self.assertEqual(float(marker.attrib["markerHeight"]), 7)
                self.assertEqual(float(marker.attrib["refX"]), 7)
            self.assertEqual(float(paths[0][0]["stroke-width"]), line_width)
            marker_attributes.append([m.attrib for m in markers])
        self.assertEqual(marker_attributes[0], marker_attributes[1])
        self.assertEqual(marker_attributes[1], marker_attributes[2])

    def test_short_and_long_edges_keep_visible_terminal_and_avoid_obstacles(self):
        value = chain(4)
        value["edges"].append({"source": "n0", "target": "n3", "kind": "dependency"})
        svg, summary = render_method_figure(value)
        self.assert_routes_clear_and_terminal_visible(svg)
        _, _, paths = parse(svg)
        direct = paths[0][1]
        long_path = paths[-1][1]
        self.assertGreater(sum(math.dist(a, b) for a, b in zip(long_path, long_path[1:])), 3 * math.dist(direct[0], direct[-1]))
        self.assertGreater(len(long_path), 2, "long dependency must route around middle nodes")
        self.assertEqual(summary["error_count"], 0)

    def test_parallel_join_and_feedback_preserve_declared_topology(self):
        value = parallel()
        original = copy.deepcopy(value)
        svg, summary = render_method_figure(value)
        self.assertEqual(value, original)
        self.assertEqual(summary["grid"], {"columns": 4, "rows": 2})
        _, boxes, paths = parse(svg)
        self.assertEqual(boxes["shape"].x, boxes["texture"].x)
        self.assertLess(boxes["shape"].y, boxes["texture"].y)
        self.assertGreater(boxes["join"].x, boxes["shape"].x)
        self.assertEqual([(p[0]["data-source"], p[0]["data-target"], p[0]["data-kind"]) for p in paths],
                         [(e["source"], e["target"], e["kind"]) for e in value["edges"]])
        feedback = paths[-1][1]
        self.assertGreater(max(p[1] for p in feedback), max(b.bottom for b in boxes.values()))
        self.assert_routes_clear_and_terminal_visible(svg)
        self.assertEqual(summary["error_count"], 0, summary["issues"])

    def test_two_different_contents_and_node_counts_are_deterministic(self):
        for figure in (chain(2), chain(4), parallel()):
            first = render_method_figure(figure)
            self.assertEqual(first, render_method_figure(figure))
            self.assert_routes_clear_and_terminal_visible(first[0])
            self.assertEqual(first[1]["error_count"], 0, first[1]["issues"])

    def test_xml_text_and_attributes_are_safe_in_every_author_field(self):
        danger = '<script onload="alert(1)">&\u0001'
        value = {"title": danger, "nodes": [
            {"id": danger, "label": danger, "detail": danger},
            {"id": "b", "label": "结果"}], "edges": [
            {"id": danger, "source": danger, "target": "b", "label": danger}],
            "groups": [{"id": danger, "label": danger, "nodes": [danger]}]}
        svg, _ = render_method_figure(value)
        root, boxes, paths = parse(svg)
        self.assertEqual(root.find("s:title", NS).text, danger.replace("\u0001", ""))
        self.assertNotIn("<script", svg)
        self.assertNotIn("onload=\"", svg)
        self.assertNotIn("foreignObject", svg)
        self.assertEqual(paths[0][0]["data-source"], danger.replace("\u0001", ""))
        self.assertIn(danger.replace("\u0001", ""), boxes)

    def test_narrow_target_reports_effective_screen_font_without_shrinking_tokens(self):
        svg, summary = render_method_figure(chain(4), display_width=480)
        problems = [i for i in summary["issues"] if i["code"] == "small_screen_text"]
        self.assertTrue(problems)
        self.assertTrue(any(i["token"] == "detail_font" and i["screen_px"] < 12 for i in problems))
        self.assertEqual(summary["screen"]["fonts_px"]["detail_font"], 6.5)
        root = ET.fromstring(svg)
        self.assertEqual(float(root.attrib["width"]), 480)
        self.assertIn('font-size="13"', svg)

    def test_three_column_compact_tokens_fit_640_without_small_text(self):
        figure = chain(3)
        figure["theme"] = {"canvas_min_width": 640, "node_width": 160,
                           "column_gutter": 54, "margin": 24}
        svg, summary = render_method_figure(figure, display_width=640)
        self.assertEqual(summary["error_count"], 0, summary["issues"])
        self.assertEqual(summary["view_box"][2], 640)
        self.assertEqual(summary["screen"]["fonts_px"]["detail_font"], 13)
        self.assert_routes_clear_and_terminal_visible(svg)

    def test_small_graph_is_centered_in_minimum_canvas(self):
        svg, summary = render_method_figure(chain(2))
        root, boxes, _ = parse(svg)
        graph = root.find("s:g/s:g[@transform]", NS)
        shift = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", graph.attrib["transform"])]
        left = min(b.x for b in boxes.values()) + shift[0]
        right = max(b.right for b in boxes.values()) + shift[0]
        self.assertAlmostEqual(left, summary["view_box"][2] - right)

    def test_small_gutters_expand_instead_of_hiding_arrow_or_shortening_shaft(self):
        value = parallel()
        value["theme"] = {"column_gutter": 4, "row_gutter": 4, "port_stub": 5}
        svg, summary = render_method_figure(value)
        self.assert_routes_clear_and_terminal_visible(svg)
        codes = {i["code"] for i in summary["issues"]}
        self.assertIn("gutter_expanded", codes)
        self.assertIn("stub_expanded", codes)
        self.assertNotIn("short_terminal", codes)
        self.assertNotIn("edge_node_collision", codes)

    def test_explicit_multiline_grid_and_multiple_feedback_routes_are_clear(self):
        value = chain(6)
        for node, cell in zip(value["nodes"], [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]):
            node["column"], node["row"] = cell
        value["edges"].extend([
            {"source": "n5", "target": "n0", "kind": "feedback"},
            {"source": "n4", "target": "n1", "kind": "feedback"}])
        svg, summary = render_method_figure(value)
        self.assertEqual(summary["grid"], {"columns": 3, "rows": 2})
        self.assert_routes_clear_and_terminal_visible(svg)

    def test_overflow_and_wide_sequence_request_splitting_without_font_reduction(self):
        value = chain(6)
        value["nodes"][0]["detail"] = "不应该自动缩小字号以容纳过多的科学描述" * 8
        svg, summary = render_method_figure(value)
        codes = {i["code"] for i in summary["issues"]}
        self.assertIn("too_many_columns", codes)
        self.assertIn("node_text_overflow", codes)
        self.assertIn("small_screen_text", codes)
        self.assertIn("…", svg)
        self.assertIn(value["nodes"][0]["detail"], svg)
        self.assertIn('font-size="13"', svg)

    def test_invalid_grid_cycle_references_and_theme_are_errors(self):
        invalid = []
        value = chain(2); value["nodes"][0]["row"] = 0; invalid.append(value)
        value = chain(2); value["edges"].append({"source": "n1", "target": "n0"}); invalid.append(value)
        value = chain(2); value["edges"][0]["target"] = "missing"; invalid.append(value)
        value = chain(2); value["theme"] = {"line_width": float("nan")}; invalid.append(value)
        value = chain(2); value["theme"] = {"unknown": 1}; invalid.append(value)
        for value in invalid:
            with self.assertRaises(ValueError):
                render_method_figure(value)

    def test_cli_summary_strict_exit_and_invalid_input_preserve_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "figure.json", Path(directory) / "figure.svg"
            source.write_text(json.dumps(chain(4), ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, str(SCRIPTS / "render_method_figure.py"), "--input", str(source), "--output", str(target)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["issue_count"], 0)
            self.assertTrue(target.read_text(encoding="utf-8").startswith("<svg"))
            run = subprocess.run(command + ["--display-width", "400", "--strict"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertGreater(json.loads(run.stdout)["error_count"], 0)
            previous = target.read_bytes()
            source.write_text('{"nodes":[]}', encoding="utf-8")
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(target.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
