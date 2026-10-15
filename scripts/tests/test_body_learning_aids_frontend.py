"""Run with Python/Playwright; isolated loopback reader and mocked question APIs."""
import copy
import json
import mimetypes
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from test_retired_preloaded_frontend import fixture as user_fixture
from test_narrative_contract import chart, graph

SKILL=Path(__file__).resolve().parents[2]
ASSETS=SKILL/'assets'
OUTPUT=SKILL.parent/'validation'


def fixture():
    data,annotations,draft=user_fixture()
    data['id']='body-aid-fixture'
    aid=chart();aid['id']='body-chart';aid['title']='原文成绩比较'
    aid['config']['models'].append({'id':'m2','label':'另一个模型'})
    aid['config']['observations'].extend([
        {'model_id':'m2','task_id':'t','series_id':'a','value':72},
        {'model_id':'m2','task_id':'t','series_id':'b','value':74}])
    data['overview']['sections'][0]['blocks'].insert(1,{
        'id':'body-chart-block','type':'learning_aid','placement':'body','evidence_kind':'paper',
        'source_refs':[{'page':5,'label':'Table1'}],'aid':aid})
    data['overview']['sections'][0]['blocks'].insert(2,{
        'id':'legacy-aid','type':'learning_aid','evidence_kind':'teaching_example','aid_id':'example'})
    nested=graph();nested['id']='nested-graph';nested['title']='两人传话的教学例子'
    nested['config']['stages'].append({'id':'alone','title':'只看A','text':'A先处理这个教学输入。',
        'active_node_ids':['a'],'active_edge_ids':[]})
    data['overview']['sections'][0]['blocks'][0]['learning']['blocks'].append({
        'id':'nested-graph-block','type':'learning_aid','aid':nested})
    return data,annotations,draft


