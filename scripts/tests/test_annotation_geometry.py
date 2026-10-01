"""Real Chromium regression for closed-details and selection-tail geometry.

The cached-rectangle probe reproduces the in-app browser's closed <details>
behavior independently of the Chromium version installed for testing.
No production annotation API or model calls are made.
"""
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
from test_frontend_v3 import StaticHandler, ARTIFACTS


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'ok': False, 'checks': []}
    errors = []

    def api(route):
        name = route.request.url.split('/api/', 1)[1].split('?')[0]
        data = {'token': 'geometry-fixture', 'paper_id': 'frontend_v3_fixture'} if name == 'session' else (
            {'bridge_available': False} if name == 'status' else {'annotations': []})
        if name == 'events':
            route.fulfill(status=200, content_type='text/event-stream', body=': fixture\n\n')
        else:
            route.fulfill(status=200, content_type='application/json', body=json.dumps(data))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1000, 'height': 820})
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/api/**', api)
            page.route('https://**', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('ReaderV3 && ReaderV3.getState().online')
            page.evaluate(r'''() => {
              ReaderV3.getState().stopped=true;
              PAPER_DATA.explanation.sections=[{id:'geometry',title_cn:'批注坐标回归',blocks:[
                {id:'geo-before',type:'paragraph',text_zh:'这段正文在折叠讲解之前，跨段选择时仍然应当保留。',annotations:[{title:'原有讲解',body:'这是已折叠的旧讲解。它的缓存坐标不能画到下一段正文上。'}]},
                {id:'geo-after',type:'paragraph',text_zh:'当相邻面在材质编号或标量属性上不连续时，网格中就出现 sharp edge（尖锐边/不连续曲线）。这些曲线包括物体边界、材质边界、颜色突变和**法线断裂**。后续简化会额外保护它们。'},
                {id:'geo-math',type:'paragraph',text_zh:'跨标签的**加粗文字**及公式 \(\frac{a}{b}+v_s\) 只测量一次，屏幕缩放后仍应贴合原文。'},
                {id:'geo-long',type:'paragraph',text_zh:'用于检查换行和滚动后的选区。'.repeat(18)}
              ]}];ReaderV3.switchView('explanation');
              const details=document.querySelector('[data-content-id="geo-before"].block-frame details');
              details.open=true;
              const hidden=document.querySelector('[data-content-id="geo-before"][data-field="annotations.0.body"]');
              hidden.getBoundingClientRect();details.open=false;
              const next=document.querySelector('[data-content-id="geo-after"].anchor-field');
              const stale=next.getBoundingClientRect();
              const elementRects=Element.prototype.getClientRects,rangeRects=Range.prototype.getClientRects;
              hidden.getClientRects=()=>[stale];
              Range.prototype.getClientRects=function(){const n=this.commonAncestorContainer;return !details.open&&hidden.contains(n.nodeType===1?n:n.parentElement)?[stale]:rangeRects.call(this);};
              window.geometryAnchor=(root,exact)=>{const text=ReaderV3.canonicalIndex(root).text,start=exact?text.indexOf(exact):0,end=exact?start+exact.length:text.length;const fragment={view:root.dataset.view,language:root.dataset.language,content_id:root.dataset.contentId,field:root.dataset.field,content_version:PAPER_DATA.content_version,start,end,exact:text.slice(start,end),prefix:text.slice(Math.max(0,start-40),start),suffix:text.slice(end,end+40)};return {...fragment,kind:'text',fragments:[fragment]};};
              ReaderV3.getState().annotations=[{id:'hidden-note',anchor:geometryAnchor(hidden),status:'answered',user_note:'缓存坐标不能显示',answer:'保留旧问答',selected_text:hidden.textContent},
                {id:'visible-word',anchor:geometryAnchor(next,'法线断裂'),status:'answered',user_note:'选区尾部',answer:'真实选区'}];
              ReaderV3.drawAnnotations();
            }''')
            page.wait_for_function('document.querySelectorAll(".annotation-marker").length===1')
            assert page.locator('.annotation-highlight[data-annotation-id="hidden-note"]').count() == 0
            assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.getState().annotations[0]).status') == 'located'
            assert page.evaluate(r'document.querySelector("[data-field=\"annotations.0.body\"]").getClientRects().length') == 1
            report['checks'].append('closed details with nonzero cached rectangles never paint')

            def assert_geometry():
                result = page.evaluate(r'''() => {
                  const bounds=document.getElementById('reading-scroll').getBoundingClientRect(),markers=[...document.querySelectorAll('.annotation-marker')],marks=[...document.querySelectorAll('.annotation-highlight:not(.block-highlight)')];
                  const textRects=[...document.querySelectorAll('.anchor-field')].filter(f=>{for(let n=f;n;n=n.parentElement)if(n.tagName==='DETAILS'&&!n.open)return false;return true;}).flatMap(f=>{const r=document.createRange();r.selectNodeContents(f);return [...r.getClientRects()].filter(r=>r.width>1&&r.height>1);});
                  const intersects=(a,b)=>a.left<b.right-.5&&a.right>b.left+.5&&a.top<b.bottom-.5&&a.bottom>b.top+.5;
                  return {covered:markers.flatMap(m=>textRects.filter(r=>intersects(m.getBoundingClientRect(),r)).map(r=>m.dataset.annotationId)),
                    overlaps:markers.flatMap((m,i)=>markers.slice(i+1).filter(n=>intersects(m.getBoundingClientRect(),n.getBoundingClientRect())).map(n=>[m.dataset.annotationId,n.dataset.annotationId])),
                    shifted:marks.filter(n=>getComputedStyle(n).transform!=='none').length,
                    wrongTail:markers.filter(n=>n.getBoundingClientRect().left<Math.min(Number(n.dataset.anchorX)+5,bounds.right-30)-1).map(n=>n.dataset.annotationId)};
                }''')
                assert result == {'covered': [], 'overlaps': [], 'shifted': 0, 'wrongTail': []}, result

            assert_geometry()
            report['checks'].append('visible word marker remains at selection tail')
            cross = page.evaluate(r'''() => {
              const a=document.querySelector('[data-content-id="geo-before"][data-field="text_zh"]'),b=document.querySelector('[data-content-id="geo-after"][data-field="text_zh"]');
              const first=ReaderV3.rangeFor(a,0,10),last=ReaderV3.rangeFor(b,0,10),r=document.createRange();r.setStart(first.startContainer,first.startOffset);r.setEnd(last.endContainer,last.endOffset);getSelection().removeAllRanges();getSelection().addRange(r);return ReaderV3.captureSelection().fragments.map(f=>f.field);
            }''')
            assert cross == ['text_zh', 'text_zh'], cross
            report['checks'].append('cross-paragraph selection excludes closed explanations')
            page.evaluate('getSelection().removeAllRanges()')
            page.locator('[data-content-id="geo-before"].block-frame details > summary').click()
            page.wait_for_function('document.querySelectorAll(".annotation-marker").length===2')
            assert page.locator('.annotation-highlight[data-annotation-id="hidden-note"]').count() > 0
            page.locator('[data-content-id="geo-before"].block-frame details > summary').click()
            page.wait_for_function(r'!document.querySelector(".annotation-marker[data-annotation-id=\"hidden-note\"]")')
            report['checks'].append('expand and collapse redraw the correct annotation')

            # Nested details: opening only the inner disclosure is insufficient.
            page.evaluate(r'''() => {const inner=document.querySelector('[data-content-id="geo-before"].block-frame details'),outer=document.createElement('details'),summary=document.createElement('summary');summary.textContent='外层折叠';outer.append(summary);inner.before(outer);outer.append(inner);inner.open=true;ReaderV3.drawAnnotations();}''')
            assert page.locator('.annotation-marker[data-annotation-id="hidden-note"]').count() == 0
            page.evaluate('ReaderV3.openAnnotation("hidden-note")')
            page.get_by_role('button', name='定位原文', exact=True).click()
            page.wait_for_function(r'document.querySelectorAll(".original-explanations[open]").length===1 && document.querySelector(".annotation-marker[data-annotation-id=\"hidden-note\"]")')
            assert page.locator('[data-content-id="geo-before"].block-frame details:not([open])').count() == 0
            page.locator('#panel-close').click()
            report['checks'].append('annotation jump reveals all nested disclosures')

            # True overlaps ending at the same word, with a longer selection
            # below them: bubbles must avoid selected text and each other.
            page.evaluate(r'''() => {
              const f=document.querySelector('[data-content-id="geo-after"].anchor-field'),word=geometryAnchor(f,'法线断裂'),whole=geometryAnchor(f);
              ReaderV3.getState().annotations=Array.from({length:4},(_,i)=>({id:'same-tail-'+i,anchor:word,status:'answered',user_note:'同一末端 '+i})).concat({id:'long-selection',anchor:whole,status:'answered',user_note:'长选区'});
              f.scrollIntoView({block:'center'});ReaderV3.drawAnnotations();
            }''')
            for width in [1000, 680, 1440]:
                page.set_viewport_size({'width': width, 'height': 820})
                page.wait_for_function('document.querySelectorAll(".annotation-marker").length===5')
                page.evaluate('ReaderV3.drawAnnotations()')
                assert_geometry()
            report['checks'].append('same-tail overlaps avoid selections at desktop and mobile widths')
            page.screenshot(path=str(ARTIFACTS / 'annotation-geometry-overlap.png'))

            # KaTeX's accessibility rectangles and nested bold nodes must not
            # inflate underline lanes within the same annotation.
            page.evaluate(r'''() => {const f=document.querySelector('[data-content-id="geo-math"].anchor-field');ReaderV3.getState().annotations=[{id:'mixed-inline',anchor:geometryAnchor(f),status:'answered',user_note:'文本与公式'}];f.scrollIntoView({block:'center'});ReaderV3.drawAnnotations();}''')
            assert page.locator('.annotation-highlight[data-annotation-id="mixed-inline"][data-lane]:not([data-lane="0"])').count() == 0
            assert_geometry()
            report['checks'].append('bold text and math measured without phantom overlap lanes')
            for offset in [120, -80]:
                page.evaluate('(offset)=>{document.getElementById("reading-scroll").scrollTop+=offset;}', offset)
                page.wait_for_timeout(40)
                assert_geometry()
            report['checks'].append('scrolling remeasures current text geometry')

            page.evaluate(r'''() => {ReaderV3.switchView('translation');const en=document.querySelector('.language-en [data-content-id="tr-p1"].anchor-field'),zh=document.querySelector('.language-zh [data-content-id="tr-p1"].anchor-field');ReaderV3.getState().annotations=[{id:'english',anchor:geometryAnchor(en,'stable anchors'),status:'answered'},{id:'chinese',anchor:geometryAnchor(zh,'稳定锚点'),status:'answered'}];en.scrollIntoView({block:'center'});ReaderV3.drawAnnotations();}''')
            for width in [1000, 680]:
                page.set_viewport_size({'width': width, 'height': 820})
                page.wait_for_function('document.querySelectorAll(".annotation-marker").length===2')
                assert_geometry()
            report['checks'].append('both translation columns preserve tail geometry')
            assert not errors, errors
            browser.close()
        report['ok'] = True
    finally:
        server.shutdown()
        server.server_close()
        (ARTIFACTS / 'annotation-geometry-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
