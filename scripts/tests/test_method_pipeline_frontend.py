"""Real browser, isolated synthetic map; no paper edits or model calls."""
import copy
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright
from test_body_learning_aids_frontend import fixture
from test_method_pipeline import example

SKILL = Path(__file__).resolve().parents[2]
ASSETS = SKILL / 'assets'
OUTPUT = SKILL.parent / 'validation' / 'v3.9.5' / 'pipeline'


def edge_point(component, edge_id, fraction=0.5):
    return component.locator('.method-pipeline-edge-hit[data-edge-id="'+edge_id+'"]').evaluate('''(path, fraction) => {
        const point=path.getPointAtLength(path.getTotalLength()*fraction);
        const screen=new DOMPoint(point.x,point.y).matrixTransform(path.getScreenCTM());
        return {x:screen.x,y:screen.y};
    }''', fraction)


def shared_edge_point(component, edge_id, other_id, tolerance=1.5):
    return component.evaluate('''(root, args) => {
        const pathFor=id => root.querySelector('.method-pipeline-edge-hit[data-edge-id="'+id+'"]');
        const sample=path => {
            const length=path.getTotalLength(), count=Math.max(80, Math.ceil(length/2));
            return Array.from({length:count+1},(_,i)=>{
                const fraction=i/count, point=path.getPointAtLength(length*fraction);
                const screen=new DOMPoint(point.x,point.y).matrixTransform(path.getScreenCTM());
                return {x:screen.x,y:screen.y,fraction};
            });
        };
        const primary=sample(pathFor(args.edgeId)),other=sample(pathFor(args.otherId));
        for(const point of primary){
            if(point.fraction<.02||point.fraction>.98)continue;
            const distance=Math.min(...other.map(candidate=>Math.hypot(point.x-candidate.x,point.y-candidate.y)));
            if(distance<=args.tolerance)return {x:point.x,y:point.y,distance};
        }
        throw new Error('shared edge segment was not found: '+args.edgeId+' / '+args.otherId);
    }''', {'edgeId':edge_id,'otherId':other_id,'tolerance':tolerance})


def clear_edge_point(component, edge_id, blocked_ids, minimum_distance=10):
    return component.evaluate('''(root, args) => {
        const pathFor=id => root.querySelector('.method-pipeline-edge-hit[data-edge-id="'+id+'"]');
        const sample=path => {
            const length=path.getTotalLength(), count=Math.max(80, Math.ceil(length/2));
            return Array.from({length:count+1},(_,i)=>{
                const fraction=i/count, point=path.getPointAtLength(length*fraction);
                const screen=new DOMPoint(point.x,point.y).matrixTransform(path.getScreenCTM());
                return {x:screen.x,y:screen.y,fraction};
            });
        };
        const primary=sample(pathFor(args.edgeId));
        const blocked=args.blocked.map(id=>sample(pathFor(id)));
        for(const point of primary){
            if(point.fraction<.02||point.fraction>.98)continue;
            const distances=blocked.flatMap(points=>points.map(candidate=>Math.hypot(point.x-candidate.x,point.y-candidate.y)));
            const distance=distances.length?Math.min(...distances):Infinity;
            if(distance>args.minimumDistance)return {x:point.x,y:point.y,distance};
        }
        throw new Error('independent edge segment was not found: '+args.edgeId);
    }''', {'edgeId':edge_id,'blocked':blocked_ids,'minimumDistance':minimum_distance})