def main():
    data,annotations,draft=fixture();errors,requests,checks=[],[],[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            if name=='paper-data.js':
                source=('window.PAPER_DATA='+json.dumps(data,ensure_ascii=False)+';').encode('utf-8');media='application/javascript'
            elif name=='assets/legacy-context.svg':
                source=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 45"><text x="10" y="28">Saved user context</text></svg>';media='image/svg+xml'
            else:
                path=(ASSETS/name).resolve()
                if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():self.send_error(404);return
                source=path.read_bytes();media=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',media+'; charset=utf-8');self.end_headers();self.wfile.write(source)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1360,'height':1000},color_scheme='dark')
            context.add_init_script('window.EventSource=undefined;localStorage.setItem('+json.dumps('arm_v3_'+data['id']+'_drafts')+','+json.dumps(json.dumps([draft],ensure_ascii=False))+');')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            def api(route):
                request=route.request;requests.append({'method':request.method,'url':request.url})
                if request.method!='GET':result={'ok':True,'drafts':[]}
                elif '/api/session?' in request.url:result={'paper_id':data['id'],'token':'fixture','service_instance_id':'fixture'}
                elif '/api/status?' in request.url:result={'bridge_available':False}
                else:result={'annotations':annotations}
                route.fulfill(status=200,json=result)
            page.route('**/api/**',api);page.route('https://**',lambda route:route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3&&ReaderV3.getState().online')
            body=page.locator('#paper-content [data-content-id="body-chart-block"][data-learning-placement="body"]')
            primary=body.locator('[data-aid-id="body-chart"]')
            legacy=page.locator('#paper-content [data-content-id="legacy-aid"] .ai-supplement')
            nested=page.locator('#paper-content [data-aid-id="nested-graph"]')
            assert body.is_visible() and primary.is_visible() and body.locator('.ai-supplement').count()==0
            assert '论文结果 · 原文数值' in primary.inner_text() and body.locator('.block-sources button').count()==2
            assert body.get_by_role('button',name='对整块内容提问',exact=True).is_visible()
            assert legacy.is_visible() and nested.is_visible()
            primary.get_by_role('combobox',name='使用的模型',exact=True).select_option('m2')
            assert primary.get_attribute('data-model-id')=='m2' and '74.00%' in primary.inner_text()
            nested.get_by_role('button',name='只看A',exact=True).click()
            before=page.evaluate('JSON.stringify(ReaderV3.getState().studyState)')
            page.locator('#plain-language-toggle').click()
            assert primary.is_visible() and not legacy.is_visible() and not nested.is_visible()
            assert page.locator('#paper-content .ai-supplement:visible').count()==0
            assert page.evaluate('JSON.stringify(ReaderV3.getState().studyState)')==before
            assert primary.get_attribute('data-model-id')=='m2'
            page.locator('#plain-language-toggle').click()
            assert legacy.is_visible() and nested.is_visible()
            assert nested.get_by_role('button',name='只看A',exact=True).get_attribute('aria-pressed')=='true'
            checks.append('explicit body chart is visible with original values, provenance and whole-block question; toggle hides old and nested AI aids while preserving every component state')

            assert page.evaluate('ReaderV3.allAnnotations().map(a=>a.id)')==['user-1','user-from-old-note','draft-from-old-note']
            assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.allAnnotations()[0]).status')=='located'
            page.evaluate('ReaderV3.openAnnotation("user-1")')
            assert page.locator('.answer-turn').count()==2
            assert page.evaluate('ReaderV3.allAnnotations()[0].history')==annotations[0]['history']
            assert page.locator('.answer-content.ai-answer .answer-title').all_text_contents()==['答疑','答疑']
            page.locator('#panel-close').click()
            body.get_by_role('button',name='对整块内容提问',exact=True).click()
            page.locator('#annotation-note').fill('为什么这个模型的成绩更高？')
            created=page.evaluate('ReaderV3.getState().drafts.at(-1)')
            assert created['anchor']['kind']=='block' and created['anchor']['content_id']=='body-chart-block'
            assert created['study_state']['components']['paper/overview/body-chart-block/body-chart']['model_id']=='m2'
            assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.getState().drafts.at(-1)).status')=='located'
            retained=page.evaluate('ReaderV3.getState().drafts.find(d=>d.id==="draft-from-old-note")')
            assert retained['note_content']==draft['note_content'] and retained['study_state']==draft['study_state']
            page.locator('#panel-close').click()
            checks.append('normal user numbering, locator, two-turn rich answers and saved old draft remain; independent answers use the QA heading; body chart creates only a user-editable block draft carrying current model state')

            page.evaluate('ReaderV3.switchView("translation")')
            assert page.locator('#paper-content .ai-supplement,#paper-content .learning-component').count()==0
            assert page.locator('.block-frame[data-content-id="source-body"][data-language="zh"]').is_visible()
            page.evaluate('ReaderV3.switchView("overview")')
            assert primary.get_attribute('data-model-id')=='m2'
            assert nested.get_by_role('button',name='只看A',exact=True).get_attribute('aria-pressed')=='true'
            page.reload();page.wait_for_function('window.ReaderV3&&ReaderV3.getState().online')
            assert primary.get_attribute('data-model-id')=='m2'
            assert nested.get_by_role('button',name='只看A',exact=True).get_attribute('aria-pressed')=='true'
            page.evaluate('ReaderV3.setPlainLanguage(false)')
            assert primary.is_visible() and not legacy.is_visible() and not nested.is_visible()
            assert page.locator('[data-content-id="paper-formula"] .math-atom[data-math-state="rendered"]').is_visible()
            assert page.locator('.block-frame[data-content-id="paper-result"]').is_visible()
            assert page.evaluate('getComputedStyle(document.body).backgroundColor')=='rgb(255, 255, 255)'
            body.scroll_into_view_if_needed();page.locator('.paper-width').screenshot(path=str(OUTPUT/'body-learning-aids-desktop.png'))
            page.set_viewport_size({'width':320,'height':780})
            body.scroll_into_view_if_needed();assert primary.is_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('.paper-width').screenshot(path=str(OUTPUT/'body-learning-aids-mobile320.png'))
            checks.append('switching views and reloading restores body model and nested stage; translation remains bilingual without new automatic content; original formula/results and light 320px layout remain usable')
            submissions=[r for r in requests if r['method']=='POST' and ('/annotations' in r['url'] or '/bridge/' in r['url'])]
            assert submissions==[] and errors==[],(submissions,errors)
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    report={'ok':True,'checks':checks,'question_posts':[],'page_errors':errors}
    (OUTPUT/'body-learning-aids-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
