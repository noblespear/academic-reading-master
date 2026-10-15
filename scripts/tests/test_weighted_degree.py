"""One finite, declarative degree lesson; no model or production changes."""
import copy
import json
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from content_contract import validate_rich_document

def aid():
    # Keep this regression portable with a published skill package. The actual
    # authoring JSON is checked separately by validation/weighted-degree-browser.py.
    return {'id':'degree', 'type':'weighted_degree', 'version':1,
        'evidence_kind':'teaching_example', 'config':{
            'nodes':[{'id':name, 'label':name} for name in 'ABCD'],
            'edges':[{'id':a+'-'+b, 'from':a, 'to':b, 'weight':w}
                for a,b,w in [('A','B',.7), ('A','C',.2), ('A','D',.1), ('B','C',.5), ('C','D',.6)]],
            'adjustable_edge_id':'C-D', 'selected_node_id':'C',
            'initial_mode':'all', 'tie_break':'node_order'},
        'fallback':{'version':1, 'blocks':[
            {'id':'graph', 'type':'paragraph', 'text_zh':'A→B 0.7，A→C 0.2，A→D 0.1，B→C 0.5，C→D 0.6。'},
            {'id':'sum', 'type':'paragraph', 'text_zh':'A=1.0，B=1.2，C=1.3，D=0.7。把相邻入边与出边权重相加。'},
            {'id':'change', 'type':'paragraph', 'text_zh':'C→D改为0.9时A与D并列最低，按教学列序先移除A。改为1.0时A唯一最低。'},
            {'id':'scope', 'type':'paragraph', 'text_zh':'筛选后不重算本轮分数；这只是教学计算，不表示真实贡献、正确率或token。'}]}}


def document(value):
    return {'version': 1, 'blocks': [{'id': 'lesson', 'type': 'learning_aid', 'aid': value}]}


class WeightedDegreeContractTests(unittest.TestCase):
    def assert_error(self, value, text):
        errors, _ = validate_rich_document(document(value))
        self.assertTrue(any(text in error for error in errors), errors)

    def test_example_and_rich_fallback_validate_without_mutation(self):
        value = aid()
        original = copy.deepcopy(value)
        self.assertEqual(validate_rich_document(document(value)), ([], []))
        self.assertEqual(value, original)
        self.assertEqual(len(value['fallback']['blocks']), 4)

    def test_example_arithmetic_and_declared_tie_are_independently_recomputed(self):
        config = aid()['config']
        def sums(weight):
            return {node['id']: round(sum((weight if edge['id'] == 'C-D' else edge['weight'])
                for edge in config['edges'] if node['id'] in (edge['from'], edge['to'])), 8)
                for node in config['nodes']}
        self.assertEqual(sums(.6), {'A': 1., 'B': 1.2, 'C': 1.3, 'D': .7})
        self.assertEqual(sums(.9), {'A': 1., 'B': 1.2, 'C': 1.6, 'D': 1.})
        self.assertEqual(sums(1.), {'A': 1., 'B': 1.2, 'C': 1.7, 'D': 1.1})
        for weight, expected in ((.6, 'D'), (.9, 'A'), (1., 'A')):
            scores = sums(weight)
            self.assertEqual(min(config['nodes'], key=lambda node: scores[node['id']])['id'], expected)
        self.assertEqual(config['tie_break'], 'node_order')

    def test_graph_size_and_short_labels(self):
        for mutation, message in ((lambda c: c['nodes'].pop(), 'exactly four'),
                (lambda c: c['nodes'][0].update(label='过长节点名称需要读者横向移动' * 2), '12 characters'),
                (lambda c: c.update(edges=[]), '1..6'),
                (lambda c: c['edges'].extend(copy.deepcopy(c['edges'])), '1..6')):
            value = aid(); mutation(value['config']); self.assert_error(value, message)

    def test_invalid_references_self_edges_and_repeated_pairs(self):
        for mutation, message in ((lambda c: c['nodes'][1].update(id='A'), 'duplicate ID'),
                (lambda c: c['edges'][0].update(to='missing'), 'unknown node'),
                (lambda c: c['edges'][0].update(to='A'), 'self edge'),
                (lambda c: c['edges'][1].update({'from':'B', 'to':'A'}), 'repeated node pair'),
                (lambda c: c.update(adjustable_edge_id='missing'), 'adjustable_edge_id'),
                (lambda c: c.update(selected_node_id=[]), 'selected_node_id')):
            value = aid(); mutation(value['config']); self.assert_error(value, message)

    def test_weights_are_bounded_finite_numeric_tenths(self):
        for invalid in (True, '0.7', -.1, 1.1, float('inf'), float('nan'), .75):
            value = aid(); value['config']['edges'][0]['weight'] = invalid
            self.assert_error(value, '.weight')

    def test_rule_is_explicit_fixed_and_not_executable(self):
        for mutation, message in ((lambda c: c.pop('tie_break'), 'tie_break'),
                (lambda c: c.update(tie_break='random'), 'tie_break'),
                (lambda c: c.update(initial_mode=['all']), 'initial_mode'),
                (lambda c: c.update(expression='alert(1)'), 'unsupported'),
                (lambda c: c['nodes'][0].update(label='<script>alert(1)</script>'), 'raw HTML'),
                (lambda c: c['edges'][0].update(onclick='alert(1)'), 'executable'),
                (lambda c: c.update(script='alert(1)'), 'executable')):
            value = aid(); mutation(value['config']); self.assert_error(value, message)

    def test_missing_fallback_and_wrong_evidence_are_rejected(self):
        value = aid(); del value['fallback']; self.assert_error(value, '.fallback')
        value = aid(); value['evidence_kind'] = 'paper'; self.assert_error(value, 'teaching_example')

    def test_non_json_unknown_keys_report_errors_without_crashing(self):
        value = aid(); value['config'][7] = 'invalid'; self.assert_error(value, 'object keys')


if __name__ == '__main__':
    unittest.main()
