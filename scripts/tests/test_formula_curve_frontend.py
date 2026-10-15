"""Isolated Chromium verification of AST-derived formula curves."""
import copy
import json
import math
import mimetypes
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from test_retired_preloaded_frontend import fixture

SKILL=Path(__file__).resolve().parents[2]
ASSETS=SKILL/'assets'
OUTPUT=SKILL.parent/'validation'/'continuous-teaching-3.6'


def parameter(pid,label,minimum,maximum,value,step=0.01):
    return {'id':pid,'label':label,'min':minimum,'max':maximum,'value':value,'step':step}


def aid(aid_id,params,expression,label='结果',unit=''):
    return {'id':aid_id,'type':'formula_explorer','version':1,'title':aid_id,
        'evidence_kind':'teaching_example','config':{'parameters':params,'expression':expression,
        'result_label':label,'unit':unit},'fallback':{'version':1,'blocks':[
        {'id':'static','type':'paragraph','text_zh':'教学公式的静态说明保留。'}]}}


def main():
    data,_,_=fixture()
    data['id']='formula-curve-fixture'
    expected=aid('expected',[parameter('p','高分图概率',0,1,0.5),parameter('score','高分图得分',0,1,0.8)],
        {'op':'add','args':[{'op':'multiply','args':[{'parameter':'p'},{'parameter':'score'}]},
        {'op':'multiply','args':[{'op':'subtract','args':[1,{'parameter':'p'}]},0.4]}]},'期望效用')
    cost=aid('cost',[parameter('n','后续题目数',0,300,100,1),parameter('saving','每题省下的token',0,200,100,1)],
        {'op':'subtract','args':[{'op':'multiply','args':[{'parameter':'n'},{'parameter':'saving'}]},10000]},'净节省','token')
    gap=aid('gap',[parameter('x','x',-1,1,0.5)],{'op':'divide','args':[1,{'op':'subtract','args':[{'parameter':'x'},0.137]}]})
    even=aid('even-gap',[parameter('x','x',-1,1,0.5)],{'op':'divide','args':[1,{'op':'power','args':[{'op':'subtract','args':[{'parameter':'x'},0.137]},2]}]})
    zero=aid('zero-denominator',[parameter('x','x',-1,1,0.5)],{'op':'divide','args':[{'parameter':'x'},0]})
    fallback_axis=aid('first-unused',[parameter('unused','未参与公式的量',0,10,2),parameter('a','实际可调量',0.1,1,0.8)],{'parameter':'a'},'核范数（本例秩始终为1）')
    constant=aid('constant',[parameter('unused','未参与公式的量',0,10,2)],7)
    aids=[expected,cost,gap,even,zero,fallback_axis,constant]
    data['overview']={'sections':[{'id':'math','title_cn':'数学关系','blocks':[
        {'id':'body-'+a['id'],'type':'learning_aid','placement':'body','evidence_kind':'teaching_example','aid':a}
        for a in aids]}]}
    errors=[]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            if name=='paper-data.js':
                source=('window.PAPER_DATA='+json.dumps(data,ensure_ascii=False)+';').encode();media='application/javascript'
            else:
                path=(ASSETS/name).resolve()
                if not path.is_relative_to(ASSETS.resolve()) or not path.is_file():self.send_error(404);return
                source=path.read_bytes();media=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',media+'; charset=utf-8');self.end_headers();self.wfile.write(source)

        def log_message(self,*args):pass

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1360,'height':1000})
            page.add_init_script('window.EventSource=undefined;')
            page.on('pageerror',lambda e:errors.append(str(e)))
            def api(route):
                req=route.request
                if '/api/session?' in req.url:result={'paper_id':data['id'],'token':'fixture'}
                elif '/api/status?' in req.url:result={'bridge_available':False}
                else:result={'annotations':[]}
                route.fulfill(status=200,json=result)
            page.route('**/api/**',api)
            page.route('https://**',lambda route:route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            def component(aid_id):return page.locator('[data-aid-id="'+aid_id+'"]')
            expected_graph=component('expected')
            expected_point=expected_graph.locator('.formula-current-point')
            assert math.isclose(float(expected_point.get_attribute('data-current-y')),0.6)
            original=expected_graph.locator('.formula-function-line').get_attribute('d')
            expected_graph.get_by_role('slider',name='高分图概率').fill('0.75')
            assert math.isclose(float(expected_point.get_attribute('data-current-y')),0.7)
            assert float(expected_point.get_attribute('data-current-x'))==0.75
            assert expected_graph.locator('.formula-function-line').get_attribute('d')==original
            expected_graph.get_by_role('slider',name='高分图得分').fill('0.6')
            assert math.isclose(float(expected_point.get_attribute('data-current-y')),0.55)
            assert expected_graph.locator('.formula-function-line').get_attribute('d')!=original
            assert '高分图得分=0.6固定在当前值' in expected_graph.locator('.formula-curve-note').inner_text()
            assert '不是测量数据' in expected_graph.locator('.formula-curve-note').inner_text()
            cost_graph=component('cost');cost_graph.get_by_role('slider',name='后续题目数').fill('50')
            assert float(cost_graph.locator('.formula-current-point').get_attribute('data-current-y'))==-5000
            cost_graph.get_by_role('slider',name='后续题目数').fill('100')
            assert float(cost_graph.locator('.formula-current-point').get_attribute('data-current-y'))==0
            cost_graph.get_by_role('slider',name='后续题目数').fill('200')
            assert float(cost_graph.locator('.formula-current-point').get_attribute('data-current-y'))==10000
            assert cost_graph.locator('.formula-zero-line').count()==1
            for aid_id in ['gap','even-gap']:
                plot=component(aid_id)
                assert plot.locator('.formula-curve-svg').get_attribute('data-curve-segments')=='2'
                assert plot.locator('.formula-function-line').get_attribute('d').count('M')==2
            zero_graph=component('zero-denominator')
            assert zero_graph.locator('.formula-curve-svg').count()==0
            assert '整个横轴范围都没有有限结果' in zero_graph.inner_text()
            assert component('first-unused').get_attribute('data-curve-parameter')=='a'
            assert component('constant').locator('.formula-curve-svg').count()==0
            assert '不绘制函数曲线' in component('constant').inner_text()
            expected_graph.scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT/'formula-curve-desktop.png'))
            page.reload();page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert float(component('expected').locator('.formula-current-point').get_attribute('data-current-x'))==0.75
            assert math.isclose(float(component('expected').locator('.formula-current-point').get_attribute('data-current-y')),0.55)
            page.set_viewport_size({'width':320,'height':800})
            component('first-unused').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#reading-scroll').evaluate('(n)=>n.scrollWidth<=n.clientWidth')
            plots=page.locator('.formula-curve-svg')
            assert plots.evaluate_all('(ns)=>ns.every(s=>Math.abs(s.width.baseVal.value-s.getBoundingClientRect().width)<1)')
            assert plots.locator('text').evaluate_all('(ns)=>ns.every(n=>parseFloat(getComputedStyle(n).fontSize)>=12)')
            assert plots.locator('path').evaluate_all('(ns)=>ns.every(n=>!/(NaN|Infinity)/.test(n.getAttribute("d")))')
            page.screenshot(path=str(OUTPUT/'formula-curve-mobile320.png'))
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    report={'ok':True,'checks':['current point changes with primary parameter; other parameters recompute the line',
        'expected value and break-even teaching numbers are correct',
        'ordinary and squared zero denominators split paths even when the singularity is not a sample',
        'entirely invalid range gives explanatory fallback; first actual parameter used; constant formula does not invent a graph',
        'state restores; 320px SVG redraws at real width with readable labels; no nonfinite paths'],
        'page_errors':errors,'scope':'synthetic AST configurations; no model calls or user SQLite'}
    (OUTPUT/'formula-curve-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
