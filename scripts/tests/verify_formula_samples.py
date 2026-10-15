"""Render the real AgentDropout teaching configurations using isolated APIs."""
import json
import mimetypes
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

SKILL=Path(__file__).resolve().parents[2]
ROOT=SKILL.parents[1]
ASSETS=SKILL/'assets'
PAPER=ROOT/'PaperVault/papers/2025_agentdropout'
OUTPUT=SKILL.parent/'validation'/'continuous-teaching-3.6'


def main():
    errors=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            base=PAPER if name=='paper-data.js' or name.startswith('assets/') else ASSETS
            path=(base/name).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',(mimetypes.guess_type(path.name)[0] or 'application/octet-stream')+'; charset=utf-8');self.end_headers();self.wfile.write(path.read_bytes())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report={'ok':True,'scope':'actual AgentDropout data with current UI, isolated browser/API; user SQLite untouched','cases':{}}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.add_init_script('window.EventSource=undefined;')
            page.on('pageerror',lambda e:errors.append(str(e)))
            def api(route):
                req=route.request
                if '/api/session?' in req.url:result={'paper_id':'2025_agentdropout','token':'fixture'}
                elif '/api/status?' in req.url:result={'bridge_available':False}
                else:result={'annotations':[]}
                route.fulfill(status=200,json=result)
            page.route('**/api/**',api);page.route('https://**',lambda route:route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3')
            page.evaluate('ReaderV3.switchView("explanation");ReaderV3.setPlainLanguage(true)')
            for name,value,expected in [('expectation','0.75',0.7),('nuclear','0.5',0.5),('amortization','200',10000)]:
                component=page.locator('[data-aid-id="ad-aid-ct36-'+name+'"]').first
                assert component.is_visible()
                component.get_by_role('slider').first.fill(value)
                point=component.locator('.formula-current-point')
                actual=float(point.get_attribute('data-current-y'))
                assert abs(actual-expected)<1e-8,(name,actual,expected)
                component.scroll_into_view_if_needed()
                component.screenshot(path=str(OUTPUT/f'sample-formula-{name}-desktop.png'))
                report['cases'][name]={'point_x':float(point.get_attribute('data-current-x')),'point_y':actual,'line_segments':component.locator('.formula-curve-svg').get_attribute('data-curve-segments')}
            page.set_viewport_size({'width':320,'height':800})
            for name in report['cases']:
                component=page.locator('[data-aid-id="ad-aid-ct36-'+name+'"]').first
                component.scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                overflow=page.locator('#reading-scroll').evaluate('(n)=>({width:n.clientWidth,scroll:n.scrollWidth})')
                if overflow['scroll']>overflow['width']:
                    overflow['elements']=page.locator('.paper-width').evaluate('n=>Array.from(n.querySelectorAll("*")).filter(e=>e.getBoundingClientRect().right>innerWidth+1&&!e.closest(".formula-display,.table-scroll")).map(e=>({tag:e.tagName,cls:e.className,content:e.closest("[data-content-id]")?.dataset.contentId,right:e.getBoundingClientRect().right})).slice(0,18)')
                assert overflow['scroll']<=overflow['width'],overflow
                assert component.locator('.formula-curve-svg').evaluate('(s)=>Math.abs(s.width.baseVal.value-s.getBoundingClientRect().width)<1')
                component.screenshot(path=str(OUTPUT/f'sample-formula-{name}-mobile320.png'))
                report['cases'][name]['mobile320_no_overflow']=True
            assert not errors,errors
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    report['page_errors']=errors
    (OUTPUT/'sample-formula-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
