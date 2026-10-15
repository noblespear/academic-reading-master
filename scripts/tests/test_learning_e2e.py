"""Real browser + real HTTP/SQLite. The answer is synthetic; no model is called."""
import json
from pathlib import Path
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from deploy_reader import deploy_templates
from serve_reader_v3 import ReaderServer


def main():
    artifacts = Path(__file__).parent / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="learning-e2e-") as temporary:
        vault = Path(temporary)
        data = json.loads((ROOT / "assets/content-templates/complete-cache-example.json").read_text(encoding="utf-8"))
        paper = vault / "papers" / data["id"]
        paper.mkdir(parents=True)
        (paper / "paper-data.js").write_text("window.PAPER_DATA = " + json.dumps(data, ensure_ascii=False) + ";", encoding="utf-8")
        deploy_templates(paper, ROOT, data)
        server = ReaderServer(vault, data["id"], bridge="none")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        errors = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(server.base_url + "/papers/" + data["id"] + "/reader.html")
                page.wait_for_function("ReaderV3.getState().online && ReaderV3.getState().assistant")
                page.evaluate("ReaderV3.setPlainLanguage(true)")
                page.screenshot(path=str(artifacts / "learning-e2e-overview.png"))
                page.locator('[data-view="explanation"]').click()
                page.locator('[data-content-id="ex-ratio"]').first.scroll_into_view_if_needed()
                assert page.locator(".katex-error,.math-error").count() == 0
                page.screenshot(path=str(artifacts / "learning-e2e-formula.png"))
                page.evaluate("""() => ReaderV3.newDraft({kind:'block',view:'explanation',language:'zh',
                    content_id:'ex-ratio',field:'block',content_version:PAPER_DATA.content_version},PAPER_DATA.explanation.sections[1].blocks[0].latex)""")
                page.locator("#annotation-note").fill("请用公式和表格解释。\n\n$$h=\\frac{2}{7}$$\n\n| 量 | 次数 |\n| --- | --- |\n| 命中 | 2 |\n| 总数 | 7 |\n\n---\n\n> 请保留这个引用。")
                page.get_by_label("引用学习组件", exact=True).select_option("aid-ratio")
                page.locator(".primary-button").first.click()
                page.wait_for_function("ReaderV3.getState().drafts.length === 0 && ReaderV3.getState().annotations.length === 1")
                annotation = server.store.list_annotations(data["id"])["annotations"][0]
                kinds = {block["type"] for block in annotation["note_content"]["blocks"]}
                assert {"formula", "table", "learning_aid", "quote", "separator"} <= kinds, kinds
                server.store.register_bridge("synthetic-e2e", "mock")
                task = server.store.claim("synthetic-e2e")["task"]
                server.store.acknowledge(task["id"], "synthetic-e2e", task["claim_token"], "synthetic-thread", "synthetic-turn")
                reply = {"version": 1, "blocks": [
                    {"id": "reply-note", "type": "paragraph", "text_zh": "自动化测试合成回答：用两次除以七次。", "evidence_kind": "teaching_example"},
                    {"id": "reply-formula", "type": "formula", "latex": r"h=\frac{2}{7}\approx0.286"},
                    {"id": "reply-table", "type": "table", "headers": ["量", "次数"], "rows": [["命中", "2"], ["总数", "7"]]},
                    {"id": "reply-aid", "type": "learning_aid", "aid_id": "aid-ratio"}
                ]}
                result = server.store.result(task["id"], "synthetic-e2e", task["claim_token"],
                    {"answer": "合成回答", "answer_content": reply, "sources": [{"content_id": "src-hit-ratio"}]})
                assert result["applied"]
                page.evaluate("ReaderV3.poll()")
                page.wait_for_function("ReaderV3.getState().annotations[0].status === 'answered'")
                page.evaluate("(id) => ReaderV3.openAnnotation(id)", annotation["id"])
                page.wait_for_selector("#annotation-followup")
                assert page.locator("#panel-body .katex").count() >= 2
                assert page.locator("#panel-body .data-table th").all_text_contents().count("次数") >= 1
                page.screenshot(path=str(artifacts / "learning-e2e-rich-answer.png"))
                page.reload()
                page.wait_for_function("ReaderV3.getState().online && ReaderV3.getState().annotations.length === 1")
                restored = page.evaluate("ReaderV3.getState().annotations[0]")
                assert restored["note_content"] == annotation["note_content"]
                assert restored["answer_content"] == reply
                assert restored["history"][-1]["answer_content"] == reply
                assert not errors, errors
                browser.close()
            print("PASS: real HTTP rich submission, validated formulas/tables/quote/separator/component, synthetic result, history and reload.")
        finally:
            server.shutdown()
            server.cleanup()


if __name__ == "__main__":
    main()
