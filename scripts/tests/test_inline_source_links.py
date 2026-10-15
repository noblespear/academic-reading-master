"""Source fragments reuse reader navigation in Markdown and cleaned tables."""
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from test_frontend_v3 import StaticHandler, FIXTURE

OUTPUT=Path(__file__).resolve().parents[3]/'validation'/'methodology-20261012'
EXTRA='''
PAPER_DATA.sections[0].blocks.push({id:'src-p004-b054',type:'paragraph',text_en:'Original source statement.',text_zh:'原文来源段落。'});
PAPER_DATA.sections[0].blocks.push({id:'来源段落',type:'paragraph',text_en:'Encoded source ID.',text_zh:'编码来源段落。'});
PAPER_DATA.explanation.sections[0].blocks.push({id:'clean-table',type:'table',table_html:'<table><tr><td><a href="#tr-sec2">表格章节</a></td><td><a href="#missing">表格未知来源</a></td><td><a href="javascript:alert(1)" onclick="window.injected=1">HTML危险</a></td><td><a href="https://example.test/clean">HTML外链</a></td></tr></table>'});
PAPER_DATA.explanation.sections[0].blocks.push({id:'external-sources',type:'paragraph',text_zh:'外部材料说明。',source_refs:[{url:'https://example.test/book'},{url:'https://example.test/book#page=7',page:7},{url:'javascript:alert(1)',page:9},{page:2}]});
'''


class Handler(StaticHandler):
    def do_GET(self):
        if self.path.split('?')[0].lstrip('/')=='paper-data.js':
            source=((FIXTURE/'paper-data.js').read_text(encoding='utf-8')+EXTRA).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.end_headers();self.wfile.write(source)
        else:super().do_GET()


