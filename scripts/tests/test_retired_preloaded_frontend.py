"""Reader 3.4 ignores preloaded notes; saved user questions remain editable.

Run directly with Python/Playwright. Loopback fixture only; every API is mocked.
"""
import copy
import json
import mimetypes
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

SKILL = Path(__file__).resolve().parents[2]
ASSETS = SKILL / 'assets'
OUTPUT = SKILL.parent / 'validation'


def document(*blocks):
    return {'version':1, 'blocks':list(blocks)}


def fixture():
    body='缓存先保留这份数据。需要同一份数据时直接读取。'
    anchor={'kind':'text','view':'overview','language':'zh','content_id':'body','field':'text_zh',
        'content_version':1,'start':0,'end':2,'exact':'缓存','prefix':'','suffix':'','fragments':[]}
    aid={'id':'example','type':'formula_explorer','version':1,'evidence_kind':'teaching_example',
        'config':{'parameters':[{'id':'x','label':'数量','min':0,'max':4,'step':1,'value':2}],
            'expression':{'op':'multiply','args':[{'parameter':'x'},2]},'result_label':'相加后的数'},
        'fallback_text':'两份各为2，相加为4。'}
    context=[
        {'id':'default-context-old-heading','type':'heading','text_zh':'早先由用户保存的引用语境'},
        {'id':'default-context-old-quote','type':'quote','text_zh':body},
        {'id':'default-context-old-formula','type':'formula','latex':'1+2=3'},
        {'id':'default-context-old-figure','type':'figure','src':'assets/legacy-context.svg','caption_zh':'用户旧追问保留的图片'}]
    question=document({'id':'reader-text-question','type':'paragraph','text_zh':'这个旧例子为何相加？'},*context)
    answer=document({'id':'answer-p','type':'paragraph','text_zh':'这里保留用户收到的原回答。'},
        {'id':'answer-math','type':'formula','latex':'2+2=4'},
        {'id':'answer-table','type':'table','headers':['输入','输出'],'rows':[['2+2','4']]})
    data={'schema_version':3,'content_contract_version':'3.1','id':'retired-note-fixture','content_version':1,
        'title_cn':'主动批注兼容样板','level':'beginner','learning_catalog':{'concepts':[],'aids':[aid]},
        'overview':{'sections':[{'id':'overview','title_cn':'从具体问题讲起','blocks':[
            {'id':'body','type':'paragraph','text_zh':body,'learning':{'id':'narrative','presentation':'narrative','plain_summary':'例子',
                'blocks':[{'id':'intro','type':'paragraph','text_zh':'两份相同的数据怎样相加？先代入2，再看数量改变后的结果。'},
                    {'id':'aid-inline','type':'learning_aid','aid_id':'example'}]}},
            {'id':'paper-formula','type':'formula','latex':'x+y=z','text_zh':'把两份数量相加。'},
            {'id':'paper-result','type':'table','headers':['原实验','结果'],'rows':[['条件甲','60%']]}]}]},
        'explanation':{'sections':[{'id':'explanation','title_cn':'过程','blocks':[{'id':'ex-body','type':'paragraph','text_zh':'先读取，再解释这里的缓存。'}]}]},
        'sections':[{'id':'source','title_cn':'原文','blocks':[{'id':'source-body','type':'paragraph','text_en':'Read the same data again.','text_zh':'再次读取同一份数据。'}]}],
        'default_annotations':[{'id':'old-auto','view':'overview','content_id':'body','field':'text_zh','exact':'缓存','title':'不应显示的预注标题','content':document({'id':'auto','type':'paragraph','text_zh':'AUTO_NOTE_MUST_NOT_RENDER'})},
            {'id':'stale','view':'unknown','content_id':'deleted','field':'retired','exact':'missing','content':{'version':99,'blocks':[{'type':'unknown','script':'window.preloadedExecuted=true'}]}}]}
    annotations=[
        {'id':'user-1','status':'answered','revision':1,'user_note':'用户主动问题一','selected_text':'缓存','anchor':copy.deepcopy(anchor),
            'answer':'原回答二','history':[{'user_note':'用户主动问题一','answer':'原回答一','status':'answered'},
                {'user_note':'用户继续问题二','answer':'原回答二','answer_content':answer,'status':'answered'}]},
        {'id':'user-from-old-note','status':'answered','revision':1,'user_note':'这个旧例子为何相加？','selected_text':'缓存','anchor':copy.deepcopy(anchor),
            'note_content':question,'answer':'保留的回答','answer_content':answer,'history':[{'user_note':'这个旧例子为何相加？','note_content':question,'answer':'保留的回答','answer_content':answer,'status':'answered'}]}]
    draft={'id':'draft-from-old-note','status':'draft','revision':0,'local':True,'user_note':'','selected_text':'缓存','anchor':copy.deepcopy(anchor),
        'note_content':document(*context),'editor_extra_blocks':copy.deepcopy(context),
        'study_state':{'version':1,'components':{'default-note/old/steps':{'step':1}}},'history':[]}
    return data,annotations,draft


