"""Real-browser regression for annotation scroll during polling and SSE.

Uses only synthetic answers and local HTTP; no paper vault or model calls.
Run: python scripts/tests/test_panel_scroll.py
"""
import json
import threading
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from test_frontend_v3 import StaticHandler


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    answer = '\n\n'.join(
        f'第 {i + 1} 段：这里是合成的长回答，用于检查阅读位置。'
        '后台检查回答状态时，读者仍应停留在正在阅读的这一段。'
        r'变量 \(x_i\) 的含义也应保持清晰。'
        for i in range(24))
    annotations = [
        {'id': f'scroll-fixture-{i}', 'paper_id': 'frontend_v3_fixture',
         'revision': 1, 'status': 'answered', 'unread': False,
         'anchor': {'kind': 'block', 'view': 'overview', 'language': 'zh',
                    'content_id': 'ov-summary', 'content_version': 'fixture-1'},
         'selected_text': '稳定锚点', 'user_note': f'合成问题 {i + 1}',
         'answer': answer, 'history': [{'user_note': f'合成问题 {i + 1}',
                                      'answer': answer, 'status': 'answered'}]}
        for i in range(12)]
    requests = {'snapshots': 0}

    def api(route):
        path = route.request.url.split('/api/', 1)[1].split('?')[0]
        if path == 'session':
            data = {'paper_id': 'frontend_v3_fixture', 'token': 'scroll-test-token'}
        elif path == 'status':
            data = {'bridge_available': True}
        elif path == 'events':
            route.fulfill(status=200, content_type='text/event-stream', body=': fixture\n\n')
            return
        else:
            assert path == 'papers/frontend_v3_fixture/annotations'
            assert route.request.method == 'GET'
            requests['snapshots'] += 1
            data = {'annotations': annotations, 'revision': requests['snapshots']}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 850})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('''
                // Deliver SSE using the same event handlers as the real client.
                window.EventSource = class extends EventTarget {
                    constructor() { super(); window.fixtureEvents = this; }
                    close() {}
                };
            ''')
            page.route('**/api/**', api)
            page.route('https://**', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('ReaderV3.getState().online && !ReaderV3.getState().polling')
            page.evaluate('ReaderV3.openAnnotation("scroll-fixture-0")')
            page.evaluate('document.fonts.ready')
            baseline = page.evaluate('''() => {
                const body = document.getElementById('panel-body');
                body.scrollTop = 390;
                window.originalAnswer = body.querySelector('.answer-content');
                const text = originalAnswer.querySelector('p').firstChild;
                const range = document.createRange();
                range.setStart(text, 0); range.setEnd(text, 6);
                getSelection().removeAllRanges(); getSelection().addRange(range);
                return {top: body.scrollTop, selected: getSelection().toString()};
            }''')
            count = requests['snapshots']
            # Observe two genuine five-second fallback intervals, not just a direct render.
            page.wait_for_timeout(10800)
            after = page.evaluate('''() => ({top: document.getElementById('panel-body').scrollTop,
                same: document.querySelector('.answer-content') === originalAnswer,
                selected: getSelection().toString()})''')
            assert requests['snapshots'] >= count + 2, requests
            assert abs(after['top'] - baseline['top']) <= 1, {'before': baseline, 'after': after}
            assert after['same'], 'Unchanged polling rebuilt the answer DOM.'
            assert after['selected'] == baseline['selected'], 'Polling lost the reading selection.'

            # An update to another annotation must not disturb the open answer.
            annotations[1]['unread'] = True
            page.evaluate('ReaderV3.poll()')
            assert page.evaluate('document.querySelector(".answer-content") === originalAnswer')
            assert page.evaluate('document.getElementById("panel-body").scrollTop') == baseline['top']

            # A real new answer for the open annotation is visible without moving the viewport.
            annotations[0]['answer'] += '\n\n新增段落：来自后台的真实内容变化。'
            annotations[0]['history'][0]['answer'] = annotations[0]['answer']
            annotations[0]['unread'] = True
            page.evaluate('''a => fixtureEvents.dispatchEvent(new MessageEvent('annotation',
                {data: JSON.stringify({paper_id: a.paper_id, annotation: a})}))''', annotations[0])
            assert '新增段落' in page.locator('.answer-content').inner_text()
            assert abs(page.evaluate('document.getElementById("panel-body").scrollTop') - baseline['top']) <= 1
            assert '新回答' in page.locator('#toast').inner_text()

            # Focused follow-up input, caret and local draft survive periodic refresh.
            page.locator('#annotation-followup').fill('追问草稿保持原样')
            input_state = page.evaluate('''() => {
                const input = document.getElementById('annotation-followup');
                input.setSelectionRange(3, 5); window.originalInput = input;
                return {top: document.getElementById('panel-body').scrollTop,
                        start: input.selectionStart, end: input.selectionEnd};
            }''')
            page.evaluate('ReaderV3.poll()')
            assert page.evaluate('document.activeElement === originalInput')
            assert page.locator('#annotation-followup').input_value() == '追问草稿保持原样'
            assert page.evaluate('originalInput.selectionStart') == input_state['start']
            assert page.evaluate('originalInput.selectionEnd') == input_state['end']
            assert page.evaluate('document.getElementById("panel-body").scrollTop') == input_state['top']
            page.evaluate('document.activeElement.blur()')

            # List refresh is also stable, while filters and a different detail start at the top.
            page.evaluate('ReaderV3.openList()')
            list_top = page.evaluate('''() => {
                const body = document.getElementById('panel-body'); body.scrollTop = 260;
                window.originalList = body.querySelector('.annotation-list'); return body.scrollTop;
            }''')
            for _ in range(3):
                page.evaluate('ReaderV3.poll()')
            assert page.evaluate('document.querySelector(".annotation-list") === originalList')
            assert page.evaluate('document.getElementById("panel-body").scrollTop') == list_top
            annotations[2]['user_note'] = '列表内一个真正修改的问题'
            page.evaluate('ReaderV3.poll()')
            assert '真正修改' in page.locator('.annotation-list').inner_text()
            assert page.evaluate('document.getElementById("panel-body").scrollTop') == list_top
            page.evaluate('ReaderV3.openAnnotation("scroll-fixture-3")')
            assert page.evaluate('document.getElementById("panel-body").scrollTop') == 0
            assert not errors, errors
            browser.close()
        print(json.dumps({'ok': True, 'checks': ['five-second polling keeps scroll/DOM/selection',
            'unrelated updates keep open answer', 'new SSE answers update without scrolling',
            'follow-up focus/caret/draft kept', 'list refresh keeps scroll', 'different annotation starts at top']},
            ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
