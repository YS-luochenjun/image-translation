import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from image_localizer import fireplay
from image_localizer.fireplay_download import _inspect
from image_localizer.fireplay_reuse import search_history


class HistoricalReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code = 'TEST-20261126-001'
        self.original = self.register_source(self.code, 'red')

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def register_source(self, code, color):
        original = self.root / 'state/fireplay' / code / 'sources/original.png'
        original.parent.mkdir(parents=True)
        Image.new('RGB', (300, 250), color).save(original)
        identifier = str(uuid4())
        source = {'index': 1, 'id': 'original', 'url': 'https://example.invalid/original.png'}
        snapshot = {'content': {'assetType': 'button', 'code': code, 'id': identifier},
                    'parsed': {'sources': [source]}}
        version = fireplay._hash(snapshot)
        self.write(original.parent.parent / 'snapshots' / f'{version}.json', snapshot)
        self.write(original.parent.parent / 'request.json', {
            'code': code, 'requirement_id': identifier, 'content_version': version, 'sources': [source],
            'targets': ['ja'], 'downloads': {'1': {
                **_inspect(original), 'local_path': str(original), 'content_version': version,
            }},
        })
        return original

    def test_local_requires_same_source_and_confirmed_historical_material(self):
        previous = 'TEST-20261125-002'
        historical_original = self.register_source(previous, 'red')
        row = json.loads(historical_original.parent.parent.joinpath('request.json').read_bytes())
        folder = self.root / 'work/fireplay-production' / previous
        self.write(folder / 'delivery-state.json', {
            'content_version': row['content_version'], 'requirement': {'asset_type': 'button'}, 'size_spec': 'sizes.json',
        })
        self.write(folder / 'sizes.json', {'sources': [{'id': 'orig', 'path': str(historical_original)}]})
        confirmed = {'state': 'confirmed', 'code': previous, 'requirement_id': row['requirement_id'],
                     'content_version': row['content_version'], 'verified_material_ids': ['historical']}
        self.write(historical_original.parent.parent / 'status-finish.json', confirmed)
        deliveries = [{'source_id': 'orig', 'language': 'ja', 'member_id': '300x250',
                       'material_id': 'historical', 'resource_id': 'exact'}]
        report = {'requirement_id': row['requirement_id'], 'code': previous, 'complete': True,
                  'publication_unknown': [], 'verified_deliveries': deliveries}
        with patch('image_localizer.fireplay_reuse.next_delivery', return_value=report):
            self.assertEqual(['exact'], [item['resource_id'] for item in self.search()['local_candidates']])
            report['publication_unknown'] = ['unconfirmed']
            self.assertEqual([], self.search()['local_candidates'])
            report['publication_unknown'] = []
            Image.new('RGB', (300, 250), 'blue').save(historical_original)
            self.assertEqual([], self.search()['local_candidates'])

    def test_vector_scores_do_not_authorize_hash_mismatches(self):
        source = self.original.read_bytes()
        sha = hashlib.sha256(source).hexdigest()
        md5 = hashlib.md5(source).hexdigest()
        hits = [{'id': item, 'matchedResourceId': f'{item}-bytes', 'canView': True,
                 'matchedResourceUrl': f'https://example.invalid/{item}.png', 'similarityScore': score}
                for item, score in (('changed', 0.999), ('exact', 0.91), ('different-size', 0.95))]
        spec = {'source_sha256': sha, 'image_search': {'items': hits}}
        planned = self.search(spec)['fireplay']['resource_lookups']
        self.assertEqual(['changed', 'exact', 'different-size'], planned[0]['arguments']['material_ids'])
        spec['resources'] = [{'items': [{
            'material_id': hit['id'], 'complete': True, 'query_status': 'ready',
            'images': [{'resource_id': hit['matchedResourceId'], 'status': 'normal',
                        'url': hit['matchedResourceUrl'], 'md5': md5,
                        'width': 301 if hit['id'] == 'different-size' else 300, 'height': 250}],
        } for hit in hits]}]
        calls = []

        def transfer(url, sink, *, max_bytes):
            calls.append(url)
            sink(source if '/exact.' in url else bytes(reversed(source)))
            self.assertEqual(len(source), max_bytes)
            return len(source)

        with patch('image_localizer.fireplay_reuse.transfer_public_bytes', side_effect=transfer):
            result = self.search(spec)['fireplay']
        self.assertEqual(['exact'], result['verified_source_material_ids'])
        self.assertEqual(['exact'], result['requirement_lookup']['arguments']['material_ids'])
        self.assertEqual(2, len(calls))
        spec['source_sha256'] = 'unrelated'
        with self.assertRaisesRegex(ValueError, 'different original'):
            self.search(spec)

    def search(self, receipt=None):
        return search_history(self.root, self.code, 1, receipt)
