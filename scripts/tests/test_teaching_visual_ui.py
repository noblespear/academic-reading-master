"""Real-browser visual regression using synthetic content and isolated APIs."""
import copy
import json
import mimetypes
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from test_body_learning_aids_frontend import fixture

SKILL = Path(__file__).resolve().parents[2]
ASSETS = SKILL / 'assets'
OUTPUT = SKILL.parent / 'validation' / 'continuous-teaching-3.6'


def main():
    data, annotations, _ = fixture()
    data['id'] = 'teaching-visual-fixture'
    section = data['overview']['sections'][0]
    section['title_cn'] = '方法框架'
    section['blocks'][0]['text_zh'] = ('协作系统需要决定哪些回答值得交给后续参与者。'
        '如果一个参与者没有贡献新的判断，却反复调用模型并传递已有内容，那么额外的计算未必改善结果。'
        '因此，方法先考察参与者之间的依赖，再决定保留哪些参与者以及哪些连接。')
    section['blocks'].insert(1, {'id':'wide-mechanism','type':'figure',
        'src':'fixture-mechanism.svg','alt':'协作关系示意',
        'caption_zh':'机制示意：箭头表示前面的回答交给后面的参与者。'})
    graph = copy.deepcopy(section['blocks'][0]['learning']['blocks'][-1]['aid'])
    graph['id'] = 'qa-graph'
    annotations[0]['history'][0]['answer_content'] = {'version':1,'blocks':[
        {'id':'explain','type':'paragraph','text_zh':'连接规定一次回答会传给谁。保留参与者以后，仍须检查这些传递是否带来新的信息；因此，参与者筛选与连接调整解决不同的问题。'},
        {'id':'math','type':'formula','latex':r'G=(V,E)'},
        {'id':'diagram','type':'learning_aid','aid':graph}]}
    errors, posts, checks = [], [], []
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="550" viewBox="0 0 1200 330">'
        '<rect width="1200" height="330" fill="#f5f8fc"/>'
        '<text x="60" y="70" font-size="26" fill="#315f97">Collaboration mechanism</text>'
        '<path d="M210 180H545 M655 180H990" stroke="#647b93" stroke-width="3"/>'
        '<rect x="70" y="125" width="220" height="100" rx="5" fill="#eaf2fb"/>'
        '<rect x="490" y="125" width="220" height="100" rx="5" fill="#eaf2fb"/>'
        '<rect x="910" y="125" width="220" height="100" rx="5" fill="#eaf2fb"/>'
        '<g fill="#25364c" font-size="23" text-anchor="middle">'
        '<text x="180" y="185">Candidate answers</text><text x="600" y="185">Participant selection</text>'
        '<text x="1020" y="185">Connection adjustment</text></g></svg>').encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            if name == 'paper-data.js':
                source = ('window.PAPER_DATA=' + json.dumps(data, ensure_ascii=False) + ';').encode()
                media = 'application/javascript'
            elif name == 'fixture-mechanism.svg':
                source, media = svg, 'image/svg+xml'
            elif name == 'assets/legacy-context.svg':
                source, media = svg, 'image/svg+xml'
            else:
                path = (ASSETS / name).resolve()
                if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
                    self.send_error(404)
                    return
                source = path.read_bytes()
                media = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', media + '; charset=utf-8')
            self.end_headers()
            self.wfile.write(source)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width':1440,'height':1000},color_scheme='dark')
            context.add_init_script('window.EventSource=undefined;')
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))

            def api(route):
                req = route.request
                if req.method != 'GET':
                    posts.append({'method':req.method,'url':req.url})
                    result = {'ok':True}
                elif '/api/session?' in req.url:
                    result = {'paper_id':data['id'],'token':'fixture'}
                elif '/api/status?' in req.url:
                    result = {'bridge_available':False}
                else:
                    result = {'annotations':annotations}
                route.fulfill(status=200,json=result)

            page.route('**/api/**',api)
            page.route('https://**',lambda route:route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            page.evaluate('document.fonts.ready')
            prose = page.locator('.block-frame[data-content-id="plain-parent"]')
            if prose.count() == 0:
                prose = page.locator('#paper-content .block-frame.content-prose').first
            wide = page.locator('.block-frame[data-content-id="wide-mechanism"]')
            assert wide.bounding_box()['width'] > prose.bounding_box()['width'] + 100
            assert prose.bounding_box()['width'] <= 761
            assert page.evaluate('getComputedStyle(document.body).backgroundColor') == 'rgb(255, 255, 255)'
            graph = page.locator('#paper-content [data-aid-id="nested-graph"]')
            graph.get_by_role('button',name='只看A',exact=True).click()
            assert graph.locator('.graph-node.is-inactive').count() == 1
            assert graph.locator('.graph-node[data-node-state="active"]').count() == 1
            assert graph.locator('.graph-edge').count() == 0
            page.evaluate('ReaderV3.setPlainLanguage(false)')
            assert wide.is_visible() and not graph.is_visible()
            chart = page.locator('#paper-content [data-aid-id="body-chart"]')
            assert chart.is_visible()
            assert chart.get_by_role('combobox').count() == 1
            page.locator('#reading-scroll').evaluate('(n)=>n.scrollTop=0')
            page.screenshot(path=str(OUTPUT/'teaching-ui-body-desktop.png'))
            checks.append('wide technical figure exceeds continuous prose width; dark host retains light page; graph state changes meaningful nodes/edges; single-option chart picker omitted')

            baseline = page.locator('#reading-scroll').bounding_box()
            page.evaluate('ReaderV3.openAnnotation("user-1")')
            panel = page.locator('#annotation-panel')
            assert panel.locator('.answer-title').all_text_contents() == ['答疑','答疑']
            assert panel.locator('.ai-supplement-title').count() == 0
            assert panel.locator('.math-atom[data-math-state="rendered"]').count() >= 1
            assert panel.locator('[data-aid-id="qa-graph"]').is_visible()
            panel_width = panel.bounding_box()['width']
            page.locator('#panel-expand').click()
            assert panel.bounding_box()['width'] > panel_width + 300
            assert page.locator('#reading-scroll').bounding_box() == baseline
            assert page.locator('#panel-expand').get_attribute('aria-pressed') == 'true'
            page.locator('#panel-body').evaluate('(n)=>n.scrollTop=n.querySelector(".answer-content").offsetTop-20')
            page.screenshot(path=str(OUTPUT/'qa-expanded-desktop.png'))
            page.locator('#panel-expand').click()
            page.locator('#panel-close').click()
            checks.append('independent QA stays visible with AI switch off and renders formula/graph; expanding answers does not squeeze article geometry')

            wide.locator('.image-zoom').click()
            dialog = page.get_by_role('dialog',name='放大图像')
            dialog.get_by_role('button',name='实际尺寸',exact=True).click()
            assert dialog.locator('.image-dialog-canvas').evaluate('(n)=>n.scrollWidth>n.clientWidth')
            dialog.get_by_role('button',name='适应窗口',exact=True).click()
            page.keyboard.press('Escape')
            dialog.wait_for(state='detached')
            page.evaluate('ReaderV3.setPlainLanguage(true)')
            page.locator('#reading-scroll').evaluate('(n)=>n.scrollTop=0')
            page.screenshot(path=str(OUTPUT/'teaching-ui-desktop.png'))
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert graph.get_by_role('button',name='只看A',exact=True).get_attribute('aria-pressed') == 'true'
            checks.append('wide image supports fit/actual size and Escape; meaningful graph stage survives refresh')

            page.set_viewport_size({'width':320,'height':800})
            graph.scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#reading-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth')
            assert graph.locator('.learning-graph-svg text').evaluate_all('(ns)=>ns.every(n=>parseFloat(getComputedStyle(n).fontSize)>=12)')
            page.screenshot(path=str(OUTPUT/'teaching-ui-mobile320.png'))
            page.evaluate('ReaderV3.openAnnotation("user-1")')
            page.locator('#panel-expand').click()
            assert panel.bounding_box()['width'] <= 302
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#panel-body').evaluate('(n)=>n.scrollWidth<=n.clientWidth')
            page.locator('#panel-body').evaluate('(n)=>n.scrollTop=n.querySelector(".answer-content").offsetTop-12')
            page.screenshot(path=str(OUTPUT/'qa-mobile320.png'))
            assert not errors, errors
            assert not posts, posts
            checks.append('320px article, active graph and expanded QA have no page/panel horizontal overflow; graph labels stay readable; no user database or model submissions')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    report = {'ok':True,'checks':checks,'page_errors':errors,'mutating_requests':posts,
        'visual_guidance':'references/visual_authoring_guide.md','design_reference':'reader-design.png',
        'evidence_scope':'Synthetic content with real Chromium; live paper content requires main-agent review.'}
    (OUTPUT/'teaching-visual-ui-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