def main():
    data,annotations,draft=fixture()
    errors,requests,checks=[],[],[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            if name=='paper-data.js':
                source=('window.PAPER_DATA='+json.dumps(data,ensure_ascii=False)+';').encode('utf-8')
                media='application/javascript'
            elif name=='assets/legacy-context.svg':
                source=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 45"><text x="10" y="28">Saved user context</text></svg>'
                media='image/svg+xml'
            else:
                path=(ASSETS/name).resolve()
                if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():
                    self.send_error(404);return
                source=path.read_bytes();media=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',media+'; charset=utf-8');self.end_headers();self.wfile.write(source)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def open_reader(browser, records, saved_draft=None):
        context=browser.new_context(viewport={'width':1360,'height':1000},color_scheme='dark')
        initial='window.EventSource=undefined;'
        if saved_draft is not None:
            initial+='localStorage.setItem('+json.dumps('arm_v3_'+data['id']+'_drafts')+','+json.dumps(json.dumps([saved_draft],ensure_ascii=False))+');'
        context.add_init_script(initial)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        def api(route):
            request=route.request;requests.append({'method':request.method,'url':request.url,'data':request.post_data})
            if request.method!='GET':
                route.fulfill(status=200,json={'ok':True,'drafts':[]});return
            if '/api/session?' in request.url:
                result={'paper_id':data['id'],'token':'fixture','service_instance_id':'fixture'}
            elif '/api/status?' in request.url:
                result={'bridge_available':False}
            else:
                result={'annotations':records}
            route.fulfill(json=result)
        page.route('**/api/**',api)
        page.route('https://**',lambda route:route.abort())
        page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
        page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
        return page
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=open_reader(browser,annotations,draft)
            original=page.evaluate('JSON.stringify(PAPER_DATA)')
            assert page.evaluate('ReaderV3.allAnnotations().map(a=>a.id)')==['user-1','user-from-old-note','draft-from-old-note']
            assert page.locator('#annotation-count').inner_text()=='3'
            assert page.locator('.default-term,[data-default-annotation-id],.default-note-content,.status-prewritten').count()==0
            assert page.locator('#paper-content').get_by_role('button',name='查看批注：缓存').count()==0
            assert page.evaluate('ReaderV3.newDefaultFollowup===undefined&&ReaderV3.defaultAnnotations===undefined')
            assert 'AUTO_NOTE_MUST_NOT_RENDER' not in page.locator('body').inner_text()
            assert page.evaluate('window.preloadedExecuted===undefined')
            checks.append('legacy preloaded field is never rendered, counted, wrapped or exposed as a prefill API; only two user questions and their saved draft remain')
            assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.allAnnotations()[0]).status')=='located'
            field=page.locator('[data-content-id="body"][data-field="text_zh"]')
            assert field.evaluate('(n)=>ReaderV3.canonicalIndex(n).text')==data['overview']['sections'][0]['blocks'][0]['text_zh']
            page.wait_for_function('document.querySelectorAll(".annotation-marker").length===3')
            page.evaluate('ReaderV3.openList()')
            assert page.locator('.annotation-list-item').count()==3
            assert page.locator('.list-item-head').all_text_contents()[0].startswith('#1')
            page.get_by_role('button',name='待回答',exact=True).click()
            assert page.locator('.annotation-list-item').count()==1
            page.get_by_role('button',name='全部',exact=True).click()
            assert '不应显示的预注标题' not in page.locator('#panel-body').inner_text()
            page.evaluate('ReaderV3.openAnnotation("user-1")')
            assert page.locator('.answer-turn').count()==2
            assert '原回答一' in page.locator('.answer-history').inner_text() and '这里保留用户收到的原回答。' in page.locator('.answer-history').inner_text()
            assert page.evaluate('ReaderV3.allAnnotations()[0].history')==annotations[0]['history']
            assert page.locator('.answer-history .math-atom[data-math-state="rendered"]').count()==1
            assert page.locator('.answer-history .data-table').count()==1
            page.get_by_role('button',name='定位原文',exact=True).click()
            checks.append('user numbering, canonical selection/locator, normal markers, pending filter and two-turn rich answer history remain intact')
            page.evaluate('ReaderV3.openAnnotation("user-from-old-note")')
            assert page.locator('#annotation-note').input_value()=='这个旧例子为何相加？'
            assert page.locator('.turn-question .figure-image').count()==1
            assert page.locator('.turn-question .math-atom[data-math-state="rendered"]').count()==1
            assert page.locator('.asset-placeholder').count()==0
            assert page.locator('#annotation-followup').is_visible()
            page.locator('#annotation-followup').fill('围绕保留回答继续追问')
            assert '基于此说明追问' not in page.locator('#panel-body').inner_text()
            page.evaluate('ReaderV3.openAnnotation("draft-from-old-note")')
            assert page.locator('#annotation-note').input_value()==''
            page.locator('#annotation-note').fill('原草稿现在补写的用户问题')
            saved=page.evaluate('ReaderV3.getState().drafts.find(d=>d.id==="draft-from-old-note")')
            assert saved['study_state']['components']['default-note/old/steps']=={'step':1}
            assert all(b in saved['note_content']['blocks'] for b in draft['editor_extra_blocks'])
            assert saved['note_content']['blocks'][0]['text']=='原草稿现在补写的用户问题'
            checks.append('previous user followups retain copied quotation, math, confined image, answer and editable followup; old unsent rich draft remains editable without losing source blocks or state')
            page.locator('#panel-close').click()
            page.locator('#plain-language-toggle').click()
            assert page.locator('#paper-content .ai-supplement:visible').count()==0
            assert page.locator('[data-content-id="paper-formula"].block-frame .math-atom[data-math-state="rendered"]').is_visible()
            assert page.locator('[data-content-id="paper-result"].block-frame').is_visible()
            assert page.evaluate('ReaderV3.locateAnnotation(ReaderV3.allAnnotations()[0]).status')=='located'
            page.locator('#plain-language-toggle').click()
            assert page.locator('#paper-content [data-aid-id="example"]').is_visible()
            page.evaluate('ReaderV3.switchView("translation")')
            assert page.locator('.default-term,.ai-supplement').count()==0
            page.evaluate('ReaderV3.switchView("overview")')
            assert page.evaluate('JSON.stringify(PAPER_DATA)')==original
            assert page.evaluate('getComputedStyle(document.body).backgroundColor')=='rgb(255, 255, 255)'
            checks.append('AI examples and original formulas/results still work; plain/view switches preserve active user notes and the established light appearance')
            page.locator('.paper-width').screenshot(path=str(OUTPUT/'retired-preloaded-notes-desktop.png'))
            data['default_annotations']={'retired':'unknown structure','html':'<script>window.preloadedExecuted=true</script>','content':{'version':99,'blocks':[{'type':'retired'}]}}
            empty=open_reader(browser,[])
            assert empty.locator('#annotation-count').inner_text()=='0'
            assert empty.evaluate('ReaderV3.allAnnotations().length')==0
            assert empty.locator('.annotation-marker,.default-term').count()==0
            empty.evaluate('ReaderV3.openList()')
            assert empty.locator('.annotation-list-item').count()==0
            assert '选中正文' in empty.locator('.empty-list').inner_text()
            assert empty.evaluate('window.preloadedExecuted===undefined')
            checks.append('unknown retired metadata remains readable with zero annotations and no execution; active user action is required to begin a note')
            empty.locator('#panel-close').click()
            user_anchor=copy.deepcopy(draft['anchor'])
            empty.evaluate('(a)=>ReaderV3.newDraft(a,"缓存")',user_anchor)
            empty.locator('#annotation-note').fill('这是我主动填写的问题')
            assert empty.evaluate('ReaderV3.allAnnotations().length')==1
            assert empty.locator('#annotation-count').inner_text()=='1'
            question_posts=[r for r in requests if r['method']=='POST' and ('/annotations' in r['url'] or '/bridge/' in r['url'])]
            assert question_posts==[] and errors==[],(question_posts,errors)
            checks.append('a fresh user-created draft works normally; zero question/model submissions and zero page errors')
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    report={'ok':True,'checks':checks,'question_posts':[],'page_errors':errors,
        'mutations':[r['url'] for r in requests if r['method']!='GET']}
    (OUTPUT/'retired-preloaded-notes-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
