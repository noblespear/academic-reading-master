"""Narrative prose and visual contracts; retired preloaded metadata is ignored."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from content_contract import validate_learning_content, validate_rich_document, rich_to_text

def document(text='解释'):
    return {'version':1,'blocks':[{'id':'p','type':'paragraph','text_zh':text}]}

def paper():
    data = json.loads((ROOT/'assets/content-templates/minimal-valid-example.json').read_text(encoding='utf-8'))
    block = data['overview']['sections'][0]['blocks'][0]
    block['learning'] = {'id':'learn-capacity','presentation':'narrative','plain_summary':'摘要','blocks':document('想象桌上只有三个杯垫。每放一只杯子，就用掉一个位置；第四只杯子来时，先收走一只才有地方放。')['blocks']}
    data.pop('default_annotations', None)
    return data

def graph():
    return {'id':'graph','type':'graph_walkthrough','version':1,'evidence_kind':'teaching_example','fallback':document(),
      'config':{'nodes':[{'id':'a','label':'A'},{'id':'b','label':'B'}], 'edges':[{'id':'ab','from':'a','to':'b'}],
        'stages':[{'id':'both','title':'传话','text':'A把结果交给B','active_node_ids':['a','b'],'active_edge_ids':['ab']} ]}}

def chart():
    return {'id':'result','type':'comparison_chart','version':1,'evidence_kind':'paper','source_refs':[{'page':5,'label':'Table1'}],'fallback':document(),
      'config':{'metric_label':'准确率','unit':'%','models':[{'id':'m','label':'同一模型'}],'tasks':[{'id':'t','label':'同一任务','description':'回答问题','denominator':'测试题数'}],
      'series':[{'id':'a','label':'对照'},{'id':'b','label':'方法'}], 'observations':[{'model_id':'m','task_id':'t','series_id':'a','value':66.51},{'model_id':'m','task_id':'t','series_id':'b','value':68.7}]}}

class NarrativeTests(unittest.TestCase):
    def test_narrative_is_valid_without_preloaded_notes_or_per_term_coverage(self):
        data=paper()
        data['learning_requirements']['required_concept_ids']=[c['id'] for c in data['learning_catalog']['concepts']]
        self.assertNotIn('default_annotations', data)
        self.assertEqual(validate_learning_content(data), ([],[]))

    def test_stale_and_retired_preloaded_shapes_are_completely_ignored(self):
        legacy={'id':'old','concept_id':'missing','view':'retired','content_id':'deleted','field':'old.path','exact':'不存在','occurrence':True,'content':{'version':99,'blocks':[{'type':'unknown','src':'../outside.png','script':'must never execute'}]}}
        for metadata in ([legacy,copy.deepcopy(legacy)], {'retired':'shape','html':'<script>ignored()</script>'}, None, 'old format'):
            with self.subTest(metadata=metadata):
                data=paper(); data['default_annotations']=metadata
                original=copy.deepcopy(data)
                self.assertEqual(validate_learning_content(data), ([],[]))
                self.assertEqual(data, original)

    def test_ignored_metadata_does_not_relax_active_rich_document_safety(self):
        unsafe={'version':1,'blocks':[{'id':'x','type':'figure','src':'../outside.png'}]}
        self.assertTrue(any('confined' in e for e in validate_rich_document(unsafe)[0]))
        data=paper();data['overview']['sections'][0]['blocks'][0]['learning']['blocks']=unsafe['blocks']
        self.assertTrue(any('confined' in e for e in validate_learning_content(data)[0]))

    def test_json_schema_accepts_absent_or_unknown_retired_metadata(self):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest('optional jsonschema package is not installed')
        schemas={p.name:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'schemas').glob('*.json')}
        registry=Registry().with_resources((name, Resource.from_contents(schema)) for name,schema in schemas.items())
        validator=Draft202012Validator(schemas['paper-data-3.1.schema.json'],registry=registry)
        data=paper()
        self.assertEqual(list(validator.iter_errors(data)), [])
        for metadata in ([], {'retired':'unknown shape','content':{'version':99,'blocks':[{'type':'retired'}]}}, None, 'old format'):
            with self.subTest(metadata=metadata):
                data['default_annotations']=metadata
                self.assertEqual(list(validator.iter_errors(data)), [])

    def test_narrative_requires_visible_prose_not_only_metadata(self):
        data=paper();data['overview']['sections'][0]['blocks'][0]['learning'].pop('blocks')
        self.assertTrue(any('connected prose' in e for e in validate_learning_content(data)[0]))

    def test_graph_rejects_dangling_active_edges_and_bad_refs(self):
        aid=graph(); doc={'version':1,'blocks':[{'id':'g','type':'learning_aid','aid':aid}]}
        self.assertEqual(validate_rich_document(doc),([],[]))
        aid['config']['stages'][0]['active_node_ids']=['a']
        self.assertTrue(any('endpoints' in e for e in validate_rich_document(doc)[0]))
        aid['config']['edges'][0]['to']='missing'
        self.assertTrue(any('unknown node' in e for e in validate_rich_document(doc)[0]))

    def test_chart_requires_source_complete_matrix_and_percentage_range(self):
        aid=chart(); doc={'version':1,'blocks':[{'id':'c','type':'learning_aid','aid':aid}]}
        self.assertEqual(validate_rich_document(doc),([],[]))
        self.assertIn('[论文结果]',rich_to_text(doc))
        for mutate, expected in [(lambda a:a.pop('source_refs'),'explicit sources'),(lambda a:a['config']['observations'].pop(),'each model'),(lambda a:a['config']['observations'][0].update(value=101),'[0,100]')]:
            a=copy.deepcopy(aid);mutate(a)
            self.assertTrue(any(expected in e for e in validate_rich_document({'version':1,'blocks':[{'id':'c','type':'learning_aid','aid':a}]})[0]))

if __name__=='__main__':unittest.main()
