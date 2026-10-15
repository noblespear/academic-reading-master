"""Generic method-map contract, registration and backward compatibility."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from content_contract import validate_learning_content, validate_rich_document, rich_to_text
from test_overview_framework import paper as old_paper, body_graph


def example():
    return json.loads((ROOT / 'assets/content-templates/method-pipeline-example.json').read_text(encoding='utf-8'))


def pipeline(data):
    return data['overview']['sections'][0]['blocks'][1]['aid']


def document(aid):
    return {'version': 1, 'blocks': [{'id': 'map', 'type': 'learning_aid', 'aid': aid}]}


class MethodPipelineTests(unittest.TestCase):
    def assert_bad(self, aid, message):
        errors, _ = validate_rich_document(document(aid))
        self.assertTrue(any(message in error for error in errors), errors)

    def test_complete_synthetic_example_and_cycles_are_valid_without_mutation(self):
        data = example()
        original = copy.deepcopy(data)
        self.assertEqual(validate_learning_content(data), ([], []))
        self.assertEqual(validate_rich_document(document(pipeline(data))), ([], []))
        self.assertEqual(data, original)
        config = pipeline(data)['config']
        self.assertEqual({edge['kind'] for edge in config['edges']}, {'flow', 'branch', 'feedback', 'dependency'})
        fallback = rich_to_text(document(pipeline(data)))
        for label in ('读取图像', '校正亮度', '提取轮廓', '估计纹理', '合并表示', '检查质量', '分支', '反馈', '依赖'):
            self.assertIn(label, fallback)

    def test_paper_method_requires_sources_and_retains_provenance(self):
        aid = pipeline(example())
        aid['evidence_kind'] = 'paper'
        self.assert_bad(aid, '.source_refs: paper results require explicit sources')
        aid['source_refs'] = [{'page': 3, 'label': 'Method, synthetic source reference test'}]
        self.assertEqual(validate_rich_document(document(aid)), ([], []))
        aid['evidence_kind'] = 'ai_explanation'
        self.assert_bad(aid, '.evidence_kind')

    def test_stage_shape_bounds_and_required_explanations(self):
        cases = [
            (lambda c: c.update(nodes=[]), '.nodes: requires 2..40'),
            (lambda c: c.update(nodes=[c['nodes'][0]]), '.nodes: requires 2..40'),
            (lambda c: c['nodes'][0].update(row=True), '.row: integer grid position'),
            (lambda c: c['nodes'][0].update(column=40), '.column: integer grid position'),
            (lambda c: c['nodes'][0].update(row=-1), '.row: integer grid position'),
            (lambda c: c['nodes'][0].update(label='x'*81), '.label: stage text exceeds'),
            (lambda c: c['nodes'][0].update(summary=' '), '.summary: must be non-empty'),
            (lambda c: c['nodes'][0].pop('inputs'), '.inputs: must be non-empty'),
            (lambda c: c['nodes'][0].update(outputs=''), '.outputs: must be non-empty'),
            (lambda c: c['nodes'][0].update(inputs={}), '.inputs: must be non-empty'),
            (lambda c: c['nodes'][0].update(outputs='x'*4001), '.outputs: stage text exceeds'),
            (lambda c: c['nodes'][0].update(role=None), '.role: must be non-empty'),
            (lambda c: c['nodes'][0].update(unknown='x'), 'unsupported stage field'),
            (lambda c: c['nodes'][1].update(id=c['nodes'][0]['id']), 'duplicate ID'),
            (lambda c: c['nodes'][1].update(column=0, row=0), 'share a grid position'),
            (lambda c: c.update(summary=''), '.summary: must be non-empty'),
            (lambda c: c.update(summary='x'*4001), '.summary: method summary exceeds'),
            (lambda c: c.update(script='never execute'), 'executable/HTML field'),
            (lambda c: c.update(custom='unsupported'), 'unsupported method_pipeline field'),
        ]
        for mutate, error in cases:
            with self.subTest(error=error):
                aid = pipeline(example())
                mutate(aid['config'])
                self.assert_bad(aid, error)

    def test_relationship_shape_direction_and_duplicate_pairs(self):
        cases = [
            (lambda c: c.update(edges=[]), '.edges: requires 1..120'),
            (lambda c: c['edges'][0].update(to='missing'), '.to: unknown stage'),
            (lambda c: c['edges'][0].update(to='input'), 'no self relationship'),
            (lambda c: c['edges'][0].update(kind='communication'), '.kind: flow, branch'),
            (lambda c: c['edges'][0].update(kind=[]), '.kind: flow, branch'),
            (lambda c: c['edges'][0].update(label='x'*241), '.label: relationship text exceeds'),
            (lambda c: c['edges'][0].pop('label'), '.label: must be non-empty'),
            (lambda c: c['edges'][0].pop('flow'), '.flow: must be non-empty'),
            (lambda c: c['edges'][0].update(cause=' '), '.cause: must be non-empty'),
            (lambda c: c['edges'][0].update(cause={}), '.cause: must be non-empty'),
            (lambda c: c['edges'][0].update(flow='x'*4001), '.flow: relationship explanation exceeds'),
            (lambda c: c['edges'][0].update(from_node='input'), 'unsupported relationship field'),
            (lambda c: c['edges'].append(dict(c['edges'][0], id='duplicate-pair')), 'repeated directed pair'),
            (lambda c: c['edges'].append(copy.deepcopy(c['edges'][0])), 'duplicate ID'),
        ]
        for mutate, error in cases:
            with self.subTest(error=error):
                aid = pipeline(example())
                mutate(aid['config'])
                self.assert_bad(aid, error)
        aid = pipeline(example())
        aid['config']['edges'].append({'id':'reverse','from':'prepare','to':'input','kind':'feedback','label':'有向反向边与正向边是不同关系','flow':'输入条件不完整时请求补充拍摄条件。','cause':'合成测试中，校正需要条件完整时才继续。'})
        self.assertEqual(validate_rich_document(document(aid)), ([], []))

    def test_optional_roles_cannot_substitute_summary_input_output_or_edge_explanations(self):
        aid = pipeline(example())
        node = aid['config']['nodes'][0]
        node.update(role='处理输入', why_needed='后续需要', handoff='交给后续')
        self.assertEqual(validate_rich_document(document(aid)), ([], []))
        for field in ('summary', 'inputs', 'outputs'):
            broken = copy.deepcopy(aid)
            del broken['config']['nodes'][0][field]
            self.assert_bad(broken, '.'+field+': must be non-empty')
        for field in ('flow', 'cause'):
            broken = copy.deepcopy(aid)
            del broken['config']['edges'][0][field]
            self.assert_bad(broken, '.'+field+': must be non-empty')

    def test_reading_order_is_an_optional_complete_permutation(self):
        for value, message in ((None, 'must be an array'), (['input'], 'every stage'),
                (['input']*6, 'duplicate ID'), (['missing']*6, 'unknown reference'), (['input', {}], 'invalid stable ID')):
            aid = pipeline(example())
            aid['config']['reading_order'] = value
            self.assert_bad(aid, message)
        aid = pipeline(example())
        del aid['config']['reading_order']
        self.assertEqual(validate_rich_document(document(aid)), ([], []))

    def test_new_registration_rejects_static_old_graph_and_hidden_placement(self):
        for block in (None, body_graph()):
            data = old_paper(block)
            data['learning_requirements']['overview_framework']['kind'] = 'interactive_pipeline'
            errors, _ = validate_learning_content(data, ROOT/'assets/content-templates')
            self.assertTrue(any('method_pipeline' in e for e in errors), errors)
        data = example()
        data['overview']['sections'][0]['blocks'][1]['placement'] = 'supplement'
        errors, _ = validate_learning_content(data)
        self.assertTrue(any('requires placement body' in e for e in errors), errors)
        data = example()
        data['learning_requirements']['overview_framework']['kind'] = 'static'
        errors, _ = validate_learning_content(data)
        self.assertTrue(any('.kind: only interactive_pipeline' in e for e in errors), errors)

    def test_catalog_reference_and_existing_registrations_remain_valid(self):
        data = example()
        block = data['overview']['sections'][0]['blocks'][1]
        aid = block.pop('aid')
        data['learning_catalog']['aids'].append(aid)
        block['aid_id'] = aid['id']
        self.assertEqual(validate_learning_content(data), ([], []))
        for old in (old_paper(), old_paper(body_graph())):
            self.assertEqual(validate_learning_content(old, ROOT/'assets/content-templates'), ([], []))

    def test_json_schema_matches_component_shape_and_paper_evidence(self):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest('optional jsonschema is not installed')
        schemas = {p.name:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'schemas').glob('*.json')}
        registry = Registry().with_resources((name, Resource.from_contents(s)) for name, s in schemas.items())
        validator = Draft202012Validator(schemas['paper-data-3.1.schema.json'], registry=registry)
        data = example()
        self.assertEqual(list(validator.iter_errors(data)), [])
        pipeline(data).update(evidence_kind='paper', source_refs=[{'page': 3, 'label': 'Synthetic source test'}])
        self.assertEqual(list(validator.iter_errors(data)), [])
        mutations = [lambda a: a['config']['nodes'][0].update(row=True),
            lambda a: a['config']['edges'][0].update(kind='other'),
            lambda a: a['config'].update(extra='x'),
            lambda a: a['config']['nodes'][0].pop('summary'),
            lambda a: a['config']['nodes'][0].pop('inputs'),
            lambda a: a['config']['nodes'][0].update(outputs=''),
            lambda a: a['config']['edges'][0].pop('flow'),
            lambda a: a['config']['edges'][0].update(cause=''),
            lambda a: a['config'].update(nodes=[])]
        for mutate in mutations:
            broken = copy.deepcopy(data)
            mutate(pipeline(broken))
            self.assertTrue(list(validator.iter_errors(broken)))


if __name__ == '__main__':
    unittest.main()