def assert_no_collinear_overlaps(component):
    """Every rendered edge must own its positive-length orthogonal strokes."""
    overlaps = component.evaluate('''root => {
        const segmentList = path => {
            const commands = path.getAttribute('d').match(/[ML]\\s*-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)\\s+-?(?:\\d+(?:\\.\\d*)?|\\.\\d+)/g) || [];
            const points = commands.map(command => command.slice(1).trim().split(/\\s+/).map(Number));
            const segments = [];
            for(let i=1;i<points.length;i++){
                const [x1,y1] = points[i-1], [x2,y2] = points[i];
                if(x1===x2 && y1===y2) continue;
                segments.push({edge:path.dataset.edgeId,axis:x1===x2?'v':'h',coordinate:x1===x2?x1:y1,
                    start:x1===x2?Math.min(y1,y2):Math.min(x1,x2),end:x1===x2?Math.max(y1,y2):Math.max(x1,x2)});
            }
            return segments;
        };
        const all = [...root.querySelectorAll('.method-pipeline-edge')].flatMap(segmentList), result=[];
        for(let i=0;i<all.length;i++) for(let j=i+1;j<all.length;j++){
            const a=all[i],b=all[j];
            if(a.axis!==b.axis || Math.abs(a.coordinate-b.coordinate)>6) continue;
            const length=Math.min(a.end,b.end)-Math.max(a.start,b.start);
            if(length>12) result.push({first:a,second:b,length,gap:Math.abs(a.coordinate-b.coordinate)});
        }
        return result;
    }''')
    assert overlaps == [], overlaps


