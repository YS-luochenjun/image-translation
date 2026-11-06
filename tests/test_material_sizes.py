import io
import json
from contextlib import redirect_stderr, redirect_stdout
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from image_localizer.material_sizes import plan_sizes, validate_sizes, POLICY
from image_localizer.cli import main


class MaterialSizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        Image.new('RGB', (600, 500), 'white').save(self.root / 'source.png')
        self.spec = {'asset_type': 'button', 'sources': [{'id': 'one', 'path': 'source.png', 'languages': ['en', 'ja']}]}

    def save(self):
        path = self.root / 'spec.json'
        path.write_text(json.dumps(self.spec), encoding='utf-8')
        return path

    def seven_scope(self):
        members = [{'member_id': f'member-{i}', 'resource_id': f'resource-{i}', 'width': w, 'height': h}
                   for i, (w, h) in enumerate([(1200, 628)] + [(1200, 1200)] * 3 + [(960, 1200)] * 3)]
        (self.root / 'receipt.json').write_text(json.dumps({'resources': members}), encoding='utf-8')
        self.spec.update(asset_type='image', delivery_mode='reference_seven_set',
                         scope_basis='User confirms this requirement follows the evidenced 064 seven-member set',
                         reference_members=members, reference_evidence_path='receipt.json')
        return members

    def write_outputs(self, plan):
        self.spec['outputs'] = []
        for index, item in enumerate(plan['items']):
            name = f'output-{index}.png'
            Image.new('RGB', (item['width'], item['height']), (index % 256, 100, 100)).save(self.root / name)
            self.spec['outputs'].append({'source_id': item['source_id'], 'language': item['language'],
                                         'member_id': item['member_id'], 'path': name, 'adaptation': 'recompose'})

    def test_machine_policy_has_exact_23_presets_and_no_image_default(self):
        policy = json.loads(POLICY.read_text(encoding='utf-8'))
        self.assertEqual(len(policy['types']['button']['whitelist']), 23)
        self.assertIn([240, 400], policy['types']['button']['whitelist'])
        self.assertEqual(policy['types']['button']['max_bytes'], 153600)
        self.assertIsNone(policy['types']['image']['defaults'])
        self.assertEqual(policy['types']['image']['required_roles'], [])

    def test_default_matrix_once_per_language_and_source_shape(self):
        result = plan_sizes(self.save())
        self.assertEqual(result['expected_resource_count'], 4)
        self.assertEqual({(i['width'], i['height']) for i in result['items']}, {(300, 250), (336, 280)})
        Image.new('RGB', (600, 620), 'white').save(self.root / 'source.png')
        self.assertEqual(plan_sizes(self.save())['expected_resource_count'], 4)
        Image.new('RGB', (400, 1000), 'white').save(self.root / 'source.png')
        result = plan_sizes(self.save())
        self.assertEqual(result['expected_resource_count'], 2)
        self.assertEqual(result['items'][0]['height'], 600)

    def test_explicit_240400_and_accepted_336280_preserved(self):
        self.spec.update(explicit_sizes=[[240, 400]], explicit_specification='User specified SHE-059 240x400')
        self.assertEqual(plan_sizes(self.save())['items'][0]['width'], 240)
        self.spec['explicit_sizes'] = [[336, 280]]
        self.assertEqual(plan_sizes(self.save())['expected_resource_count'], 2)
        self.spec['explicit_sizes'] = [[241, 400]]
        with self.assertRaisesRegex(ValueError, 'whitelist'):
            plan_sizes(self.save())

    def test_priority_explicit_over_same_group_reference(self):
        (self.root / 'receipt.json').write_text('{}')
        self.spec['historical_reference'] = {'requirement_id': 'old', 'material_id': 'material', 'evidence_path': 'receipt.json', 'sizes': [[336, 280]], 'same_group_same_type': True}
        self.assertEqual(plan_sizes(self.save())['items'][0]['authority'], 'same_group_same_type_delivered_reference')
        self.spec.update(explicit_sizes=[[240, 400]], explicit_specification='Explicit current demand')
        self.assertEqual(plan_sizes(self.save())['items'][0]['authority'], 'explicit_requirement')

    def test_tk_square_fb_two_and_video_unsupported(self):
        self.spec['asset_type'] = 'tk_carousel'
        self.assertEqual(plan_sizes(self.save())['expected_resource_count'], 2)
        self.spec['asset_type'] = 'fb_image'
        self.assertEqual(plan_sizes(self.save())['expected_resource_count'], 4)
        self.spec['asset_type'] = 'video'
        self.assertEqual(plan_sizes(self.save())['status'], 'unsupported')

    def test_image_unresolved_scope_and_role_only_history_fail_closed(self):
        self.spec['asset_type'] = 'image'
        for historical in (None, {'sizes': [[1200, 628], [1200, 1200]]},
                           {'sizes': [[1200, 628], [1200, 1200], [960, 1200]]}):
            if historical is not None:
                self.spec['historical_reference'] = historical
            with self.assertRaisesRegex(ValueError, 'scope_unresolved'):
                plan_sizes(self.save())
            for command in ('material-size-plan', 'material-size-validate'):
                with redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(main([command, str(self.save())]), 1)
                self.assertIn('scope_unresolved', error.getvalue())

    def test_provided_five_sources_six_languages_forms_six_complete_groups(self):
        sizes = [(500, 628), (500, 628), (1200, 628), (1200, 1200), (1200, 1200)]
        self.spec.update(asset_type='image', delivery_mode='provided_source_set',
                         scope_basis='User supplies the complete five-image set; deliver six translated groups')
        self.spec['sources'] = []
        for index, size in enumerate(sizes):
            path = f'source-{index}.png'
            Image.new('RGB', size, 'white').save(self.root / path)
            self.spec['sources'].append({'id': f'source-{index}', 'path': path,
                                         'languages': ['en', 'de', 'fr', 'es', 'ja', 'ko']})
        result = plan_sizes(self.save())
        self.assertEqual(result['expected_resource_count'], 30)
        for language in self.spec['sources'][0]['languages']:
            group = [item for item in result['items'] if item['language'] == language]
            self.assertEqual({item['source_id'] for item in group}, {source['id'] for source in self.spec['sources']})
            self.assertEqual(len(group), 5)
        for source, size in zip(self.spec['sources'], sizes):
            items = [i for i in result['items'] if i['source_id'] == source['id']]
            self.assertEqual(len(items), 6)
            self.assertEqual({(i['width'], i['height']) for i in items}, {size})
        self.write_outputs(result)
        self.assertEqual(validate_sizes(self.save())['status'], 'pass')
        # A duplicate cannot compensate for a missing source/language member,
        # even when the number of output records still equals thirty.
        omitted = self.spec['outputs'][-1]
        self.spec['outputs'][-1] = dict(self.spec['outputs'][0])
        invalid = validate_sizes(self.save())
        self.assertEqual(len(self.spec['outputs']), 30)
        self.assertEqual(invalid['status'], 'fail')
        self.assertTrue(any(item['source_id'] == omitted['source_id'] and item['language'] == omitted['language']
                            for item in invalid['missing']))
        self.assertTrue(any(item['reason'] == 'duplicate matrix item' for item in invalid['errors']))

    def test_064_seven_members_four_languages_is_twenty_eight(self):
        self.seven_scope()
        self.spec['sources'][0]['languages'] = ['en', 'de', 'jp', 'ko']
        result = plan_sizes(self.save())
        self.assertEqual(result['expected_resource_count'], 28)
        for language in self.spec['sources'][0]['languages']:
            items = [i for i in result['items'] if i['language'] == language]
            self.assertEqual(len({i['member_id'] for i in items}), 7)
            self.assertEqual(sum(i['width'] == i['height'] for i in items), 3)
        self.write_outputs(result)
        originals = {output['path']: (self.root / output['path']).read_bytes() for output in self.spec['outputs']}
        checked = validate_sizes(self.save())
        self.assertEqual(checked['expected_resource_count'], 28)
        self.assertEqual(checked['covered_resource_count'], 28)
        self.assertEqual(checked['status'], 'pass')
        self.assertTrue(all((self.root / path).read_bytes() == data for path, data in originals.items()))
        self.spec['outputs'].pop(2)
        missing = validate_sizes(self.save())
        self.assertEqual(missing['status'], 'fail')
        self.assertEqual(missing['covered_resource_count'], 27)
        self.assertEqual(len(missing['missing']), 1)
        self.assertEqual(missing['missing'][0]['member_id'], 'member-2')

    def test_same_size_members_require_identity_and_separate_files(self):
        self.seven_scope()
        self.spec['sources'][0]['languages'] = ['en', 'de']
        self.write_outputs(plan_sizes(self.save()))
        self.spec['outputs'][7]['path'] = self.spec['outputs'][0]['path']
        self.assertEqual(validate_sizes(self.save())['status'], 'pass')
        del self.spec['outputs'][1]['member_id']
        checked = validate_sizes(self.save())
        self.assertEqual(checked['status'], 'fail')
        self.assertIn('ambiguous', checked['errors'][0]['reason'])
        self.spec['outputs'][1]['member_id'] = 'member-1'
        self.spec['outputs'][2]['path'] = self.spec['outputs'][1]['path']
        checked = validate_sizes(self.save())
        self.assertEqual(checked['status'], 'fail')
        self.assertIn('separate output', checked['errors'][0]['reason'])

    def test_seven_set_needs_scope_basis_evidence_and_seven_unique_ids(self):
        members = self.seven_scope()
        self.spec['reference_members'] = members[:3]
        with self.assertRaisesRegex(ValueError, 'seven explicit members'):
            plan_sizes(self.save())
        self.spec['reference_members'] = members
        members[2]['member_id'] = members[1]['member_id']
        with self.assertRaisesRegex(ValueError, 'Duplicate reference'):
            plan_sizes(self.save())
        members[2]['member_id'] = 'member-2'
        self.spec['reference_evidence_path'] = 'absent.json'
        with self.assertRaisesRegex(ValueError, 'Missing seven-member'):
            plan_sizes(self.save())
        self.spec['reference_evidence_path'] = 'receipt.json'
        self.spec['scope_basis'] = ' '
        with self.assertRaisesRegex(ValueError, 'nonempty text'):
            plan_sizes(self.save())

    def test_explicit_any_pixels_record_basis_without_role_expansion(self):
        basis = 'User expressly requests only 350x700 and 500x500 for this requirement'
        self.spec.update(asset_type='image', explicit_sizes=[[350, 700], [500, 500]], explicit_specification=basis)
        result = plan_sizes(self.save())
        self.assertEqual(result['expected_resource_count'], 4)
        self.assertTrue(all(i['explicit_specification'] == basis for i in result['items']))
        self.assertEqual(result['items'][0]['official_size_check']['status'], 'conflict')
        self.write_outputs(result)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['material-size-validate', str(self.save())]), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'pass')
        self.spec['explicit_specification'] = ' '
        with self.assertRaisesRegex(ValueError, 'nonempty text'):
            plan_sizes(self.save())

    def test_explicit_scope_for_one_source_does_not_resolve_other_source(self):
        self.spec['asset_type'] = 'image'
        self.spec['sources'][0].update(explicit_sizes=[[1200, 1200]], explicit_specification='User: square only for this source')
        self.spec['sources'].append({'id': 'two', 'path': 'source.png', 'languages': ['en', 'ja']})
        with self.assertRaisesRegex(ValueError, 'scope_unresolved'):
            plan_sizes(self.save())

    def test_real_files_coverage_bytes_and_no_stretch(self):
        self.spec['sources'][0]['languages'] = ['en']
        self.write_outputs(plan_sizes(self.save()))
        result = validate_sizes(self.save())
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(len(result['outputs'][0]['sha256']), 64)
        self.spec['outputs'].pop()
        self.assertEqual(validate_sizes(self.save())['status'], 'fail')
        self.spec['outputs'][0]['adaptation'] = 'stretch'
        with self.assertRaisesRegex(ValueError, 'stretch'):
            validate_sizes(self.save())

    def test_byte_limit_real_files_and_cli_failure(self):
        self.spec.update(explicit_sizes=[[300, 250]], explicit_specification='Explicit fixture size')
        self.spec['sources'][0]['languages'] = ['en']
        path = self.root / 'output.png'
        Image.new('RGB', (300, 250), 'white').save(path)
        with path.open('ab') as stream:
            stream.write(b'padding' * 25000)
        self.spec['outputs'] = [{'source_id': 'one', 'language': 'en', 'path': 'output.png', 'adaptation': 'recompose'}]
        result = validate_sizes(self.save())
        self.assertEqual(result['status'], 'fail')
        self.assertIn('byte limit', result['errors'][0]['reason'])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['material-size-validate', str(self.save())]), 1)

    def test_unknown_fields_and_duplicate_languages(self):
        self.spec['quantity'] = 4
        with self.assertRaises(ValueError):
            plan_sizes(self.save())
        del self.spec['quantity']
        self.spec['sources'][0]['languages'] = ['en', 'en']
        with self.assertRaisesRegex(ValueError, 'unique'):
            plan_sizes(self.save())


if __name__ == '__main__':
    unittest.main()
