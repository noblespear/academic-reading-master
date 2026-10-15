"""Explicit main-body aids are independent of the optional AI examples layer."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from content_contract import validate_learning_content, validate_rich_document
from test_narrative_contract import paper, chart, graph


def body_block(aid=None):
    return {'id':'body-aid','type':'learning_aid','placement':'body',
        'evidence_kind':'paper','source_refs':[{'page':5,'label':'Table1'}],'aid':aid or chart()}


class BodyAidTests(unittest.TestCase):
    def assert_invalid(self,data):
        errors,_=validate_learning_content(data)
        self.assertTrue(any('placement' in e for e in errors),errors)
        self.assert_schema(data,False)

    def assert_schema(self,data,valid=True):
        try:
            from jsonschema import Draft202012Validator
            from referencing import Registry, Resource
        except ImportError:
            return  # Semantic validation remains standard-library only.
        schemas={p.name:json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'schemas').glob('*.json')}
        registry=Registry().with_resources((name,Resource.from_contents(s)) for name,s in schemas.items())
        errors=list(Draft202012Validator(schemas['paper-data-3.1.schema.json'],registry=registry).iter_errors(data))
        self.assertEqual(not errors,valid,[e.message for e in errors])

    def test_body_chart_and_graph_are_allowed_in_both_explanation_views(self):
        for view in ('overview','explanation'):
            for aid in (chart(),graph()):
                with self.subTest(view=view,aid=aid['type']):
                    data=paper();data[view]['sections'][0]['blocks'].append(body_block(aid))
                    self.assertEqual(validate_learning_content(data),([],[]))
                    self.assert_schema(data)

    def test_catalog_reference_and_legacy_unspecified_placement(self):
        data=paper();aid=graph();data['learning_catalog']['aids'].append(aid)
        b=body_block();b.pop('aid');b['aid_id']=aid['id']
        data['overview']['sections'][0]['blocks'].append(b)
        self.assertEqual(validate_learning_content(data),([],[]));self.assert_schema(data)
        b.pop('placement')
        self.assertEqual(validate_learning_content(data),([],[]));self.assert_schema(data)

    def test_only_body_value_on_aids_is_allowed(self):
        for value in ('ai','BODY',None,False):
            with self.subTest(value=value):
                data=paper();b=body_block();b['placement']=value
                data['overview']['sections'][0]['blocks'].append(b);self.assert_invalid(data)
        data=paper();data['overview']['sections'][0]['blocks'][0]['placement']='body';self.assert_invalid(data)

    def test_nested_ai_blocks_cannot_escape_parent(self):
        for wrapper in (lambda b:b,lambda b:{'id':'nested-details','type':'details','summary':'解释','blocks':[b]},
                        lambda b:{'id':'nested-quote','type':'quote','blocks':[b]}):
            data=paper();data['overview']['sections'][0]['blocks'][0]['learning']['blocks'].append(wrapper(body_block()))
            self.assert_invalid(data)

    def test_nested_body_containers_and_fallbacks_cannot_escape(self):
        data=paper();data['overview']['sections'][0]['blocks'].append({'id':'top-details','type':'details','evidence_kind':'paper','summary':'过程','blocks':[body_block()]})
        # Nested containers are checked even without a learning field.
        self.assert_invalid(data)
        data=paper();b=body_block();b['aid']['fallback']['blocks'].append(body_block(graph()))
        data['overview']['sections'][0]['blocks'].append(b);self.assert_invalid(data)

    def test_translation_and_user_rich_documents_do_not_allow_placement(self):
        data=paper();data['sections'][0]['blocks'].append(body_block());self.assert_invalid(data)
        errors,_=validate_rich_document({'version':1,'blocks':[body_block()]})
        self.assertTrue(any('placement' in e for e in errors),errors)

    def test_live_body_aid_is_still_validated_for_safety_and_references(self):
        for mutate,expected in ((lambda b:b['aid']['config'].update(script='never execute'),'executable'),
                                (lambda b:b['aid']['config']['observations'][0].update(value=101),'[0,100]'),
                                (lambda b:b.update(aid_id='missing'),'exactly one')):
            data=paper();b=body_block();mutate(b);data['overview']['sections'][0]['blocks'].append(b)
            self.assertTrue(any(expected in e for e in validate_learning_content(data)[0]))


if __name__=='__main__':unittest.main()