def main():
    data, annotations, _ = fixture()
    data['id'] = 'method-pipeline-browser-fixture'
    map_block = copy.deepcopy(example()['overview']['sections'][0]['blocks'][1])
    data['overview']['sections'][0]['blocks'].insert(1, map_block)
    errors, posts, checks = [], [], []
    corrupt = [False]
    wide = [False]
    shared = [False]
    chara_vertical = [False]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split('?')[0].lstrip('/') or 'reader-v3.html'
            if name == 'paper-data.js':
                served = copy.deepcopy(data)
                if corrupt[0]:
                    served['overview']['sections'][0]['blocks'][1]['aid']['config']['nodes'][0]['row'] = -1
                if wide[0]:
                    served['overview']['sections'][0]['blocks'][1]['aid']['config']['nodes'][3]['column'] = 3
                if shared[0]:
                    config = served['overview']['sections'][0]['blocks'][1]['aid']['config']
                    positions = {
                        'input': (0, 0), 'prepare': (1, 0), 'contour': (2, 0),
                        'texture': (4, 0), 'merge': (3, 1), 'check': (4, 1),
                    }
                    for node in config['nodes']:
                        node['column'], node['row'] = positions[node['id']]
                    config['edges'] = [
                        {'id':'shared-short','from':'prepare','to':'contour','kind':'branch','label':'短分支共享端口','flow':'共享端口后的短支路。','cause':'回归测试覆盖短边优先命中。'},
                        {'id':'shared-long','from':'prepare','to':'texture','kind':'branch','label':'长分支共享端口','flow':'共享端口后的长支路。','cause':'回归测试确认长边尾段仍可点。'},
                        {'id':'join-left','from':'contour','to':'merge','kind':'flow','label':'轮廓汇入','flow':'轮廓表示进入汇合阶段。','cause':'并行支路在汇合节点会合。'},
                        {'id':'join-right','from':'texture','to':'merge','kind':'flow','label':'纹理汇入','flow':'纹理表示进入汇合阶段。','cause':'并行支路在汇合节点会合。'},
                        {'id':'merge-check','from':'merge','to':'check','kind':'flow','label':'提交检查','flow':'汇合表示交给质量检查。','cause':'检查对象必须是本轮合并结果。'},
                        {'id':'check-prepare','from':'check','to':'prepare','kind':'feedback','label':'反馈校正','flow':'检查失败时返回校正阶段。','cause':'需要调整时返回亮度校正。'},
                        {'id':'input-texture','from':'input','to':'texture','kind':'dependency','label':'参考依赖','flow':'原始输入提供纹理参考。','cause':'这是依赖关系，不表示先后执行。'},
                        {'id':'input-prepare','from':'input','to':'prepare','kind':'flow','label':'进入校正','flow':'原始输入交给校正阶段。','cause':'校正需要原始图像。'},
                    ]
                    config['reading_order'] = ['input','prepare','contour','texture','merge','check']
                if chara_vertical[0]:
                    config = served['overview']['sections'][0]['blocks'][1]['aid']['config']
                    positions = {
                        'conditions': (0, 0), 'identity-cache': (1, 0),
                        'relations': (2, 0), 'frame-sampling': (3, 0),
                        'point-tracking': (4, 0), 'adaptive-merge': (5, 0),
                        'images': (4, 1),
                    }
                    config['nodes'] = [
                        {'id':id, 'label':label, 'summary':'Chara 阶段的可读摘要。',
                         'inputs':'前一阶段传入的方法信息。', 'outputs':'交给下一阶段的中间结果。',
                         'column':column, 'row':row}
                        for id,label in (
                            ('conditions','生成条件'), ('identity-cache','身份图缓存'),
                            ('relations','身份关系'), ('frame-sampling','帧序列采样'),
                            ('point-tracking','点追踪注意力'), ('adaptive-merge','自适应合并'),
                            ('images','生成帧图像'))
                        for column,row in [positions[id]]
                    ]
                    def edge(id, source, target, kind):
                        return {'id':id, 'from':source, 'to':target, 'kind':kind,
                                'label':'Chara 方法关系', 'flow':'中间结果沿有向关系传递。',
                                'cause':'该关系用于保持方法阶段的输入输出语义。'}
                    config['edges'] = [
                        edge('id-merge','identity-cache','adaptive-merge','flow'),
                        edge('id-attn','identity-cache','point-tracking','flow'),
                        edge('merge-sampling','adaptive-merge','frame-sampling','feedback'),
                        edge('prompt-probe','conditions','relations','flow'),
                        edge('sampling-image','frame-sampling','images','flow'),
                        edge('id-relations','identity-cache','relations','flow'),
                        edge('attn-merge','point-tracking','adaptive-merge','flow'),
                        edge('sampler-attn','frame-sampling','point-tracking','flow'),
                        edge('prompt-id','conditions','identity-cache','flow'),
                        edge('relation-ready','relations','frame-sampling','flow'),
                    ]
                    config['reading_order'] = ['conditions','identity-cache','relations','frame-sampling','point-tracking','adaptive-merge','images']
                source = ('window.PAPER_DATA=' + json.dumps(served, ensure_ascii=False) + ';').encode('utf-8')
                media = 'application/javascript'
            elif name == 'assets/legacy-context.svg':
                source = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 45"><text x="10" y="28">Saved user context</text></svg>'
                media = 'image/svg+xml'
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

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width':1360, 'height':1000}, color_scheme='dark', has_touch=True)
            context.add_init_script('window.EventSource=undefined;')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))

            def api(route):
                request = route.request
                if request.method != 'GET':
                    posts.append({'method':request.method, 'url':request.url})
                    result = {'ok':True}
                elif '/api/session?' in request.url:
                    result = {'paper_id':data['id'], 'token':'fixture'}
                elif '/api/status?' in request.url:
                    result = {'bridge_available':False}
                else:
                    result = {'annotations':annotations}
                route.fulfill(status=200, json=result)

            page.route('**/api/**', api)
            page.route('https://**', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}/reader-v3.html')
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            body = page.locator('#paper-content [data-content-id="overview-method-framework"][data-learning-placement="body"]')
            component = body.locator('[data-aid-id="method-map"]')
            graph = component.locator('.method-pipeline-drawing')
            nodes = component.locator('.method-pipeline-node')
            paths = component.locator('.method-pipeline-edge')
            assert component.is_visible() and body.locator('.ai-supplement').count() == 0
            assert nodes.count() == 6 and paths.count() == 8
            assert component.get_attribute('data-selected-node-id') == ''
            assert component.get_attribute('data-layout-columns') == '2'
            assert component.locator('.method-pipeline-node.is-selected').count() == 0
            assert all(node.is_visible() for node in nodes.all())
            assert_no_collinear_overlaps(component)
            assert graph.bounding_box()['height'] < 430
            before_paths = paths.evaluate_all('(nodes) => nodes.map(n => n.getAttribute("d"))')
            before_description = component.locator('.method-pipeline-description').inner_text()
            page.locator('.annotation-layer').evaluate_all('(layers) => layers.forEach(layer => layer.style.pointerEvents="none")')
            feedback_edge_ids = paths.evaluate_all('(nodes) => nodes.map(n => n.dataset.edgeId)')
            for width in (1360,760,320):
                page.set_viewport_size({'width':width, 'height':1000})
                page.wait_for_function('document.querySelector(".method-pipeline-drawing").scrollWidth <= document.querySelector(".method-pipeline-drawing").clientWidth + 1')
                graph.scroll_into_view_if_needed()
                for edge_id in feedback_edge_ids:
                    point = clear_edge_point(component, edge_id, [other for other in feedback_edge_ids if other != edge_id], minimum_distance=24)
                    page.mouse.click(point['x'], point['y'])
                    assert component.get_attribute('data-selected-edge-id') == edge_id, (width,edge_id,point)
                    component.get_by_role('button', name='返回全貌', exact=True).click()
            checks.append('all eight directed feedback-fixture edges remain directly selectable at 1360/760/320px from an independently clear sampled segment; resetting each edge preserves the full graph')
            page.set_viewport_size({'width':1360, 'height':1000})
            graph.scroll_into_view_if_needed()
            point = edge_point(component, 'input-prepare')
            page.mouse.click(point['x'], point['y'])
            assert component.get_attribute('data-selected-edge-id') == 'input-prepare'
            assert component.get_attribute('data-selected-node-id') == ''
            assert component.locator('.method-pipeline-node.is-selected').count() == 0
            assert component.locator('.method-pipeline-node.is-related').count() == 2
            assert component.locator('.method-pipeline-edge.is-selected').count() == 1
            assert component.locator('.method-pipeline-edge.is-related').count() == 1
            assert component.locator('.method-pipeline-flow').inner_text().endswith(map_block['aid']['config']['edges'][0]['flow'])
            assert component.locator('.method-pipeline-cause').inner_text().endswith(map_block['aid']['config']['edges'][0]['cause'])
            body.scroll_into_view_if_needed()
            component.screenshot(path=str(OUTPUT/'method-pipeline-edge-desktop.png'))
            component.locator('.method-pipeline-relations summary').click()
            dependency = component.get_by_role('button', name='关系：读取图像 → 估计纹理', exact=True)
            dependency.focus()
            dependency.press('Space')
            assert component.get_attribute('data-selected-edge-id') == 'input-texture'
            assert component.get_attribute('data-selected-node-id') == ''
            assert dependency.get_attribute('aria-pressed') == 'true'
            assert component.locator('.method-pipeline-edge-dependency.is-selected').count() == 1
            assert '不是质量提升的实验因果结论' in component.locator('.method-pipeline-cause').inner_text()
            state_edge = page.evaluate('ReaderV3.getState().studyState')
            assert state_edge['paper/overview/overview-method-framework/method-map'] == {'selected_node_id':'','selected_edge_id':'input-texture'}
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert component.get_attribute('data-selected-edge-id') == 'input-texture'
            assert component.get_attribute('data-selected-node-id') == ''
            component.get_by_role('button', name='返回全貌', exact=True).click()
            assert component.get_attribute('data-selected-edge-id') == ''
            checks.append('direct graph-edge click selects exactly that directed edge and its endpoints, displays exact flow/cause; native relationship buttons support Space and distinguish dependency from causal evidence, with mutually exclusive state restored on refresh')
            component.get_by_role('button', name='提取轮廓', exact=True).click()
            assert component.get_attribute('data-selected-node-id') == 'contour'
            assert component.locator('.method-pipeline-node.is-selected').count() == 1
            assert component.locator('.method-pipeline-edge.is-related').count() == 2
            assert component.locator('.method-pipeline-description').inner_text() != before_description
            for text in ('概要', '输入', '输出', '形状边界', '校正后的图像'):
                assert text in component.locator('.method-pipeline-description').inner_text()
            node = map_block['aid']['config']['nodes'][2]
            for field in ('summary', 'inputs', 'outputs'):
                assert component.locator('.method-pipeline-'+field).inner_text().endswith(node[field])
            assert component.get_attribute('data-selected-edge-id') == ''
            assert paths.count() == 8 and nodes.count() == 6
            assert paths.evaluate_all('(nodes) => nodes.map(n => n.getAttribute("d"))') == before_paths
            component.get_by_role('button', name='检查质量', exact=True).click()
            assert component.get_attribute('data-selected-node-id') == 'check'
            assert component.locator('.method-pipeline-edge-feedback.is-related').count() == 1
            assert '需要调整时返回亮度校正' in component.locator('.method-pipeline-description').inner_text()
            checks.append('all six stages and eight typed directed edges visible initially; clicking a node clears edge selection, highlights its related paths, and displays exact summary/inputs/outputs without deleting the full map')

            texture = component.get_by_role('button', name='估计纹理', exact=True)
            texture.focus()
            texture.press('Space')
            assert component.get_attribute('data-selected-node-id') == 'texture'
            assert component.locator('.method-pipeline-edge-dependency.is-related').count() == 1
            texture.press('ArrowDown')
            assert page.evaluate('document.activeElement.dataset.nodeId') == 'merge'
            page.keyboard.press('Enter')
            assert component.get_attribute('data-selected-node-id') == 'merge'
            component.get_by_role('button', name='下一阶段', exact=True).click()
            assert component.get_attribute('data-selected-node-id') == 'check'
            component.get_by_role('button', name='上一阶段', exact=True).click()
            assert component.get_attribute('data-selected-node-id') == 'merge'
            component.get_by_role('button', name='返回全貌', exact=True).click()
            assert component.get_attribute('data-selected-node-id') == ''
            assert component.locator('.method-pipeline-edge.is-related').count() == 0
            assert component.locator('.method-pipeline-description').inner_text() == before_description
            component.get_by_role('button', name='估计纹理', exact=True).click()
            state = page.evaluate('ReaderV3.getState().studyState')
            assert state['paper/overview/overview-method-framework/method-map']['selected_node_id'] == 'texture'
            page.locator('#plain-language-toggle').click()
            assert component.is_visible() and page.locator('#paper-content .ai-supplement:visible').count() == 0
            assert page.evaluate('ReaderV3.getState().studyState') == state
            assert component.get_attribute('data-selected-node-id') == 'texture'
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert component.get_attribute('data-selected-node-id') == 'texture'
            assert component.is_visible()
            checks.append('native buttons support Space/Enter, arrow-key focus, previous/next reading navigation and return-to-full-view; selection persists on refresh and survives hiding AI supplements while body map stays visible')

            body.scroll_into_view_if_needed()
            component.screenshot(path=str(OUTPUT / 'method-pipeline-desktop.png'))
            desktop = {node.get_attribute('data-node-id'):node.bounding_box() for node in nodes.all()}
            page.set_viewport_size({'width':320, 'height':780})
            page.wait_for_function('document.querySelector(".method-pipeline-drawing").scrollWidth <= document.querySelector(".method-pipeline-drawing").clientWidth + 1')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert nodes.count() == 6 and paths.count() == 8
            assert component.get_attribute('data-selected-node-id') == 'texture'
            assert component.locator('[data-edge-id="check-prepare"]').first.get_attribute('data-from') == 'check'
            assert component.locator('[data-edge-id="check-prepare"]').first.get_attribute('data-to') == 'prepare'
            mobile = {node.get_attribute('data-node-id'):node.bounding_box() for node in nodes.all()}
            assert mobile['prepare']['y'] == mobile['input']['y'] and mobile['texture']['y'] == mobile['contour']['y']
            assert mobile['input']['width'] < desktop['input']['width']
            for node in nodes.all():
                box = node.bounding_box()
                assert box['width'] >= 88 and box['height'] >= 44
                assert node.evaluate('(n) => n.scrollHeight <= n.clientHeight && n.scrollWidth <= n.clientWidth')
            assert component.get_attribute('data-layout-mode') == 'explicit'
            assert component.get_attribute('data-scrollable') == 'false'
            component.get_by_role('button', name='检查质量', exact=True).tap()
            assert component.get_attribute('data-selected-node-id') == 'check'
            assert '反馈' in component.locator('.method-pipeline-description').inner_text()
            body.scroll_into_view_if_needed()
            component.locator('.method-pipeline-figure').screenshot(path=str(OUTPUT / 'method-pipeline-mobile320.png'))
            component.locator('.method-pipeline-description').scroll_into_view_if_needed()
            component.locator('.method-pipeline-description').screenshot(path=str(OUTPUT / 'method-pipeline-mobile-detail320.png'))
            checks.append('320px layout retains two-column branch topology at readable label size with feedback routed outside all unrelated nodes; selection and unclipped touch targets remain without page overflow')

            graph.scroll_into_view_if_needed()
            point = edge_point(component, 'merge-check')
            page.touchscreen.tap(point['x'], point['y'])
            assert component.get_attribute('data-selected-edge-id') == 'merge-check'
            assert component.get_attribute('data-selected-node-id') == ''
            assert component.locator('.method-pipeline-edge.is-selected').count() == 1
            assert '检查对象必须是本轮合并结果' in component.locator('.method-pipeline-cause').inner_text()
            component.locator('.method-pipeline-figure').screenshot(path=str(OUTPUT/'method-pipeline-edge-mobile320.png'))
            component.locator('.method-pipeline-description').scroll_into_view_if_needed()
            component.locator('.method-pipeline-description').screenshot(path=str(OUTPUT/'method-pipeline-edge-detail320.png'))
            checks.append('touch can select the graph edge directly at 320px; its exact directed flow and downstream reason replace node details, with a single highlighted edge and two endpoint nodes')

            page.set_viewport_size({'width':760, 'height':900})
            page.wait_for_function('document.querySelector(".method-pipeline-drawing").scrollWidth <= document.querySelector(".method-pipeline-drawing").clientWidth + 1')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert component.get_attribute('data-layout-mode') == 'explicit'
            assert component.get_attribute('data-scrollable') == 'false'
            assert component.locator('.method-pipeline-node').count() == 6 and component.locator('.method-pipeline-edge').count() == 8
            checks.append('760px layout keeps the explicit grid readable without an inner horizontal scroller')

            component.locator('.component-fallback summary').click()
            fallback = component.locator('.component-fallback').inner_text()
            for text in ('读取图像', '估计纹理', '检查质量', '反馈', '依赖'):
                assert text in fallback
            wide[0] = True
            page.set_viewport_size({'width':320, 'height':780})
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert component.get_attribute('data-layout-columns') == '4'
            assert component.get_attribute('data-layout-mode') == 'turned'
            assert component.get_attribute('data-visual-columns') == '3'
            assert component.get_attribute('data-scrollable') == 'false'
            assert graph.evaluate('(n) => n.scrollWidth <= n.clientWidth + 1')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            component.get_by_role('button', name='估计纹理', exact=True).focus()
            assert graph.evaluate('(n) => n.scrollLeft == 0')
            assert component.get_attribute('data-selected-edge-id') == 'merge-check'
            assert component.get_attribute('data-selected-node-id') == ''
            component.get_by_role('button', name='估计纹理', exact=True).press('Enter')
            assert component.get_attribute('data-selected-node-id') == 'texture'
            assert nodes.count() == 6 and paths.count() == 8
            checks.append('complex four-column maps retain author rows and columns while turning into a vertical readable layout; native keyboard focus keeps every stage reachable without overflowing the page or inventing serial links')
            component.get_by_role('button', name='返回全貌', exact=True).click()
            wide[0] = False
            shared[0] = True
            page.set_viewport_size({'width':1360, 'height':1000})
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert component.locator('.content-warning').count() == 0
            assert nodes.count() == 6 and paths.count() == 8
            assert component.get_attribute('data-layout-columns') == '5'
            assert component.get_attribute('data-layout-mode') == 'explicit'
            assert_no_collinear_overlaps(component)
            hit_lengths = component.locator('.method-pipeline-edge-hit').evaluate_all('''paths => paths.map(path => ({id:path.dataset.edgeId,length:path.getTotalLength()}))''')
            assert all(left['length'] + 0.01 >= right['length'] for left,right in zip(hit_lengths,hit_lengths[1:])), hit_lengths
            route_lengths = {item['id']:item['length'] for item in hit_lengths}
            assert route_lengths['shared-short'] < route_lengths['shared-long'], route_lengths
            shared_point = shared_edge_point(component, 'shared-short', 'shared-long')
            page.mouse.click(shared_point['x'], shared_point['y'])
            assert component.get_attribute('data-selected-edge-id') == 'shared-short'
            component.get_by_role('button', name='返回全貌', exact=True).click()
            long_tail = clear_edge_point(component, 'shared-long', ['shared-short'], minimum_distance=26)
            page.mouse.click(long_tail['x'], long_tail['y'])
            assert component.get_attribute('data-selected-edge-id') == 'shared-long'
            component.get_by_role('button', name='返回全貌', exact=True).click()
            for edge_id, other_id in (('join-left','join-right'), ('join-right','join-left')):
                point = clear_edge_point(component, edge_id, [other_id], minimum_distance=26)
                page.mouse.click(point['x'], point['y'])
                assert component.get_attribute('data-selected-edge-id') == edge_id
                component.get_by_role('button', name='返回全貌', exact=True).click()
            checks.append('shared-port regression gives the shorter branch the final transparent hit layer, preserves an independently clickable long-edge tail, and keeps both parallel merge edges independently selectable')
            shared[0] = False
            chara_vertical[0] = True
            page.set_viewport_size({'width':760, 'height':1000})
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert paths.count() == 10 and nodes.count() == 7
            for width in (760,320):
                page.set_viewport_size({'width':width, 'height':1000})
                page.wait_for_function('document.querySelector(".method-pipeline-drawing").scrollWidth <= document.querySelector(".method-pipeline-drawing").clientWidth + 1')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert_no_collinear_overlaps(component)
                geometry = component.locator('[data-edge-id="merge-sampling"].method-pipeline-edge').evaluate('''path => {
                    const numbers = path.getAttribute('d').match(/[ML]\\s*-?\\d+(?:\\.\\d+)?\\s+-?\\d+(?:\\.\\d+)?/g) || [];
                    const points = numbers.map(command => {
                        const values = command.slice(1).trim().split(/\\s+/).map(Number);
                        return {x:values[0],y:values[1]};
                    });
                    const seen = new Set(), repeated=[];
                    points.slice(1).forEach((point,index) => {
                        const a=points[index], key=[a,point].sort((left,right)=>left.x-right.x||left.y-right.y).map(item=>item.x+','+item.y).join('>');
                        if(seen.has(key))repeated.push(key); else seen.add(key);
                    });
                    return {points,repeated};
                }''')
                assert not geometry['repeated'], (width, geometry)
                svg_box = component.locator('.method-pipeline-svg').bounding_box()
                assert max(point['x'] for point in geometry['points']) > svg_box['width'] * .8, (width, geometry, svg_box)
                assert max(point['x'] for point in geometry['points']) <= svg_box['width'] - 8 + .1, (width, geometry, svg_box)
                point = edge_point(component, 'merge-sampling')
                page.mouse.click(point['x'], point['y'])
                assert component.get_attribute('data-selected-edge-id') == 'merge-sampling', (width, geometry, point)
                component.get_by_role('button', name='返回全貌', exact=True).click()
            checks.append('Chara-shaped vertical same-column feedback uses a side loop with no repeated line segment and remains directly selectable at 760/320px without canvas overflow')
            chara_vertical[0] = False
            corrupt[0] = True
            page.reload()
            page.wait_for_function('window.ReaderV3 && ReaderV3.getState().online')
            assert component.locator('.content-warning').is_visible()
            assert component.locator('.method-pipeline-drawing').count() == 0
            for text in ('读取图像', '合并表示', '检查质量', '依赖', '反馈'):
                assert text in component.inner_text()
            assert posts == [] and errors == [], (posts, errors)
            checks.append('complete textual fallback remains available; invalid runtime configuration shows readable fallback rather than executing content; no question/bridge/model submissions or browser errors')
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    report = {'ok':True, 'checks':checks, 'posts':posts, 'page_errors':errors,
        'visual_guidance_sha256':hashlib.sha256((SKILL/'references/visual_authoring_guide.md').read_bytes()).hexdigest()}
    (OUTPUT/'method-pipeline-qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
