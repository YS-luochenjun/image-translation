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

    def test_photo_case_preserves_scoped_acceptance_and_portable_evidence(self):
        root = Path(__file__).resolve().parents[1]
        case = root / 'knowledge/cases/she-032-20261109'
        feedback = json.loads((case / 'user-feedback.json').read_text(encoding='utf-8'))
        result = load_knowledge(root, source_sha256=feedback['source_sha256'])
        selected = next(item for item in result['related_cases'] if item['id'] == 'she-032-20261109')
        self.assertEqual('partial', selected['status'])
        self.assertEqual('partial', selected['user_feedback'])
        unrelated = load_knowledge(root, source_sha256='0'*64)
        self.assertNotIn('she-032-20261109', [item['id'] for item in unrelated['related_cases']])
        self.assertNotEqual(feedback['generated_sha256'], feedback['background_used_sha256'])
        evidence = json.loads((case / 'evidence-manifest.json').read_text(encoding='utf-8'))
        for item in evidence['files']:
            path = (case / item['path']).resolve()
            self.assertTrue(path.is_relative_to(case.resolve()))
            self.assertEqual(item['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            if path.suffix in ('.png', '.jpg'):
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual([image.width, image.height], item['size'])
        self.assertEqual(len(feedback['case_evidence']), 5)
        for name, digest in feedback['case_evidence'].items():
            self.assertEqual(digest, hashlib.sha256((case / 'evidence' / name).read_bytes()).hexdigest())

    def test_photo_delivery_snapshot_has_exact_matrix_and_equal_reviewed_download_hashes(self):
        path = Path(__file__).resolve().parents[1] / 'knowledge/cases/she-032-20261109/delivery-snapshot.json'
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        members = snapshot['members']
        languages = {'zh-TW','es','pt','fr','th','pl','tr','hi','ar','it','vi','ko','ja','nl'}
        expected = {('source-1', language, size) for language in languages for size in ('336x280','300x250')}
        self.assertEqual(len(members), len(expected))
        self.assertEqual({(m['source_id'],m['language'],m['member_id']) for m in members}, expected)
        for member in members:
            self.assertEqual(member['reviewed_sha256'], member['downloaded_sha256'])
            self.assertEqual(member['member_id'], f"{member['width']}x{member['height']}")
            self.assertLessEqual(member['bytes'], 153600)
        self.assertFalse(snapshot['live_platform_rechecked'])
        self.assertFalse(snapshot['final_outputs_user_accepted'])

    def test_wide_banner_case_evidence_is_portable_and_hash_bound(self):
        root = Path(__file__).resolve().parents[1]
        case = root / 'knowledge/cases/she-028-20261111'
        manifest = json.loads((case / 'evidence-manifest.json').read_text(encoding='utf-8'))
        for item in manifest['files']:
            path = (case / item['path']).resolve()
            self.assertTrue(path.is_relative_to(case.resolve()))
            self.assertEqual(item['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(item['bytes'], path.stat().st_size)
            if 'size' in item:
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(item['size'], list(image.size))
                    self.assertEqual(item['format'], image.format)
        source_hash = hashlib.sha256((case / 'evidence/source-3.png').read_bytes()).hexdigest()
        result = load_knowledge(root, source_sha256=source_hash)
        selected = next(item for item in result['related_cases'] if item['id'] == 'she-028-20261111')
        self.assertEqual('partial', selected['status'])

    def test_wide_banner_delivery_preserves_native_matrix_and_acceptance_boundary(self):
        case = Path(__file__).resolve().parents[1] / 'knowledge/cases/she-028-20261111'
        snapshot = json.loads((case / 'delivery-snapshot.json').read_text(encoding='utf-8'))
        expected = {(f'source-{source}', language, '970x250')
                    for source in (1, 2, 3)
                    for language in ('zh-TW', 'es', 'pt', 'de', 'fr', 'pl', 'tr', 'ko', 'ja', 'nl')}
        members = snapshot['members']
        self.assertEqual(30, len(members))
        self.assertEqual(expected, {(m['source_id'], m['language'], m['member_id']) for m in members})
        for member in members:
            self.assertEqual(member['reviewed_sha256'], member['downloaded_sha256'])
            self.assertEqual((970, 250, 'JPEG'), (member['width'], member['height'], member['format']))
            self.assertLessEqual(member['bytes'], 153600)
            self.assertEqual('pending', member['creative_risk_status'])
        current = next(m for m in members if (m['source_id'], m['language']) == ('source-3', 'fr'))
        final_hash = hashlib.sha256((case / 'evidence/source-3-fr-v2.jpg').read_bytes()).hexdigest()
        old_hash = hashlib.sha256((case / 'evidence/source-3-fr-v1-rejected.jpg').read_bytes()).hexdigest()
        self.assertEqual(current['reviewed_sha256'], final_hash)
        self.assertNotEqual(old_hash, final_hash)
        self.assertEqual('self', snapshot['agent_review_type'])
        self.assertFalse(snapshot['live_platform_rechecked'])
        self.assertFalse(snapshot['final_outputs_user_accepted'])


if __name__=='__main__':
    unittest.main()