def main():
    annotations=[{'id':'answer-1','paper_id':'frontend_v3_fixture','revision':1,'status':'answered','unread':False,
        'anchor':{'kind':'block','view':'explanation','language':'zh','content_id':'ex-formula','content_version':'fixture-1'},
        'selected_text':'公式','user_note':'如何核对来源？',
        'answer':'参见 [§3.3](#src-p004-b054)、[编码来源](#%E6%9D%A5%E6%BA%90%E6%AE%B5%E8%90%BD)、[章节来源](#tr-sec2)。[未知来源](#missing) [编码损坏](#%E0%A4) [外链](https://example.test/paper) [危险](javascript:alert(1)) [禁止协议](ftp://example.test/file)'}]
    errors=[]
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            context.add_init_script('window.EventSource=undefined;')
            context.route('https://example.test/**',lambda route:route.fulfill(status=200,content_type='text/html',body='<p>Isolated destination</p>'))
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            def api(route):
                req=route.request
                if '/api/session?' in req.url:result={'paper_id':'frontend_v3_fixture','token':'fixture'}
                elif '/api/status?' in req.url:result={'bridge_available':False}
                else:result={'annotations':annotations}
                route.fulfill(status=200,json=result)
            page.route('**/api/**',api)
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            page.evaluate('ReaderV3.switchView("explanation");ReaderV3.openAnnotation("answer-1")')
            answer=page.locator('.answer-content')
            assert answer.get_by_role('link',name='未知来源',exact=True).count()==0
            assert answer.get_by_role('link',name='编码损坏',exact=True).count()==0
            assert answer.get_by_role('link',name='危险',exact=True).count()==0
            assert answer.get_by_role('link',name='禁止协议',exact=True).count()==0
            assert '未知来源' in answer.inner_text() and '编码损坏' in answer.inner_text()
            assert answer.get_by_role('link',name='§3.3',exact=True).get_attribute('target') is None
            original_url=page.url
            def jump(name,selector):
                page.evaluate('ReaderV3.switchView("explanation")')
                before=len(context.pages)
                answer.get_by_role('link',name=name,exact=True).click()
                page.wait_for_function('ReaderV3.getState().view==="translation"')
                page.locator(selector+'.jump-flash').first.wait_for()
                assert page.url==original_url and len(context.pages)==before
            jump('§3.3','[data-content-id="src-p004-b054"]')
            jump('编码来源','[data-content-id="来源段落"]')
            jump('章节来源','[data-section-id="tr-sec2"]')
            external=answer.get_by_role('link',name='外链',exact=True)
            assert external.get_attribute('target')=='_blank'
            assert external.get_attribute('rel')=='noopener noreferrer'
            with page.expect_popup() as popup_info:external.click()
            popup=popup_info.value;popup.wait_for_load_state();assert popup.url=='https://example.test/paper';popup.close()
            page.locator('#panel-close').click()
            for view in ('overview','explanation'):
                page.evaluate('(view) => ReaderV3.switchView(view)',view)
                assert page.locator('#paper-content .block-sources, #paper-content .page-link, #paper-content .chapter-sources').count()==0,view
            page.evaluate('ReaderV3.switchView("explanation")')
            table=page.locator('[data-content-id="clean-table"]')
            # Source metadata stays on the paper, while user-requested answer
            # content keeps the source controls and their URL safety behavior.
            page.evaluate('''() => {
                ReaderV3.openAnnotation('answer-1');
                const surface=document.createElement('div');surface.dataset.sourceAudit='answer';document.querySelector('.answer-content').append(surface);
                const block=PAPER_DATA.explanation.sections[0].blocks.find(b=>b.id==='external-sources');
                ReaderV3.renderer.renderDocument(surface,{version:1,blocks:[block]},{language:'zh',scope:'answer/source-audit'});
            }''')
            sources=page.locator('[data-source-audit="answer"] .source-details')
            assert sources.locator('summary').inner_text()=='来源 · p.2'
            sources.locator('summary').click()
            assert sources.get_by_role('button',name='外部来源',exact=True).count()==1
            page.evaluate('window.sourceOpens=[];window.originalOpen=window.open;window.open=(...args)=>window.sourceOpens.push(args)')
            sources.get_by_role('button',name='外部来源',exact=True).click()
            sources.get_by_role('button',name='外部来源 · p.7',exact=True).click()
            sources.get_by_role('button',name='外部来源 · p.9',exact=True).click()
            sources.get_by_role('button',name='原文 · p.2',exact=True).click()
            opened=page.evaluate('window.sourceOpens')
            assert [item[0] for item in opened if isinstance(item[0],str)]==['https://example.test/book','https://example.test/book#page=7','source.pdf#page=2'],opened
            assert page.locator('#toast').inner_text()=='这条外部来源的网址无效。'
            page.evaluate('window.open=window.originalOpen')
            page.locator('#panel-close').click()
            page.evaluate('''() => {
                for(const [name,cls,parent] of [['supplement','ai-supplement',document.querySelector('[data-content-id="ex-formula"]')]]){
                    const surface=document.createElement('div');surface.className=cls;surface.dataset.mathAudit=name;parent.append(surface);
                    ReaderV3.renderer.renderDocument(surface,{version:1,blocks:[{id:name+'-formula',type:'formula',latex:'E=mc^2'},{id:name+'-wide',type:'formula',latex:Array(70).fill('x').join('+')},{id:name+'-inline',type:'paragraph',text:'Inline \\(x+1\\) text.'}]},{language:'zh'});
                }
            }''')
            page.evaluate('ReaderV3.openAnnotation("answer-1")')
            # Reopening renders the answer afresh, so add its formula through the shared renderer.
            page.evaluate('''() => {
                const surface=document.createElement('div');surface.className='ai-answer';surface.dataset.mathAudit='answer';document.querySelector('.answer-content').append(surface);
                ReaderV3.renderer.renderDocument(surface,{version:1,blocks:[{id:'answer-formula',type:'formula',latex:'E=mc^2'},{id:'answer-wide',type:'formula',latex:Array(70).fill('x').join('+')}]},{language:'zh'});
            }''')
            page.evaluate('document.fonts.ready')
            math_checks=page.evaluate('''() => {
                const rect=n=>n.getBoundingClientRect(),result=[];
                const body=document.querySelector('[data-content-id="ex-formula"] > .formula-display');
                for(const [name,formula] of [['body',body],...Array.from(document.querySelectorAll('[data-math-audit]')).map(s=>[s.dataset.mathAudit,s.querySelector('.formula-display')])]){
                    const render=formula.querySelector('.math-render'),expression=formula.querySelector('.katex-display > .katex');
                    result.push({name,centerError:Math.abs((rect(render).left+rect(render).right)/2-(rect(expression).left+rect(expression).right)/2),textAlign:getComputedStyle(expression.parentElement).textAlign});
                }
                for(const surface of document.querySelectorAll('[data-math-audit]')){
                    const wide=surface.querySelectorAll('.formula-display')[1],expression=wide.querySelector('.katex-display > .katex');
                    result.push({name:surface.dataset.mathAudit+'-wide',overflow:wide.scrollWidth>wide.clientWidth,leftReachable:rect(expression).left>=rect(wide).left-1,scrollLeft:wide.scrollLeft});
                }
                return result;
            }''')
            assert all(c.get('centerError',0)<2 and c.get('textAlign','center')=='center' and c.get('overflow',True) and c.get('leftReachable',True) and c.get('scrollLeft',0)==0 for c in math_checks),math_checks
            assert page.evaluate('(() => {const atoms=Array.from(document.querySelectorAll(".math-atom[data-display=false]"));return atoms.length>0&&atoms.every(n=>getComputedStyle(n).display!=="block")})()')
            page.locator('#panel-close').click()
            OUTPUT.mkdir(parents=True,exist_ok=True)
            page.locator('.block-frame[data-content-id="ex-formula"]').screenshot(path=str(OUTPUT/'centered-display-math.png'))
            assert table.get_by_role('link',name='表格未知来源',exact=True).count()==0
            assert table.get_by_role('link',name='HTML危险',exact=True).count()==0
            assert table.locator('[onclick]').count()==0
            assert table.get_by_role('link',name='HTML外链',exact=True).get_attribute('target')=='_blank'
            before=len(context.pages)
            table.get_by_role('link',name='表格章节',exact=True).click()
            page.wait_for_function('ReaderV3.getState().view==="translation"')
            page.locator('[data-section-id="tr-sec2"].jump-flash').wait_for()
            assert len(context.pages)==before and page.url==original_url
            assert not errors,errors
            OUTPUT.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(OUTPUT/'inline-source-jump.png'))
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    report={'ok':True,'checks':['Markdown source block, decoded Unicode block and section links switch to translation and highlight without opening a tab',
        'unknown/malformed fragments remain readable text rather than false links',
        'safe external link still opens a new tab; JavaScript and FTP remain blocked',
        'cleaned legacy HTML table uses the same navigation and removes unsafe handlers',
        'overview/explanation omit automatic source DOM while retaining handwritten links and source metadata',
        'independent rich-answer sources preserve external URL precedence; invalid external URL never falls back to local PDF',
        'body, supplement and rich-answer display equations center; wide equations retain a reachable left edge'],
        'math_checks':math_checks,'page_errors':errors,'scope':'synthetic source data and mocked API; user database untouched'}
    (OUTPUT/'inline-source-links-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
