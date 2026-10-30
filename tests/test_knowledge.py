"""Knowledge relevance, provenance, and path-boundary regression checks."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from image_localizer.contracts import ContractError
from image_localizer.knowledge import load_knowledge
from image_localizer.pipeline import initialize, packet
from PIL import Image


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'knowledge').mkdir()
        (self.root/'knowledge/method.md').write_text('Conditional method',encoding='utf-8')
        (self.root/'knowledge/case.md').write_text('Unresolved feedback remains',encoding='utf-8')
        self.index={'version':1,'entries':[
            {'id':'method','path':'knowledge/method.md','kind':'method','status':'provisional','tags':[]},
            {'id':'case','path':'knowledge/case.md','kind':'case','status':'partial','tags':['ribbon'],'source_sha256':'a'*64},
        ]}
        self.save()

    def save(self):
        (self.root/'knowledge/index.json').write_text(json.dumps(self.index),encoding='utf-8')

    def test_unrelated_case_has_no_body(self):
        result=load_knowledge(self.root,source_sha256='b'*64)
        self.assertEqual(result['related_cases'],[])
        self.assertNotIn('content',result['available_cases'][0])
        self.assertEqual(result['methods'][0]['status'],'provisional')

    def test_source_match_keeps_partial_status(self):
        result=load_knowledge(self.root,source_sha256='a'*64)
        self.assertEqual(result['related_cases'][0]['status'],'partial')
        self.assertIn('Unresolved',result['related_cases'][0]['content'])

    def test_content_tags_select_related_case(self):
        result=load_knowledge(self.root,source_sha256='b'*64,tags=['ribbon'])
        self.assertEqual(len(result['related_cases']),1)

    def test_body_hash_tracks_actual_contents(self):
        result=load_knowledge(self.root,source_sha256='b'*64)
        self.assertEqual(result['methods'][0]['sha256'],hashlib.sha256(b'Conditional method').hexdigest())

    def test_path_cannot_escape_knowledge(self):
        self.index['entries'][0]['path']='../outside.md'
        self.save()
        with self.assertRaises(ContractError):
            load_knowledge(self.root,source_sha256='b'*64)

    def test_duplicate_id_rejected(self):
        self.index['entries'][1]['id']='method'
        self.save()
        with self.assertRaises(ContractError):
            load_knowledge(self.root,source_sha256='b'*64)

    def test_wrong_tag_shape_rejected(self):
        with self.assertRaises(ContractError):
            load_knowledge(self.root,source_sha256='b'*64,tags='ribbon')

    def test_actual_packet_includes_five_stage_guidance_without_approval(self):
        source=self.root/'input.png'
        Image.new('RGB',(40,30),'white').save(source)
        run=self.root/'run'
        initialize(source,'zh-CN',run)
        result=packet(run)
        self.assertEqual(len(result['workflow_stages']),5)
        self.assertGreaterEqual(len(result['knowledge']['methods']),3)
        self.assertFalse(result['manifest']['analysis']['complete'])
        self.assertNotIn('pass',result.get('status',''))


if __name__=='__main__':
    unittest.main()
