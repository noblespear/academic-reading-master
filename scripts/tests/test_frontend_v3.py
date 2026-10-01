"""Real Chromium regressions for v3; network/API isolated by Playwright routing.
Run: python scripts/tests/test_frontend_v3.py
Screenshots are written to scripts/tests/artifacts/.
"""
import json
import threading
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'assets'
FIXTURE = Path(__file__).parent / 'frontend-fixture'
ARTIFACTS = Path(__file__).parent / 'artifacts'


class StaticHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
        path = (FIXTURE if name in ('paper-data.js', 'fixture-figure.svg') else ASSETS) / name
        if not path.is_file() or not any(path.resolve().is_relative_to(root.resolve()) for root in (ASSETS, FIXTURE)):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css', '.svg': 'image/svg+xml'}.get(path.suffix, mimetypes.guess_type(str(path))[0] or 'application/octet-stream'))
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def log_message(self, *args):
        pass


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    anns, requests, errors = [], [], []
    def api_route(route):
        req = route.request
        path = req.url.split('/api/', 1)[1].split('?')[0]
        payload = json.loads(req.post_data or '{}')
        requests.append((req.method, path, payload))
        status, data = 200, {}
        if path == 'session':
            assert 'paper_id=frontend_v3_fixture' in req.url, 'Service reuse must request this paper, not the first paper'
            data = {'paper_id': 'frontend_v3_fixture', 'token': 'fixture-token', 'api_version': 3}
        elif path == 'status':
            data = {'bridge_available': True, 'bridges': [], 'counts': {}}
        elif path == 'events':
            route.fulfill(status=200, content_type='text/event-stream', body=': fixture\n\n')
            return
        elif path == 'stop':
            data = {'ok': True, 'stopping': True}
        else:
            assert req.headers.get('x-reader-token') == 'fixture-token', req.headers
            tail = path.split('annotations', 1)[-1].strip('/')
            if not tail and req.method == 'GET':
                data = {'paper_id': 'frontend_v3_fixture', 'annotations': anns, 'revision': len(requests)}
            elif not tail and req.method == 'POST':
                ann = dict(payload, paper_id='frontend_v3_fixture', revision=1, status='running', unread=False, history=[])
                anns.append(ann)
                data = {'annotation': ann, 'task': {}}
            else:
                aid, _, action = tail.partition('/')
                ann = next(a for a in anns if a['id'] == aid)
                if req.method == 'DELETE':
                    anns.remove(ann)
                    data = {'ok': True}
                elif req.method == 'PATCH':
                    if payload['revision'] != ann['revision']:
                        status, data = 409, {'current': ann, 'error': 'conflict'}
                    else:
                        ann.update(user_note=payload['user_note'], revision=ann['revision']+1, status='running' if payload.get('submit') else ann['status'])
                        data = {'annotation': ann}
                elif action == 'read':
                    ann.update(unread=False, revision=ann['revision']+1)
                    data = {'annotation': ann}
                elif action == 'followups':
                    ann['history'].append({'user_note': payload['user_note'], 'status': 'queued', 'answer': ''})
                    ann.update(status='queued', revision=ann['revision']+1)
                    data = {'annotation': ann}
                elif action == 'retry':
                    ann.update(status='queued', revision=ann['revision']+1)
                    data = {'annotation': ann}
        route.fulfill(status=status, content_type='application/json', body=json.dumps(data))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', api_route)
        page.route('https://**', lambda route: route.abort())
        page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
        page.wait_for_function('window.ReaderV3 && window.ReaderV3.getState().online')
        page.screenshot(path=str(ARTIFACTS / 'reader-v3-overview.png'), full_page=True)
        assert page.locator('.view-tabs button').all_text_contents() == ['概览', '详解', '翻译']
        assert page.locator('#source-pdf').get_attribute('href') == 'source.pdf'
        assert page.evaluate('window.__injected') is None
        # Canonical indexing crosses bold text nodes without counting markup.
        selected = page.evaluate('''() => {
          const f=document.querySelector('[data-content-id="ov-summary"].anchor-field');
          const ix=ReaderV3.canonicalIndex(f), start=ix.text.indexOf('稳定锚点');
          const r=ReaderV3.rangeFor(f,start-3,start+8);getSelection().removeAllRanges();getSelection().addRange(r);
          return {anchor:ReaderV3.captureSelection(),expected:ix.text.slice(start-3,start+8)};
        }''')
        assert selected['anchor']['exact'] == selected['expected'], selected
        page.mouse.up()
        page.evaluate('ReaderV3.newDraft(ReaderV3.captureSelection(), getSelection().toString())')
        page.locator('#annotation-note').fill('这个稳定锚点怎样跨越加粗标记？')
        assert page.evaluate('ReaderV3.allAnnotations()[0].user_note') == '这个稳定锚点怎样跨越加粗标记？'
        before = page.locator('#reading-scroll').bounding_box()
        page.locator('.primary-button').click()
        page.wait_for_function('ReaderV3.getState().drafts.length === 0 && ReaderV3.getState().annotations.length === 1')
        assert page.locator('#reading-scroll').bounding_box() == before, 'Floating panel squeezed reading area'
        assert page.locator('.annotation-marker').count() == 1
        # Same-version exact offsets recover; changed-version repeated quotes refuse to guess.
        duplicate = page.evaluate('''() => ReaderV3.locateFragment({view:'overview',language:'zh',content_id:'ov-repeat',field:'text_zh',content_version:0,exact:'重复引文',prefix:'',suffix:''}).status''')
        assert duplicate == 'unlocated'
        assert page.evaluate('''ReaderV3.locateFragment({view:'overview',language:'zh',content_id:'ov-repeat',field:'text_zh',content_version:'fixture-1',start:5,end:9,exact:'重复引文',prefix:'',suffix:''}).status''') == 'located'
        page.locator('#panel-close').click()
        page.locator('[data-view="explanation"]').click()
        assert page.locator('.formula-display .katex').count() == 1
        assert page.locator('.math-atom[data-math-state="rendered"]').count() == 4
        assert page.locator('merror,.katex-error,.math-error').count() == 0
        assert '$HOME/foo$' in page.locator('[data-content-id="ex-cross"].anchor-field').inner_text()
        assert page.locator('[data-content-id="legacy-block-1"].anchor-field').get_attribute('data-field') == 'text_zh_latex'
        assert 'x⃗' not in page.locator('[data-content-id="legacy-block-1"].anchor-field').inner_text()
        assert page.evaluate('''ReaderV3.locateAnnotation({id:'legacy',block_id:'legacy-block-1',selected_text:'内容块、语言、字段和版本'}).status''') == 'located'
        assert page.evaluate('''ReaderV3.locateAnnotation({id:'legacy-math',block_id:'legacy-block-1',selected_text:'旧式向量 x⃗'}).status''') == 'unlocated'
        # Cross-paragraph selection preserves both fragments and independent fields.
        cross = page.evaluate('''() => {
          const fs=[...document.querySelectorAll('.anchor-field')].filter(f=>['legacy-block-1','ex-cross'].includes(f.dataset.contentId));
          const r=document.createRange(),a=ReaderV3.rangeFor(fs[0],12,30),b=ReaderV3.rangeFor(fs[1],0,16);
          r.setStart(a.startContainer,a.startOffset);r.setEnd(b.endContainer,b.endOffset);getSelection().removeAllRanges();getSelection().addRange(r);
          return ReaderV3.captureSelection();
        }''')
        assert len(cross['fragments']) == 2 and cross['fragments'][0]['content_id'] == 'legacy-block-1'
        # Math hidden DOM is ignored even if renderer inserts repeated accessibility source.
        math_index = page.evaluate('''() => {
          const f=document.querySelector('.formula-display'),a=f.querySelector('.math-atom');
          const before=ReaderV3.canonicalIndex(f).text;
          const hidden=document.createElement('span');hidden.className='MJX_Assistive_MathML';hidden.textContent='E = mc^2 E = mc^2';a.append(hidden);
          return [before,ReaderV3.canonicalIndex(f).text];
        }''')
        assert math_index == ['E = mc^2', 'E = mc^2']
        page.locator('[data-view="translation"]').click()
        row_check = page.evaluate('''() => [...document.querySelectorAll('.translation-row')].map(r=>{const cells=r.querySelectorAll('.block-frame');return {a:cells[0].getBoundingClientRect().top,b:cells[1].getBoundingClientRect().top,ah:cells[0].getBoundingClientRect().height,bh:cells[1].getBoundingClientRect().height};})''')
        assert all(abs(x['a']-x['b']) < 1 and abs(x['ah']-x['bh']) < 1 for x in row_check)
        assert page.locator('.reading-scroll').count() == 1
        table_images = page.locator('[data-block-id="tr-table-image"] img')
        assert table_images.count() == 2
        assert table_images.nth(0).get_attribute('src') == table_images.nth(1).get_attribute('src')
        assert page.locator('[data-block-id="tr-table-image"] .asset-placeholder').count() == 0
        assert page.evaluate('window.__injected') is None
        assert page.locator('article script,article img[onerror],article a[href^="javascript:"]').count() == 0
        page.screenshot(path=str(ARTIFACTS / 'reader-v3-translation.png'), full_page=True)
        # English selection, overlap markers, and anchors survive a window reflow.
        page.evaluate('''() => {
          const f=document.querySelector('.language-en [data-content-id="tr-p1"].anchor-field');
          const r=ReaderV3.rangeFor(f,8,45);getSelection().removeAllRanges();getSelection().addRange(r);
          ReaderV3.newDraft(ReaderV3.captureSelection(),'scientific papers');
        }''')
        assert page.evaluate('ReaderV3.getState().drafts[0].anchor.language') == 'en'
        page.locator('#annotation-note').fill('How do anchors restore this English phrase?')
        page.locator('.primary-button').click()
        page.wait_for_function('ReaderV3.getState().annotations.length === 2')
        english_id = anns[-1]['id']
        page.evaluate('''() => {
          const a=ReaderV3.getState().annotations.at(-1);
          const hash=id=>{let h=0;for(let i=0;i<id.length;i++)h=((h<<5)-h+id.charCodeAt(i))|0;return Math.abs(h)%6;};
          let id='';for(let i=0;i<100;i++){id='overlap-collision-'+i;if(hash(id)===hash(a.id))break;}
          ReaderV3.getState().annotations.push({...a,id,identity_color:undefined,author:a.author,status:'failed',user_note:'Overlapping annotation'});
          ReaderV3.drawAnnotations();
        }''')
        assert page.locator('.annotation-marker').count() == 2
        marker_colors = page.locator('.annotation-marker').evaluate_all('(ns)=>ns.map(n=>n.style.getPropertyValue("--ann-color"))')
        assert len(set(marker_colors)) == 2, 'Equal hash candidates must get different stable colors when overlapping'
        page.set_viewport_size({'width': 1160, 'height': 850})
        page.wait_for_timeout(150)
        assert page.locator('.annotation-marker').count() == 2
        assert page.locator('.annotation-marker').evaluate_all('(ns)=>ns.map(n=>n.style.getPropertyValue("--ann-color"))') == marker_colors
        assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.getState().annotations.at(-1)).status') == 'located'
        page.screenshot(path=str(ARTIFACTS / 'reader-v3-highlight.png'), full_page=True)
        # New answer, independent unread state, safe model markup, followup, retry and deletion.
        ann=anns[-1]
        ann.update(answer='**锚点 $x$ 会恢复。**\n$$\n\\frac{a}{b} + \\sum_{i=1}^{n} x_i\n$$\n<img src=x onerror="window.__injected=true">\n[unsafe](javascript:alert(1))', status='answered', unread=True, revision=2)
        ann['history']=[{'user_note':ann['user_note'],'answer':ann['answer'],'sources':[{'label':'方法来源','content_id':'tr-p2','page':1}],'status':'answered'}]
        page.evaluate('ReaderV3.poll()')
        page.wait_for_function('ReaderV3.getState().annotations.some(a=>a.unread)')
        assert '1 新' in page.locator('#unread-count').inner_text()
        assert '新回答' in page.locator('#toast').inner_text()
        page.evaluate('(id)=>ReaderV3.openAnnotation(id)', english_id)
        page.wait_for_function('!ReaderV3.getState().annotations.find(a=>a.id===ReaderV3.getState().selected).unread')
        assert page.locator('.answer-content img,.answer-content script,.answer-content a[href^="javascript:"]').count() == 0
        assert page.locator('.answer-content .math-atom[data-math-state="rendered"]').count() == 2
        assert page.locator('.answer-content .math-error,merror').count() == 0
        assert page.evaluate('window.__injected') is None
        page.locator('#annotation-followup').fill('窗口大小变化后仍然稳定吗？')
        page.get_by_role('button', name='提交追问', exact=True).click()
        page.wait_for_function('ReaderV3.getState().annotations.find(a=>a.id===ReaderV3.getState().selected).status === "queued"')
        assert any(r[1].endswith('/followups') for r in requests)
        ann.update(status='failed', revision=ann['revision']+1)
        page.evaluate('ReaderV3.poll()')
        page.wait_for_function('ReaderV3.getState().annotations.find(a=>a.id===ReaderV3.getState().selected).status === "failed"')
        page.get_by_role('button',name='重试答疑',exact=True).click()
        page.wait_for_function('ReaderV3.getState().annotations.find(a=>a.id===ReaderV3.getState().selected).status === "queued"')
        assert any(r[1].endswith('/retry') for r in requests)
        page.get_by_role('button',name='删除批注',exact=True).click()
        page.wait_for_function('ReaderV3.getState().annotations.length === 1')
        assert len(anns) == 1
        # Source jumps and reading position are independent in each view.
        page.locator('#panel-close').click()
        page.locator('[data-view="overview"]').click()
        page.get_by_role('button',name='引言 · 第 1 段',exact=True).click()
        page.wait_for_function('ReaderV3.getState().view === "translation"')
        page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        page.evaluate('document.getElementById("reading-scroll").scrollTop=300')
        page.locator('[data-view="explanation"]').click()
        page.locator('[data-view="translation"]').click()
        assert abs(page.evaluate('document.getElementById("reading-scroll").scrollTop')-300) <= 1
        # Invalid LaTeX must be explicitly reported rather than silently shown as success.
        page.evaluate('''() => { PAPER_DATA.explanation.sections[0].blocks.push({id:'bad-latex',type:'formula',latex:'\\\\unknowncommand{',text_zh:'故意损坏的公式'}); ReaderV3.switchView('explanation'); }''')
        assert page.locator('.math-atom[data-math-state="error"]').count() == 1
        assert '公式渲染错误' in page.locator('.math-error-label').inner_text()
        assert page.locator('.math-atom[data-math-state="rendered"]').count() == 4
        assert page.locator('merror,.katex-error').count() == 0
        page.evaluate('''() => {PAPER_DATA.explanation.sections[0].blocks.push({id:'unclosed-math',type:'paragraph',text_zh:'Unclosed \\\\(x^2'});ReaderV3.switchView('overview');ReaderV3.switchView('explanation');}''')
        assert page.locator('[data-content-id="unclosed-math"] .math-error').count() == 1
        # CAS conflict preserves local edits and refreshes revision for explicit retry.
        original_note=anns[0]['user_note']
        page.evaluate('(id)=>ReaderV3.openAnnotation(id)',anns[0]['id'])
        page.locator('#annotation-note').fill('CAS conflict draft stays local')
        anns[0]['revision'] += 1
        page.get_by_role('button',name='保存修改',exact=True).click()
        page.wait_for_function('document.getElementById("toast").textContent.includes("别处更新")')
        assert anns[0]['user_note'] == original_note
        assert page.locator('#annotation-note').input_value() == 'CAS conflict draft stays local'
        page.get_by_role('button',name='保存修改',exact=True).click()
        page.wait_for_function('document.getElementById("toast").textContent === "修改已保存。"')
        assert anns[0]['user_note'] == 'CAS conflict draft stays local'
        # Reload keeps drafts and their stable anchors without submitting them.
        page.evaluate('''() => {const a=ReaderV3.getState().annotations[0];ReaderV3.newDraft(a.anchor,a.selected_text);}''')
        page.locator('#annotation-note').fill('草稿重载后仍然保留')
        draft_id=page.evaluate('ReaderV3.getState().drafts[0].id')
        page.reload()
        page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
        assert page.evaluate('ReaderV3.getState().drafts[0].user_note') == '草稿重载后仍然保留'
        page.evaluate('(id)=>ReaderV3.openAnnotation(id)',draft_id)
        assert page.locator('#annotation-note').input_value() == '草稿重载后仍然保留'
        assert len(anns)==1
        assert not errors, errors
        page.locator('#stop-service').click()
        page.wait_for_function('ReaderV3.getState().stopped && document.getElementById("stop-service").disabled')
        assert any(method == 'POST' and path == 'stop' for method, path, _ in requests)
        browser.close()
    server.shutdown()
    print(json.dumps({'ok':True,'checks':['three views','translation row synchronization','canonical cross-node offsets','cross-paragraph fragments','duplicate quote rejection','math hidden DOM exclusion','offline valid LaTeX rendering','invalid LaTeX explicit error','currency/path delimiter handling','bold and multiline answer math','historical text_zh_latex display and anchor migration','English anchors','overlap markers','resize reflow','safe user/model/svg rendering','floating panel geometry','submit','new answer toast/unread/read','followup','retry','delete','source navigation','per-view scroll state','CAS409 edits preservation and retry','draft reload persistence'],'screenshots':str(ARTIFACTS)},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
