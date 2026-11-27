import assert from 'node:assert/strict';
import test from 'node:test';
import { supplementReuse } from './fireplay-reuse-search.mjs';

const plan = { code: 'CURRENT', mode: 'normal', remote: {
  source_sha256: 'a'.repeat(64),
  image_lookup: { arguments: { image_url: 'https://example.invalid/original.png', limit: 5 } }
} };

test('historical search stops before the panel if source bytes cannot be verified', async () => {
  const calls = [];
  const result = await supplementReuse(plan, {
    invoke: async (name, args) => {
      calls.push(name);
      if (name === 'material_search_by_image') {
        return { items: [{ id: 'material', canView: true, matchedResourceId: 'image' }] };
      }
      assert.equal(name, 'material_resources_query');
      assert.deepEqual(args.material_ids, ['material']);
      return { items: [] };
    },
    verify: async evidence => {
      assert.equal(evidence.source_sha256, plan.remote.source_sha256);
      return { unverified_resource_ids: ['image'] };
    }
  });
  assert.deepEqual(calls, ['material_search_by_image', 'material_resources_query']);
  assert.equal(result.state, 'no_verified_original_in_sample');
});

test('only a current confirmed source relation becomes a Fireplay history lead', async () => {
  const invoke = async name => {
    if (name === 'material_search_by_image') {
      return { items: [{ id: 'original', canView: true, matchedResourceId: 'image' }] };
    }
    if (name === 'material_resources_query') return { items: [] };
    assert.equal(name, 'requirement_list');
    return {
      view: 'summary',
      query_meta: { filters_applied: { material_ids: ['original'] } },
      next_cursor: 'next',
      items: [
        { id: 'old', code: 'OLD', status: 'done', matched_relations: ['source'] },
        { id: 'reference', code: 'REF', status: 'done', matched_relations: ['reference'] },
        { id: 'rejected', code: 'REJECTED', status: 'partially_rejected', matched_relations: ['source'] },
        { id: 'same', code: 'CURRENT', status: 'done', matched_relations: ['source'] }
      ]
    };
  };
  const result = await supplementReuse(plan, {
    invoke,
    verify: async () => ({
      verified_source_material_ids: ['original'],
      requirement_lookup: { arguments: { material_ids: ['original'] } }
    })
  });
  assert.equal(result.state, 'searched');
  assert.equal(result.partial, true);
  assert.deepEqual(result.delivery_lookup.arguments.requirement_ids, ['old']);
});

test('rework skips all supplemental tool calls', async () => {
  const result = await supplementReuse({ ...plan, mode: 'rework' }, {
    invoke: async () => assert.fail('Rework must not call the supplemental search')
  });
  assert.deepEqual(result, { state: 'not_needed' });
});
