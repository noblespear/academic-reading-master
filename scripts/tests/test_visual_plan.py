"""Declared new-draft visual commitments cannot be silently downgraded."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_visual_plan import main, read_data, read_plan, validate_visual_plan


def component():
    return {"id": "graph-aid", "type": "graph_walkthrough", "config": {
        "nodes": [{"id": "a"}, {"id": "b"}], "edges": [],
        "stages": [{"active_node_ids": ["a"]}, {"active_node_ids": ["a", "b"]}]}}


def pipeline_config():
    return {"summary": "把输入整理后交给下游处理。", "nodes": [
        {"id": "a", "label": "整理", "summary": "将原始输入组织成可处理记录。",
         "inputs": "原始记录", "outputs": "整理好的记录", "column": 0, "row": 0},
        {"id": "b", "label": "处理", "summary": "使用整理好的记录生成结果。",
         "inputs": "整理好的记录", "outputs": "处理结果", "column": 1, "row": 0}],
        "edges": [{"id": "a-b", "from": "a", "to": "b", "kind": "flow", "label": "提交记录",
                   "flow": "传递整理好的记录", "cause": "处理阶段需要统一格式的输入才能生成结果。"}]}


def data():
    anchor = {"id": "anchor", "type": "paragraph", "learning": {"blocks": [
        {"id": "plain-fig", "type": "diagram", "src": "assets/plain.svg"},
        {"id": "plain-aid", "type": "learning_aid", "aid_id": "graph-aid"}],
        "aid_ids": ["graph-aid"]}}
    return {"overview": {"sections": [{"id": "method", "blocks": [anchor,
        {"id": "formula", "type": "formula"},
        {"id": "fig", "type": "figure", "src": "assets/figure.svg"},
        {"id": "table", "type": "table", "rows": [["A", "B"]]},
        {"id": "demo", "type": "learning_aid", "placement": "body", "aid": component()}]}]},
        "explanation": {"sections": []}, "learning_catalog": {"aids": [component()]}}


def entry(**changes):
    row = {"id": "visual-1", "view": "overview", "section_id": "method",
           "anchor_block_id": "anchor", "layer": "body", "decision": "static",
           "reason": "解释这个节点的关系", "target_block_ids": ["fig"], "status": "done"}
    row.update(changes)
    return row


def interaction_review(task="observe", **changes):
    review = {"task": task, "reason": "查看节点之间的关系"}
    if task in ("trace", "change"):
        review.update(action="切换阶段", observable_change="高亮节点随阶段变化")
    review.update(changes)
    return review


class VisualPlanTests(unittest.TestCase):
    def assert_bad(self, plan, paper=None, contains=None):
        errors = validate_visual_plan(plan, paper or data())
        self.assertTrue(errors)
        if contains:
            self.assertTrue(any(contains in e for e in errors), errors)

    def test_fulfilled_static_interactive_table_and_text(self):
        rows = [entry(), entry(id="interactive", decision="interactive", target_block_ids=["demo"]),
                entry(id="table-plan", decision="table", target_block_ids=["table"]),
                entry(id="text-plan", decision="text", target_block_ids=[])]
        self.assertEqual(validate_visual_plan(rows, data()), [])

    def test_plain_inline_and_registered_catalog_id(self):
        rows = [entry(layer="plain", target_block_ids=["plain-fig"]),
                entry(id="plain-interactive", layer="plain", decision="interactive", target_block_ids=["plain-aid", "graph-aid"])]
        self.assertEqual(validate_visual_plan(rows, data()), [])

    def test_body_catalog_reference(self):
        paper = data()
        demo = paper["overview"]["sections"][0]["blocks"][-1]
        demo.pop("aid"); demo["aid_id"] = "graph-aid"
        self.assertEqual(validate_visual_plan([entry(decision="interactive", target_block_ids=["demo"])], paper), [])

    def test_missing_target_and_empty_targets(self):
        self.assert_bad([entry(target_block_ids=[])], contains="no target")
        self.assert_bad([entry(target_block_ids=["missing"])], contains="does not exist")

    def test_no_cross_layer(self):
        self.assert_bad([entry(target_block_ids=["plain-fig"])], contains="section/layer/anchor")
        self.assert_bad([entry(layer="plain")], contains="section/layer/anchor")

    def test_no_cross_view_or_section(self):
        paper = data()
        paper["explanation"]["sections"] = copy.deepcopy(paper["overview"]["sections"])
        paper["overview"]["sections"][0]["blocks"].pop(2)
        self.assert_bad([entry()], paper, "does not exist")
        paper = data(); section = paper["overview"]["sections"][0]
        figure = section["blocks"].pop(2)
        paper["overview"]["sections"].append({"id": "elsewhere", "blocks": [figure]})
        self.assert_bad([entry()], paper, "does not exist")

    def test_no_other_anchor_plain_targets(self):
        paper = data()
        body = paper["overview"]["sections"][0]["blocks"]
        body.append({"id": "other", "type": "paragraph", "learning": body[0].pop("learning")})
        self.assert_bad([entry(layer="plain", target_block_ids=["plain-fig"])], paper, "does not exist")

    def test_no_hidden_body_aid_or_details_target(self):
        paper = data(); body = paper["overview"]["sections"][0]["blocks"]
        body[-1].pop("placement")
        self.assert_bad([entry(decision="interactive", target_block_ids=["demo"])], paper, "hidden")
        paper = data(); body = paper["overview"]["sections"][0]["blocks"]
        figure = body.pop(2); body.append({"id": "folded", "type": "details", "blocks": [figure]})
        self.assert_bad([entry()], paper, "does not exist")

    def test_plain_nested_details_cannot_fulfill(self):
        paper = data(); learning = paper["overview"]["sections"][0]["blocks"][0]["learning"]
        figure = learning["blocks"].pop(0)
        learning["blocks"].append({"id": "folded", "type": "details", "blocks": [figure]})
        self.assert_bad([entry(layer="plain", target_block_ids=["plain-fig"])], paper, "does not exist")

    def test_static_cannot_impersonate_interactive(self):
        self.assert_bad([entry(decision="interactive")], contains="interactive cannot")

    def test_steps_text_carousel_cannot_impersonate_interactive(self):
        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        aid.update(type="steps", config={"steps": [{"text": "one"}, {"text": "two"}]})
        self.assert_bad([entry(decision="interactive", target_block_ids=["demo"])], paper, "graphical states")

    def test_graphical_steps_and_alias_are_supported_but_text_changes_do_not_count(self):
        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        row = entry(decision="interactive", target_block_ids=["demo"])
        for kind in ("steps", "step_walkthrough"):
            aid.update(type=kind, config={"steps": [
                {"blocks": [{"type": "figure", "src": "assets/before.svg"}]},
                {"blocks": [{"type": "diagram", "asset": "assets/after.svg"}]}]})
            self.assertEqual(validate_visual_plan([row], paper), [])
        aid["config"] = {"steps": [
            {"text": "before", "blocks": [{"type": "figure", "src": "assets/same.svg"}]},
            {"text": "after", "blocks": [{"type": "diagram", "asset": "assets/same.svg"}]}]}
        self.assert_bad([row], paper, "graphical states")
        aid["config"] = {"steps": [
            {"blocks": [{"type": "formula", "latex": "x=1"}]},
            {"blocks": [{"type": "formula", "latex": "x=2"}]}]}
        self.assert_bad([row], paper, "graphical states")
        aid["config"] = {"steps": [
            {"blocks": [{"type": "figure", "src": "assets/one.svg"}]},
            {"blocks": [{"type": "paragraph", "text": "more text"}]}]}
        self.assert_bad([row], paper, "graphical states")

    def test_steps_tables_must_change_rows(self):
        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        row = entry(decision="interactive", target_block_ids=["demo"])
        aid.update(type="steps", config={"steps": [
            {"blocks": [{"type": "table", "rows": [["A", 1]]}]},
            {"blocks": [{"type": "table", "rows_zh": [["A", 2]]}]}]})
        self.assertEqual(validate_visual_plan([row], paper), [])
        aid["config"]["steps"][1]["blocks"][0]["rows_zh"] = [["A", 1]]
        self.assert_bad([row], paper, "graphical states")

    def test_graph_only_changing_text_is_rejected(self):
        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        aid["config"]["stages"] = [{"text": "one", "active_node_ids": ["a"]}, {"text": "two", "active_node_ids": ["a"]}]
        self.assert_bad([entry(decision="interactive", target_block_ids=["demo"])], paper, "only changes text")

    def test_method_pipeline_supported(self):
        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        aid.update(type="method_pipeline", config=pipeline_config())
        row = entry(decision="interactive", target_block_ids=["demo"])
        self.assertEqual(validate_visual_plan([row], paper), [])
        block = paper["overview"]["sections"][0]["blocks"][-1]
        block.pop("aid"); block["aid_id"] = aid["id"]
        # Replace the pre-existing graph of the same ID to keep the reference unique.
        paper["learning_catalog"]["aids"] = [aid]
        self.assertEqual(validate_visual_plan([row], paper), [])

    def test_pipeline_requires_node_summary_inputs_and_outputs(self):
        row = entry(decision="interactive", target_block_ids=["demo"])
        for field in ("summary", "inputs", "outputs"):
            for invalid in (None, "", "   ", [], 42):
                for index in (0, 1):
                    with self.subTest(field=field, invalid=invalid, index=index):
                        paper = data(); aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
                        config = pipeline_config(); config["nodes"][index][field] = invalid
                        config["nodes"][index].update(role="旧作用", why_needed="旧理由", handoff="旧交接")
                        aid.update(type="method_pipeline", config=config)
                        self.assert_bad([row], paper, f"node[{index}].{field}")

    def test_pipeline_requires_edge_flow_and_cause_in_inline_and_catalog(self):
        row = entry(decision="interactive", target_block_ids=["demo"])
        for field in ("flow", "cause"):
            for invalid in (None, "", "   ", {}, 42):
                for catalog_reference in (False, True):
                    with self.subTest(field=field, invalid=invalid, catalog=catalog_reference):
                        paper = data(); block = paper["overview"]["sections"][0]["blocks"][-1]
                        config = pipeline_config(); config["edges"][0][field] = invalid
                        aid = {"id": "pipeline-aid", "type": "method_pipeline", "config": config}
                        if catalog_reference:
                            block.pop("aid"); block["aid_id"] = aid["id"]
                            paper["learning_catalog"]["aids"].append(aid)
                        else:
                            block["aid"] = aid
                        self.assert_bad([row], paper, "edge[0]." + field)

    def test_require_pipeline_registration_is_opt_in_and_kind_is_required(self):
        rows = [entry()]
        self.assertEqual(validate_visual_plan(rows, data()), [])
        self.assertTrue(any("kind interactive_pipeline" in e for e in validate_visual_plan(rows, data(), True)))
        paper = data(); paper["learning_requirements"] = {"overview_framework": {"section_id": "method", "block_id": "demo"}}
        self.assertTrue(any("kind interactive_pipeline" in e for e in validate_visual_plan(rows, paper, True)))

    def test_require_pipeline_needs_matching_completed_plan_and_real_component(self):
        paper = data(); paper["learning_requirements"] = {"overview_framework": {
            "kind": "interactive_pipeline", "section_id": "method", "block_id": "demo"}}
        aid = paper["overview"]["sections"][0]["blocks"][-1]["aid"]
        aid.update(type="method_pipeline", config=pipeline_config())
        row = entry(decision="interactive", target_block_ids=["demo"])
        self.assertEqual(validate_visual_plan([row], paper, True), [])
        for change in ({"status": "planned"}, {"decision": "static"}, {"layer": "plain"}, {"view": "explanation"},
                       {"section_id": "elsewhere"}, {"target_block_ids": ["fig"]}):
            altered = dict(row, **change)
            with self.subTest(change=change):
                self.assertTrue(any("visual-plan entry" in e for e in validate_visual_plan([altered], paper, True)))
        # A valid graph_walkthrough cannot replace the specifically promised method_pipeline.
        paper["overview"]["sections"][0]["blocks"][-1]["aid"] = component()
        self.assertEqual(validate_visual_plan([row], paper), [])
        self.assertTrue(any("body method_pipeline" in e for e in validate_visual_plan([row], paper, True)))
        paper["learning_requirements"]["overview_framework"]["block_id"] = "fig"
        self.assertTrue(any("body method_pipeline" in e for e in validate_visual_plan([entry(decision="interactive")], paper, True)))

    def test_interaction_review_is_opt_in_for_legacy_plans(self):
        rows = [entry()]
        self.assertEqual(validate_visual_plan(rows, data()), [])
        self.assertEqual(validate_visual_plan(rows, data(), require_interaction_review=False), [])
        errors = validate_visual_plan(rows, data(), require_interaction_review=True)
        self.assertEqual(errors, ["visual-plan[0] (visual-1): interaction_review is required"])

    def test_strict_interaction_review_is_required_for_every_entry(self):
        rows = [entry(interaction_review=interaction_review()),
                entry(id="missing-static"),
                entry(id="missing-text", decision="text", target_block_ids=[])]
        errors = validate_visual_plan(rows, data(), require_interaction_review=True)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("visual-plan[1] (missing-static): interaction_review is required" in e for e in errors))
        self.assertTrue(any("visual-plan[2] (missing-text): interaction_review is required" in e for e in errors))

    def test_observe_allows_static_table_text_and_explained_interaction(self):
        rows = [entry(interaction_review=interaction_review()),
                entry(id="table-plan", decision="table", target_block_ids=["table"], interaction_review=interaction_review()),
                entry(id="text-plan", decision="text", target_block_ids=[], interaction_review=interaction_review()),
                entry(id="interactive", decision="interactive", target_block_ids=["demo"],
                      interaction_review=interaction_review(action="切换阶段", observable_change="节点高亮变化"))]
        self.assertEqual(validate_visual_plan(rows, data(), require_interaction_review=True), [])

    def test_trace_and_change_require_interactive_decision_even_without_strict_flag(self):
        for task in ("trace", "change"):
            for decision, targets in (("static", ["fig"]), ("table", ["table"]), ("text", [])):
                with self.subTest(task=task, decision=decision):
                    row = entry(decision=decision, target_block_ids=targets, interaction_review=interaction_review(task))
                    expected = [f"visual-plan[0] (visual-1).interaction_review: task {task} requires decision interactive"]
                    self.assertEqual(validate_visual_plan([row], data()), expected)
                    self.assertEqual(validate_visual_plan([row], data(), require_interaction_review=True), expected)

    def test_trace_and_change_accept_fulfilled_interactive_targets(self):
        for task in ("trace", "change"):
            with self.subTest(task=task):
                row = entry(decision="interactive", target_block_ids=["demo"], interaction_review=interaction_review(task))
                self.assertEqual(validate_visual_plan([row], data(), require_interaction_review=True), [])

    def test_registered_overview_pipeline_and_observe_plan_pass_both_gates(self):
        paper = data()
        paper["learning_requirements"] = {"overview_framework": {
            "kind": "interactive_pipeline", "section_id": "method", "block_id": "demo"}}
        paper["overview"]["sections"][0]["blocks"][-1]["aid"].update(type="method_pipeline", config=pipeline_config())
        rows = [entry(id="overview-map", decision="interactive", target_block_ids=["demo"],
                      interaction_review=interaction_review("trace")),
                entry(id="observe-detail", interaction_review=interaction_review())]
        self.assertEqual(validate_visual_plan(rows, paper, require_method_pipeline=True, require_interaction_review=True), [])

    def test_malformed_interaction_review_objects_are_rejected_without_crashing(self):
        for invalid in (None, False, 42, "observe", [], [interaction_review()]):
            for strict in (False, True):
                with self.subTest(invalid=invalid, strict=strict):
                    errors = validate_visual_plan([entry(interaction_review=invalid)], data(), require_interaction_review=strict)
                    self.assertEqual(errors, ["visual-plan[0] (visual-1).interaction_review: must be an object"])
        errors = validate_visual_plan([None], data(), require_interaction_review=True)
        self.assertEqual(errors, ["visual-plan[0]: entry must be an object"])

    def test_interaction_review_requires_valid_task_and_nonempty_reason(self):
        for field, invalid_values in (("task", (None, "", "Observe", "skip", [], {}, True, 42)),
                                      ("reason", (None, "", "   ", [], {}, True, 42))):
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=invalid):
                    review = interaction_review(**{field: invalid})
                    errors = validate_visual_plan([entry(interaction_review=review)], data())
                    self.assertTrue(any(".interaction_review." + field in e for e in errors), errors)
            review = interaction_review(); review.pop(field)
            errors = validate_visual_plan([entry(interaction_review=review)], data(), require_interaction_review=True)
            self.assertTrue(any(".interaction_review." + field in e for e in errors), errors)

    def test_interactive_learning_tasks_require_action_and_observable_change(self):
        for task in ("observe", "trace", "change"):
            for field in ("action", "observable_change"):
                for invalid in (None, "", "   ", [], {}, True, 42):
                    with self.subTest(task=task, field=field, invalid=invalid):
                        review = interaction_review(task, action="切换阶段", observable_change="高亮变化")
                        review[field] = invalid
                        row = entry(decision="interactive", target_block_ids=["demo"], interaction_review=review)
                        errors = validate_visual_plan([row], data())
                        self.assertEqual(len(errors), 1, errors)
                        self.assertIn(".interaction_review." + field + " must be non-empty text", errors[0])
                review = interaction_review(task, action="切换阶段", observable_change="高亮变化"); review.pop(field)
                row = entry(decision="interactive", target_block_ids=["demo"], interaction_review=review)
                errors = validate_visual_plan([row], data(), require_interaction_review=True)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(".interaction_review." + field + " must be non-empty text", errors[0])

    def test_interaction_review_does_not_infer_tasks_or_set_interaction_quota(self):
        rows = [entry(id=f"observe-{index}", interaction_review=interaction_review(
                    reason="正文已有公式和表格时不重复制作交互，观察此图即可")) for index in range(3)]
        self.assertEqual(validate_visual_plan(rows, data(), require_interaction_review=True), [])

    def test_type_downgrades_are_rejected(self):
        self.assert_bad([entry(target_block_ids=["table"])], contains="figure or diagram")
        self.assert_bad([entry(decision="table")], contains="table block")

    def test_empty_visual_placeholders_cannot_claim_done(self):
        paper = data(); paper["overview"]["sections"][0]["blocks"][2].pop("src")
        self.assert_bad([entry()], paper, "empty placeholder")
        paper = data(); paper["overview"]["sections"][0]["blocks"][3].pop("rows")
        self.assert_bad([entry(decision="table", target_block_ids=["table"])], paper, "empty placeholder")
        paper = data(); figure = paper["overview"]["sections"][0]["blocks"][2]
        figure["asset"] = figure.pop("src")
        self.assertEqual(validate_visual_plan([entry()], paper), [])

    def test_incomplete_statuses(self):
        for status in ("planned", "blocked", "", None, "invalid"):
            self.assert_bad([entry(status=status)], contains="status must be done")

    def test_position_must_not_cross_heading_but_may_include_formulas(self):
        self.assertEqual(validate_visual_plan([entry()], data()), [])
        paper = data(); body = paper["overview"]["sections"][0]["blocks"]
        body.insert(2, {"id": "next", "type": "heading"})
        self.assert_bad([entry()], paper, "crosses a heading")
        # Also catch a preceding target, rather than checking only forward order.
        paper = data(); body = paper["overview"]["sections"][0]["blocks"]
        body.insert(0, body.pop(2)); body.insert(1, {"id": "next", "type": "heading"})
        self.assert_bad([entry()], paper, "crosses a heading")

    def test_duplicate_and_illegal_entries(self):
        self.assert_bad([entry(), entry()], contains="duplicate id")
        for changes in ({"id": "bad id"}, {"reason": " "}, {"view": "translation"},
                        {"layer": "hidden"}, {"decision": "skip"}, {"section_id": "missing"},
                        {"anchor_block_id": "missing"}, {"target_block_ids": ["fig", "fig"]},
                        {"target_block_ids": [1]}, {"target_block_ids": None}):
            with self.subTest(changes=changes):
                self.assert_bad([entry(**changes)])
        for plan in ([], {}, [None]):
            self.assert_bad(plan)

    def test_text_requires_legal_anchor_and_reason(self):
        self.assert_bad([entry(decision="text", target_block_ids=[], reason="")], contains="reason")
        self.assert_bad([entry(decision="text", target_block_ids=[], anchor_block_id="plain-fig")], contains="anchor")

    def test_cli_plan_fence_and_missing_file(self):
        # Workspace-local temp, isolated from paper assets and cleaned automatically.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder); plan = root / "reading-plan.md"; paper = root / "paper-data.js"
            valid = "# Plan\n```visual-plan\n" + json.dumps([entry()]) + "\n```\n"
            plan.write_text(valid, encoding="utf-8")
            paper.write_text("window.PAPER_DATA = " + json.dumps(data()) + ";", encoding="utf-8")
            self.assertEqual(read_plan(plan), [entry()]); self.assertEqual(read_data(paper), data())
            for content in ("# No plan", valid + valid, "```visual-plan\n{}\n```", "```visual-plan\n[]\n```", "```visual-plan\nwrong\n```"):
                plan.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_plan(plan)
            plan.write_text(valid, encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--plan", str(plan), "--data", str(paper)]), 0)
                self.assertEqual(main(["--plan", str(plan), "--data", str(paper), "--require-method-pipeline"]), 1)
                self.assertEqual(main(["--plan", str(root / "missing.md"), "--data", str(paper)]), 1)
            self.assertIn("PASS", output.getvalue()); self.assertIn("FAIL", output.getvalue())
            plan.write_text(valid.replace('"done"', '"blocked"'), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--plan", str(plan), "--data", str(paper)]), 1)
            self.assertIn("commitment remains blocked", output.getvalue())
            paper.write_text(json.dumps(data()), encoding="utf-8")
            self.assertEqual(read_data(paper), data())

    def test_cli_require_interaction_review_and_scope_explanation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder); plan = root / "reading-plan.md"; paper = root / "paper-data.js"
            paper.write_text(json.dumps(data()), encoding="utf-8")
            args = ["--plan", str(plan), "--data", str(paper)]
            plan.write_text("```visual-plan\n" + json.dumps([entry()]) + "\n```\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args + ["--require-interaction-review"]), 1)
            self.assertIn("interaction_review is required", output.getvalue())
            row = entry(interaction_review=interaction_review())
            plan.write_text("```visual-plan\n" + json.dumps([row]) + "\n```\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(args + ["--require-interaction-review"]), 0)
            self.assertIn("supplied learning-task/media consistency", output.getvalue())
            self.assertIn("does not prove task classification or complete task coverage", output.getvalue())
        with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exit_info:
            main(["--help"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("--require-interaction-review", output.getvalue())
        self.assertIn("complete task coverage", output.getvalue())


if __name__ == "__main__":
    unittest.main()
