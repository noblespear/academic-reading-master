"""Focused 3.9 browser evidence: graph geometry, body-first and preview isolation."""
import copy
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

from playwright.sync_api import sync_playwright
from test_method_pipeline import example, pipeline
from test_prepare_method_pipeline import skeleton
from test_retired_preloaded_frontend import fixture
from prepare_method_pipeline import prepare_method_pipeline

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT/"assets"
OUTPUT = ROOT.parent/"validation/v3.9.5/pipeline"


def main():
    current = {"data": None, "preview": False}
    errors, requests, checks = [], [], []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split("?")[0].lstrip("/") or "reader-v3.html"
            if name == "paper-data.js":
                content = ("window.PAPER_DATA="+json.dumps(current["data"], ensure_ascii=False)+";").encode("utf-8")
                media = "application/javascript"
            elif name == "preview-config.js":
                content = b"window.READER_PREVIEW=true;"; media = "application/javascript"
            else:
                path = (ASSETS/name).resolve()
                if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
                    self.send_error(404); return
                content = path.read_bytes(); media = mimetypes.guess_type(name)[0] or "application/octet-stream"
                if name == "reader-v3.html" and current["preview"]:
                    content = content.replace(b'<script src="paper-data.js">', b'<script src="preview-config.js"></script><script src="paper-data.js">')
            self.send_response(200); self.send_header("Content-Type", media+"; charset=utf-8")
            self.end_headers(); self.wfile.write(content)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        print("Launching isolated Chromium fixture", flush=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for contract in ("3.1", None, "3.2", "preview"):
                data, annotations, _ = fixture()
                data["id"] = "pipeline-layout-"+str(contract)
                block = copy.deepcopy(example()["overview"]["sections"][0]["blocks"][1])
                data["overview"]["sections"][0]["blocks"].insert(1, block)
                if contract in ("3.2", "preview"):
                    data["content_contract_version"] = "3.2"
                    for section in data["overview"]["sections"]:
                        for item in section["blocks"]:
                            item.pop("learning", None)
                    data["overview"]["sections"][0]["blocks"].append({"id":"local-detail", "type":"details",
                        "title":"试算这个例子", "blocks":[{"id":"local-detail-body", "type":"paragraph", "text_zh":"局部展开保留，按需查看试算。"}]})
                    data["overview"]["sections"][0]["blocks"].append({"id":"local-reference","type":"paragraph","text_zh":"查看这段局部推导。","source_refs":[{"content_id":"local-detail-body","label":"局部正文"}]})
                    block["aid"] = prepare_method_pipeline(skeleton())
                    # Real length variability must grow the explanation area without changing the map.
                    block["aid"]["config"]["nodes"][2]["summary"] += "这份自拟教学说明需要逐步展开。"*100
                    data["sections"]=[{"id":"bilingual-fixture","title_cn":"双语结构化内容","blocks":[
                        {"id":"bilingual-heading","type":"heading","text_en":"Original heading","text_zh":"中文标题"},
                        {"id":"bilingual-table","type":"table","headers_en":["Method","Value"],"headers_zh":["方法","数值"],
                         "rows_en":[["Original",1]],"rows_zh":[["译文",1]],"caption_en":"Original comparison","caption_zh":"中文对照"}]}]
                elif contract is None:
                    del data["content_contract_version"]
                current.update(data=data, preview=contract == "preview")
                context = browser.new_context(viewport={"width":1360,"height":1000}, has_touch=True)
                context.add_init_script("window.EventSource=undefined;window.storageTouches=[];const oldGet=Storage.prototype.getItem,oldSet=Storage.prototype.setItem;Storage.prototype.getItem=function(...a){storageTouches.push('get');return oldGet.apply(this,a)};Storage.prototype.setItem=function(...a){storageTouches.push('set');return oldSet.apply(this,a)};")
                page = context.new_page(); page.on("pageerror", lambda e: errors.append(str(e)))
                api_calls = []
                def api(route):
                    api_calls.append({"method":route.request.method,"url":route.request.url})
                    if "/api/session?" in route.request.url:
                        response = {"paper_id":data["id"],"token":"fixture"}
                    elif "/api/status?" in route.request.url:
                        response = {"bridge_available":False}
                    else:
                        response = {"annotations":annotations}
                    route.fulfill(status=200, json=response)
                page.route("**/api/**", api)
                page.route("https://**", lambda route: route.abort())
                page.goto(f"http://127.0.0.1:{server.server_port}/reader-v3.html")
                page.wait_for_function("window.ReaderV3")
                if contract != "preview":
                    page.wait_for_function("ReaderV3.getState().online")
                component = page.locator('.method-pipeline')
                assert component.is_visible() and component.locator('.method-pipeline-node').count() == 6
                assert component.locator('.content-warning').count() == 0
                assert component.locator('.method-pipeline-edge-label').count() == 0
                for width in (1360,760,320):
                    page.set_viewport_size({"width":width,"height":1000})
                    page.wait_for_function("document.querySelector('.method-pipeline-drawing').scrollWidth <= document.querySelector('.method-pipeline-drawing').clientWidth + 1")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert component.get_attribute('data-scrollable') == 'false'
                    before = component.evaluate("n=>({figure:n.querySelector('.method-pipeline-figure').getBoundingClientRect().height,canvas:n.querySelector('.method-pipeline-canvas').getBoundingClientRect().height,paths:[...n.querySelectorAll('.method-pipeline-edge')].map(p=>p.getAttribute('d'))})")
                    component.get_by_role("button", name="提取轮廓", exact=True).click()
                    assert component.get_attribute("data-selected-node-id") == "contour"
                    assert "概要" in component.locator('.method-pipeline-description').inner_text()
                    component.locator('.method-pipeline-relations summary').click()
                    component.get_by_role("button",name="关系：检查质量 → 校正亮度",exact=True).click()
                    assert component.get_attribute("data-selected-edge-id") == "check-prepare"
                    assert "关系为何成立" in component.locator('.method-pipeline-description').inner_text()
                    component.locator('.method-pipeline-relations summary').click()
                    after = component.evaluate("n=>{const d=n.querySelector('.method-pipeline-description');return {figure:n.querySelector('.method-pipeline-figure').getBoundingClientRect().height,canvas:n.querySelector('.method-pipeline-canvas').getBoundingClientRect().height,paths:[...n.querySelectorAll('.method-pipeline-edge')].map(p=>p.getAttribute('d')),description:{scrollHeight:d.scrollHeight,clientHeight:d.clientHeight}}}")
                    assert before['figure'] == after['figure'] and before['canvas'] == after['canvas'] and before['paths'] == after['paths'], (contract,width,before,after)
                    assert after['description']['scrollHeight'] == after['description']['clientHeight'], (contract,width,after['description'])
                    geometry = component.evaluate('''root=>{
                        const boxes=[...root.querySelectorAll('.method-pipeline-node')].map(n=>({id:n.dataset.nodeId,b:n.getBoundingClientRect()}));let collisions=[],diagonals=[];
                        for(const path of root.querySelectorAll('.method-pipeline-edge')){
                            const coordinates=path.getAttribute('d').match(/[ML] [0-9.]+ [0-9.]+/g).map(part=>part.slice(2).split(' ').map(Number));
                            for(let i=1;i<coordinates.length;i++)if(coordinates[i][0]!==coordinates[i-1][0]&&coordinates[i][1]!==coordinates[i-1][1])diagonals.push(path.dataset.edgeId);
                            const matrix=path.getScreenCTM(),length=path.getTotalLength();
                            for(let d=0;d<=length;d+=2){const raw=path.getPointAtLength(d),q=new DOMPoint(raw.x,raw.y).matrixTransform(matrix);
                                for(const {id,b} of boxes)if(id!==path.dataset.from&&id!==path.dataset.to&&q.x>b.left+1&&q.x<b.right-1&&q.y>b.top+1&&q.y<b.bottom-1)collisions.push([path.dataset.edgeId,id]);}
                        }
                        const svg=root.querySelector('svg'),w=svg.viewBox.baseVal.width,h=svg.viewBox.baseVal.height;
                        const clipped=[...root.querySelectorAll('.method-pipeline-edge-label')].filter(n=>{const b=n.getBBox();return b.x<0||b.y<0||b.x+b.width>w||b.y+b.height>h}).map(n=>n.dataset.edgeId);
                        const labels=[...root.querySelectorAll('.method-pipeline-edge-label')].map(n=>({id:n.dataset.edgeId,b:n.getBoundingClientRect()})),labelCollisions=[];
                        const overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
                        labels.forEach((label,i)=>{for(const other of labels.slice(i+1))if(overlaps(label.b,other.b))labelCollisions.push([label.id,other.id]);for(const node of boxes)if(overlaps(label.b,node.b))labelCollisions.push([label.id,node.id]);});
                        return {collisions,clipped,diagonals,labelCollisions};
                    }''')
                    assert geometry == {"collisions":[],"clipped":[],"diagonals":[],"labelCollisions":[]}, (contract,width,geometry)
                    component.screenshot(path=str(OUTPUT/(str(contract)+"-"+str(width)+".png")))
                if contract in ("3.2", "preview"):
                    assert page.locator('#plain-language-toggle').is_hidden()
                    assert page.locator('.learning-unavailable').count() == 0
                    assert page.locator('.ai-supplement').count() == 0
                    detail = page.locator('[data-content-id="local-detail"] details')
                    detail.locator('summary').click()
                    assert detail.get_attribute('open') is not None and "局部展开保留" in detail.inner_text()
                    detail.locator('summary').click()
                    page.locator('[data-block-id="local-reference"] .source-details > summary').click()
                    page.get_by_role("button",name="局部正文",exact=True).click()
                    page.wait_for_function("document.querySelector('[data-content-id=\"local-detail\"] details').open")
                    assert page.locator('.jump-flash').count()==1
                    assert component.get_attribute('data-layout-columns') == '5'
                    assert component.get_attribute('data-scrollable') == 'false'
                    checks.append(str(contract)+": method graph visible, global switch/prompt absent, local details preserved, auto layers retain parallel join and feedback without inner horizontal scrolling")
                else:
                    assert page.locator('#plain-language-toggle').is_visible()
                    assert page.locator('.ai-supplement').is_visible()
                    page.locator('#plain-language-toggle').click()
                    assert page.locator('.ai-supplement').is_hidden() and component.is_visible()
                    page.evaluate("ReaderV3.switchView('explanation')")
                    assert page.locator('.learning-unavailable').count() == 1
                    checks.append(str(contract)+": old supplement, toggle and unavailable prompt preserved; explicit grid retained")
                if contract == "preview":
                    page.evaluate("ReaderV3.poll();ReaderV3.syncManagedDrafts();ReaderV3.setPlainLanguage(true)")
                    page.wait_for_timeout(700)
                    assert page.evaluate("storageTouches") == []
                    assert api_calls == [], api_calls
                    assert page.locator('#annotations-toggle').is_hidden() and page.locator('#stop-service').is_hidden()
                    assert page.locator('.block-question:visible,.selection-action:visible').count() == 0
                    checks.append("preview: no Storage access, session/poll/API/bridge requests or writer controls; graph interactions stay in memory")
                if contract in ("3.2","preview"):
                    page.evaluate("ReaderV3.switchView('translation')")
                    for language,header,row,caption,heading in (("en","Method","Original","Original comparison","Original heading"),("zh","方法","译文","中文对照","中文标题")):
                        table=page.locator('[data-content-id="bilingual-table"].language-'+language)
                        assert table.locator("th").first.inner_text()==header
                        assert table.locator("td").first.inner_text()==row
                        assert table.locator(".block-caption").inner_text()==caption
                        assert page.locator('[data-content-id="bilingual-heading"].language-'+language).inner_text().endswith(heading)
                    checks.append(str(contract)+": modern bilingual table headers, rows, captions and headings preserve each language")
                requests.extend(api_calls)
                context.close()
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
    assert errors == [] and all(r["method"] == "GET" for r in requests), (errors,requests)
    report = {"ok":True,"checks":checks+["1360/760/320px: all paths avoid unrelated modules; edge labels stay in the bottom panel/tooltip/list; node/edge selection does not change figure/canvas geometry and the explanation grows without an inner scrollbar"], "page_errors":errors, "non_get_requests":[]}
    (OUTPUT/"layout-qa.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
