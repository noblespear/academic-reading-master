"""Layout preparation emits existing method_pipeline v1 data, not new syntax."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_method_pipeline import prepare_method_pipeline
from test_method_pipeline import example, pipeline


def skeleton():
    aid = pipeline(example())
    for node in aid["config"]["nodes"]:
        del node["column"], node["row"]
    return aid


class PrepareMethodPipelineTests(unittest.TestCase):
    def test_parallel_join_and_feedback_have_deterministic_grid_without_mutation(self):
        aid = skeleton()
        original = copy.deepcopy(aid)
        prepared = prepare_method_pipeline(aid)
        cells = {n["id"]: (n["column"], n["row"]) for n in prepared["config"]["nodes"]}
        self.assertEqual(cells, {"input": (0, 0), "prepare": (1, 0), "contour": (2, 0),
            "texture": (2, 1), "merge": (3, 0), "check": (4, 0)})
        self.assertEqual(aid, original)
        self.assertEqual(prepared["config"]["edges"], original["config"]["edges"])
        self.assertEqual(prepared, prepare_method_pipeline(aid))
        self.assertEqual(prepare_method_pipeline(aid["config"]), prepared["config"])

    def test_explicit_grid_and_navigation_order_are_retained_exactly(self):
        aid = pipeline(example())
        self.assertEqual(prepare_method_pipeline(aid), aid)
        aid["config"]["nodes"][0]["column"] = 39
        self.assertEqual(prepare_method_pipeline(aid), aid)

    def test_unmarked_cycle_is_an_error_even_with_explicit_grid(self):
        for aid in (skeleton(), pipeline(example())):
            next(e for e in aid["config"]["edges"] if e["kind"] == "feedback")["kind"] = "flow"
            with self.assertRaisesRegex(ValueError, "non-feedback cycle"):
                prepare_method_pipeline(aid)

    def test_partial_grid_unknown_reference_and_invalid_final_data_are_errors(self):
        aid = skeleton()
        aid["config"]["nodes"][0]["row"] = 0
        with self.assertRaisesRegex(ValueError, "partial explicit grid"):
            prepare_method_pipeline(aid)
        aid = skeleton()
        aid["config"]["edges"][0]["to"] = "missing"
        with self.assertRaisesRegex(ValueError, "refer to existing nodes"):
            prepare_method_pipeline(aid)
        aid = skeleton()
        del aid["config"]["nodes"][0]["summary"]
        with self.assertRaisesRegex(ValueError, "invalid final"):
            prepare_method_pipeline(aid)

    def test_two_and_forty_modules_support_chain_and_parallel_without_authoring_cap(self):
        for size in (2, 8, 40):
            nodes = [dict(id="n"+str(i), label="阶段"+str(i), summary="本阶段的概要。", inputs="前一阶段的结果。", outputs="下一阶段的输入。") for i in range(size)]
            edges = [dict(id="e"+str(i), **{"from": "n"+str(i), "to": "n"+str(i+1)}, kind="flow", label="交给下一阶段", flow="传入结果。", cause="下一阶段需要此结果。") for i in range(size-1)]
            config = prepare_method_pipeline(dict(summary="自拟链条，仅供布局测试。", nodes=nodes, edges=edges))
            self.assertEqual([n["column"] for n in config["nodes"]], list(range(size)))
            self.assertEqual([n["row"] for n in config["nodes"]], [0]*size)
        nodes = copy.deepcopy(nodes)
        for node in nodes:
            node.pop("row", None); node.pop("column", None)
        edges = [dict(edges[0], id="p"+str(i), **{"from":"n0", "to":"n"+str(i)}) for i in range(1,40)]
        config = prepare_method_pipeline(dict(summary="自拟并行模块。", nodes=nodes, edges=edges))
        self.assertEqual([n["row"] for n in config["nodes"][1:]], list(range(39)))

    def test_cli_same_shape_and_error_does_not_replace_output(self):
        artifacts = ROOT.parent/"validation/v3.9.5/pipeline"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as directory:
            source, target = Path(directory)/"input.json", Path(directory)/"output.json"
            source.write_text(json.dumps(skeleton()), encoding="utf-8")
            command = [sys.executable, str(ROOT/"scripts/prepare_method_pipeline.py"), "--input", str(source), "--output", str(target)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            prepared = target.read_bytes()
            self.assertEqual(json.loads(prepared), prepare_method_pipeline(skeleton()))
            broken = skeleton(); broken["config"]["nodes"][0]["column"] = 0
            source.write_text(json.dumps(broken), encoding="utf-8")
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn("partial explicit grid", run.stderr)
            self.assertEqual(target.read_bytes(), prepared)


if __name__ == "__main__":
    unittest.main()
