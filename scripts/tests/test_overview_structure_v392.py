"""Prevent merging a full modern paper overview without breaking draft/legacy use."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from content_contract import validate_learning_content
from init_reading_content import build_scaffold, PRESETS

TITLES = ['问题背景', '研究框架', '主要贡献', '论证与发现', '适用范围与局限']

def full_reader():
    root = ROOT / 'assets/content-templates'
    data = json.loads((root / 'body-first-valid-example.json').read_text(encoding='utf-8'))
    graph = json.loads((root / 'method-pipeline-example.json').read_text(encoding='utf-8'))
    method = copy.deepcopy(graph['overview']['sections'][0]['blocks'][1])
    method['id'] = 'complete-method-framework'
    method['evidence_kind'] = 'teaching_example'
    method['source_refs'] = []
    data['overview']['sections'][0]['title_cn'] = TITLES[0]
    for index in range(1, 5):
        blocks = [method] if index == 1 else [{'id': 'overview-slot-' + str(index), 'type': 'paragraph',
            'text_zh': '自拟测试中第' + str(index + 1) + '个信息位。', 'evidence_kind': 'teaching_example', 'source_refs': []}]
        data['overview']['sections'].append({'id': 'overview-section-' + str(index), 'title_cn': TITLES[index], 'blocks': blocks})
    data['learning_requirements']['overview_framework'] = {'section_id': 'overview-section-1',
        'block_id': method['id'], 'kind': 'interactive_pipeline'}
    return data

class OverviewStructureTests(unittest.TestCase):
    def test_all_presets_start_with_the_five_sections_in_order(self):
        for preset in PRESETS:
            data, todo = build_scaffold('paper', preset)
            self.assertEqual([s['title_cn'] for s in data['overview']['sections']], TITLES)
            self.assertEqual(data['overview']['sections'][1]['id'], data['learning_requirements']['overview_framework']['section_id'])

    def test_merging_sections_is_rejected_even_if_their_content_is_retained(self):
        data = full_reader()
        self.assertEqual(validate_learning_content(data)[0], [])
        sections = data['overview']['sections']
        sections[3]['blocks'].extend(sections.pop()['blocks'])
        errors, _ = validate_learning_content(data)
        self.assertTrue(any('五个独立概览' in error for error in errors), errors)

    def test_draft_legacy_and_isolated_examples_keep_their_scope(self):
        data = full_reader()
        data['overview']['sections'] = data['overview']['sections'][:3]
        self.assertEqual(validate_learning_content(data, preview=True)[0], [])
        for filename in ('method-pipeline-example.json', 'body-first-valid-example.json'):
            data = json.loads((ROOT / 'assets/content-templates' / filename).read_text(encoding='utf-8'))
            self.assertEqual(validate_learning_content(data)[0], [])

    def test_existing_engineering_titles_remain_valid_without_migration(self):
        data = full_reader()
        old_titles = ['问题背景', '方法框架', '创新点', '实验与效果', '开销与论文边界']
        for section, title in zip(data['overview']['sections'], old_titles):
            section['title_cn'] = title
        before = copy.deepcopy(data)
        self.assertEqual(validate_learning_content(data)[0], [])
        self.assertEqual(data, before)

    def test_method_section_is_second_in_a_completed_five_section_overview(self):
        data = full_reader()
        sections = data['overview']['sections']
        sections[1], sections[2] = sections[2], sections[1]
        errors, _ = validate_learning_content(data)
        self.assertTrue(any('研究框架必须位于' in error for error in errors), errors)

if __name__ == '__main__':
    unittest.main()
