"""v3.1 real browser behavior with mock APIs; no model or production services."""
import base64
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_frontend_v3 import StaticHandler, ASSETS, FIXTURE

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(__file__).parent / 'artifacts-novice'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN0cAAAAASUVORK5CYII=')


class Handler(StaticHandler):
    def do_GET(self):
        if self.path.split('?')[0] == '/paper-data.js':
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.end_headers()
            addon = Path(__file__).parent / 'frontend-novice-fixture' / 'learning-data.js'
            self.wfile.write((FIXTURE / 'paper-data.js').read_bytes() + b'\n' + addon.read_bytes())
        elif self.path.startswith('/assets/annotations/'):
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.end_headers()
            self.wfile.write(PNG)
        else:
            super().do_GET()


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    annotations, drafts, requests, errors = [], {}, [], []
    auth = {'token': 'initial', 'sessions': 0, 'rotate': False}
    assistant = {'enabled': True, 'state': 'waiting', 'message': '答疑未连接', 'allowed_actions': ['ensure', 'pause'], 'pending_count': 0, 'service_instance_id': 'instance-1'}

    def route_api(route):
        req = route.request
        path = req.url.split('/api/', 1)[1].split('?')[0]
        body = json.loads(req.post_data or '{}')
        requests.append((req.method, path, body))
        code, result = 200, {}
        if path == 'session':
            auth['sessions'] += 1
            result = {'token': auth['token'], 'paper_id': 'frontend_v3_fixture', 'service_instance_id': 'instance-1'}
        elif path == 'events':
            route.fulfill(content_type='text/event-stream', body=': mock\n\n')
            return
        elif auth['rotate']:
            auth.update(rotate=False, token='rotated')
            code, result = 401, {'error': 'token expired'}
        elif req.headers.get('x-reader-token') != auth['token']:
            code, result = 401, {'error': 'token expired'}
        elif path == 'status':
            result = {'assistant': assistant, 'bridge_available': False}
        elif path.startswith('assistant/'):
            assert body['request_id']
            action = path.split('/')[-1]
            if action == 'pause':
                assistant.update(enabled=False, state='paused', message='答疑已暂停', allowed_actions=['enable'])
            elif action == 'ensure':
                if assistant['enabled'] or body.get('enable'):
                    assistant.update(enabled=True, state='connected', message='宿主已连接，答疑尚未验证', allowed_actions=['pause', 'rebuild'])
            elif action == 'rebuild':
                assistant.update(state='connected', message='会话已重建，结果不明的问题仍需确认')
            result = {'assistant': assistant}
        elif path == 'assets':
            assert len(base64.b64decode(body['data_base64'])) <= 1048576
            result = {'attachment': {'id': 'uploaded-1', 'src': 'assets/annotations/mock.png', 'media_type': 'image/png', 'alt': body['alt'], 'caption': body['caption'], 'sha256': 'mock'}}
        elif '/drafts' in path:
            tail = path.split('/drafts', 1)[1].strip('/')
            if req.method == 'GET':
                result = {'drafts': list(drafts.values())}
            elif req.method == 'DELETE':
                drafts.pop(tail, None)
                result = {'ok': True}
            else:
                prior = drafts.get(body['id'])
                if prior and body.get('revision') != prior['revision']:
                    code, result = 409, {'current': prior}
                else:
                    record = {'id': body['id'], 'data': body['data'], 'revision': (prior or {}).get('revision', 0) + 1}
                    drafts[body['id']] = record
                    result = {'draft': record}
        elif '/annotations' in path:
            tail = path.split('/annotations', 1)[1].strip('/')
            if req.method == 'GET':
                result = {'annotations': annotations}
            elif not tail:
                annotation = dict(body, status='queued', revision=1, history=[], unread=False)
                annotations.append(annotation)
                result = {'annotation': annotation}
            else:
                aid, _, action = tail.partition('/')
                annotation = next(a for a in annotations if a['id'] == aid)
                if action == 'read':
                    annotation.update(unread=False, revision=annotation['revision'] + 1)
                elif action == 'followups':
                    annotation['history'].append(dict(body, status='queued'))
                    annotation.update(status='queued', revision=annotation['revision'] + 1)
                elif req.method == 'PATCH':
                    annotation.update(body, revision=annotation['revision'] + 1)
                result = {'annotation': annotation}
        route.fulfill(status=code, content_type='application/json', body=json.dumps(result))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', route_api)
        url = f'http://127.0.0.1:{server.server_port}/reader-v3.html'
        page.goto(url)
        page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
        assert page.get_by_role('switch', name='说人话', exact=True).get_attribute('aria-checked') == 'true'
        assert page.locator('.ai-supplement').count() == 1
        assert '容量' in page.locator('.ai-supplement').inner_text()
        assert page.locator('.ai-supplement').evaluate('(n)=>getComputedStyle(n).backgroundColor') != page.locator('body').evaluate('(n)=>getComputedStyle(n).backgroundColor')
        page.screenshot(path=str(ARTIFACTS / 'overview.png'))
        walkthrough=page.locator('[data-aid-id="walk"]')
        walkthrough.get_by_role('button',name='下一步',exact=True).click()
        assert walkthrough.locator('.step-counter').inner_text()=='2 / 3'
        assert '从书架取书' in walkthrough.locator('.step-stage').inner_text()
        prediction=page.locator('[data-aid-id="predict"]')
        prediction.get_by_role('button',name='下降',exact=True).click()
        prediction.get_by_role('button',name='看解释',exact=True).click()
        assert '再想一步' in prediction.locator('.prediction-feedback').inner_text()
        prediction.get_by_role('button',name='上升',exact=True).click()
        prediction.get_by_role('button',name='看解释',exact=True).click()
        assert '预测正确' in prediction.locator('.prediction-feedback').inner_text()
        cache = page.locator('[data-aid-id="cache"]')
        for _ in range(6):
            cache.get_by_role('button', name='下一步', exact=True).click()
        assert cache.locator('.cache-stats').get_attribute('data-hits') == '2'
        assert cache.locator('.cache-stats').get_attribute('data-total') == '7'
        page.get_by_role('switch', name='说人话', exact=True).click()
        assert page.locator('.ai-supplement').is_hidden()
        page.reload()
        page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
        assert page.get_by_role('switch', name='说人话', exact=True).get_attribute('aria-checked') == 'false'
        page.get_by_role('switch', name='说人话', exact=True).click()
        assert page.locator('[data-aid-id="cache"] .cache-stats').get_attribute('data-hits') == '2'
        page.locator('[data-view="translation"]').click()
        assert page.get_by_role('switch', name='说人话', exact=True).count() == 0
        assert page.locator('.ai-supplement').count() == 0
        page.locator('[data-view="explanation"]').click()
        ratio = page.locator('#paper-content [data-aid-id="ratio"]')
        assert abs(float(ratio.locator('.explorer-result').get_attribute('data-result')) - 2/7) < 1e-8
        ratio.get_by_label('命中次数').fill('4')
        assert abs(float(ratio.locator('.explorer-result').get_attribute('data-result')) - 4/7) < 1e-8
        ratio.get_by_label('读取次数').fill('0')
        assert '分母为零' in ratio.locator('.explorer-result').inner_text()
        ratio.get_by_role('button', name='重置', exact=True).click()
        parameter=page.locator('[data-aid-id="parameter"]')
        parameter.get_by_label('项数',exact=True).fill('4')
        assert parameter.locator('.explorer-result').get_attribute('data-result')=='8'
        page.locator('.ai-supplement').screenshot(path=str(ARTIFACTS / 'formula-supplement.png'))
        assert page.locator('.formula-explanation .math-atom[data-math-state="rendered"]').count() >= 3
        assert not page.locator('.math-error').count()
        # A learning field selection still uses the original parent block ID.
        anchor = page.evaluate('''() => {
          const f=document.querySelector('[data-field="learning.plain_summary"]');
          const r=ReaderV3.rangeFor(f,0,8);getSelection().removeAllRanges();getSelection().addRange(r);
          return ReaderV3.captureSelection();
        }''')
        assert anchor['content_id'] == 'ex-formula' and anchor['field'] == 'learning.plain_summary'
        page.evaluate('(a)=>ReaderV3.newDraft(a,a.exact)', anchor)
        question='解释命中次数\n\n> 原式是什么？\n\n| 量 | 值 |\n| --- | --- |\n| H | 2 |\n\n$$\nh=2/7\n$$\n\n```text\n读取缓存\n```'
        page.locator('#annotation-note').fill(question)
        page.locator('#annotation-note').fill(question+'\n第二次编辑')
        page.locator('#annotation-note').press('End')
        page.locator('#annotation-note').press_sequentially('abc')
        for kind in ('formula','table','code'):
            assert page.evaluate('(type)=>ReaderV3.getState().drafts[0].note_content.blocks.filter(b=>b.type===type).length',kind)==1
        page.locator('.attachment-caption').first.fill('我的截图')
        page.locator('input[type=file]').first.set_input_files({'name': 'note.png', 'mimeType': 'image/png', 'buffer': PNG})
        page.wait_for_function('ReaderV3.getState().drafts[0].attachments?.length === 1')
        page.get_by_label('引用学习组件', exact=True).first.select_option('ratio')
        page.get_by_role('button', name='提交并提问', exact=True).click()
        page.wait_for_function('ReaderV3.getState().annotations.length === 1 && ReaderV3.getState().drafts.length === 0')
        annotation = annotations[0]
        assert annotation['note_content']['version'] == 1
        assert any(b['type'] == 'table' for b in annotation['note_content']['blocks'])
        assert any(b['type'] == 'figure' for b in annotation['note_content']['blocks'])
        assert any(b['type'] == 'learning_aid' for b in annotation['note_content']['blocks'])
        assert annotation['attachments'][0]['caption'] == '我的截图'
        # All major paper blocks can arrive in a saved answer; invalid asset paths cannot render.
        document = {'version': 1, 'blocks': [
            {'id': 'p', 'type': 'paragraph', 'text_zh': '**命中**是所需内容已经在缓存里。'},
            {'id': 'q', 'type': 'quote', 'text_zh': '引用与条件 $N>0$。'},
            {'id': 'f', 'type': 'formula', 'latex': 'h=\\frac{2}{7}'},
            {'id': 't', 'type': 'table', 'rows_zh': [['量', '值'], ['H', '2']]},
            {'id': 'img', 'type': 'figure', 'src': 'assets/annotations/mock.png', 'caption_zh': '已登记截图'},
            {'id': 'bad', 'type': 'figure', 'src': '../outside.png'},
            {'id': 'code', 'type': 'code', 'code': '<script>window.injected=1</script>'},
            {'id': 'a', 'type': 'algorithm', 'algo_title': '读取', 'algo_lines': ['检查缓存', '更新位置']},
            {'id': 'd', 'type': 'details', 'title': '完整推导', 'blocks': [{'id': 'dp', 'type': 'paragraph', 'text_zh': '二除以七。'}]},
            {'id': 'aid', 'type': 'learning_aid', 'aid_id': 'ratio'}]}
        annotation.update(answer='丰富回答', answer_content=document, status='answered', unread=False, revision=2)
        annotation['history']=[{'user_note':annotation['user_note'],'note_content':annotation['note_content'],'answer':'丰富回答','answer_content':document,'status':'answered','attachments':annotation['attachments']}]
        page.locator('#annotation-note').blur()
        page.evaluate('ReaderV3.poll()')
        page.wait_for_selector('.answer-content .rich-formula')
        assert page.locator('.answer-content table').count() == 1
        assert page.locator('.answer-content img').count() == 1
        assert page.locator('.answer-content .asset-placeholder').count() == 1
        assert page.locator('.answer-content script').count() == 0
        assert page.evaluate('window.injected') is None
        assert page.locator('.answer-content details.rich-details').count() == 1
        assert page.locator('.answer-content .learning-component').count() == 1
        answer_ratio=page.locator('.answer-content [data-aid-id="ratio"]')
        answer_ratio.get_by_label('命中次数').fill('6')
        assert abs(float(ratio.locator('.explorer-result').get_attribute('data-result'))-2/7)<1e-8
        page.locator('#plain-language-toggle').click()
        assert page.locator('.answer-content').is_visible()
        page.locator('#annotation-followup').fill('图片仍保留吗？')
        page.get_by_role('button',name='提交追问',exact=True).click()
        page.wait_for_function('ReaderV3.getState().annotations[0].history.length === 2')
        assert 'note_content' in annotation['history'][-1]
        page.screenshot(path=str(ARTIFACTS / 'rich-answer.png'))
        # 401 recovers session token, renews SSE and retries the authorized request once.
        before_sessions=auth['sessions'];auth['rotate']=True
        page.evaluate('ReaderV3.poll()')
        page.wait_for_function('ReaderV3.getState().token === "rotated"')
        assert auth['sessions']>before_sessions
        page.get_by_role('button',name='连接答疑',exact=True).click()
        page.wait_for_function('ReaderV3.getState().assistant.state === "connected"')
        assert '尚未验证' in page.locator('#connection-status').inner_text()
        page.get_by_role('button',name='暂停本库答疑',exact=True).click()
        page.wait_for_function('ReaderV3.getState().assistant.state === "paused"')
        page.reload();page.wait_for_function('ReaderV3.getState().assistant?.state === "paused"')
        page.get_by_role('button',name='开启答疑',exact=True).click()
        page.wait_for_function('ReaderV3.getState().assistant.state === "connected"')
        assert any(path=='assistant/ensure' and payload.get('enable') for _,path,payload in requests)
        # Managed draft survives a fresh browser origin/context.
        page.evaluate('(a)=>ReaderV3.newDraft(a,a.exact)',anchor)
        page.locator('#annotation-note').fill('跨地址保留的草稿')
        page.evaluate('ReaderV3.syncManagedDrafts()')
        page.wait_for_function('ReaderV3.getState().managedLoaded')
        page.wait_for_timeout(800)
        assert any(d['data'].get('user_note')=='跨地址保留的草稿' for d in drafts.values())
        other=browser.new_context(viewport={'width':390,'height':844},color_scheme='dark')
        mobile=other.new_page();mobile.route('**/api/**',route_api);mobile.goto(url)
        mobile.wait_for_function('ReaderV3.getState().drafts.some(d=>d.user_note === "跨地址保留的草稿")')
        assert mobile.get_by_role('switch',name='说人话',exact=True).get_attribute('aria-checked')=='false'
        mobile.screenshot(path=str(ARTIFACTS / 'mobile-dark.png'))
        mobile.get_by_role('switch',name='说人话',exact=True).click()
        assert mobile.locator('#paper-content [data-aid-id="cache"] .cache-stats').get_attribute('data-hits')=='2'
        # Deleting a synced local draft persists to the vault and cannot revive in a new context.
        draft_id=page.evaluate('ReaderV3.getState().drafts[0].id')
        page.evaluate('(id)=>ReaderV3.openAnnotation(id)',draft_id)
        page.get_by_role('button',name='删除批注',exact=True).click()
        page.wait_for_function('ReaderV3.getState().drafts.length === 0')
        assert draft_id not in drafts
        fresh=browser.new_context();fresh_page=fresh.new_page();fresh_page.route('**/api/**',route_api);fresh_page.goto(url)
        fresh_page.wait_for_function('ReaderV3.getState().managedLoaded')
        assert fresh_page.evaluate('ReaderV3.getState().drafts.length')==0
        assert not errors,errors
        browser.close()
    server.shutdown()
    print(json.dumps({'ok':True,'checks':['toggle false persists','translation excludes supplements','ordinary words and formula explanations','step transitions','prediction feedback','LRU computed trace','AST arithmetic and zero division','learning anchors','Markdown table quote','repeat Markdown edits without duplicate blocks','upload plus registered image','rich round history','learning state isolation','followup preservation','401 token/SSE refresh','truthful assistant connection','durable pause/explicit resume','managed draft fresh context','durable draft deletion','mobile dark theme'],'screenshots':str(ARTIFACTS)},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
