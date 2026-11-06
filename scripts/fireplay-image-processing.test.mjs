import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { processImage, main } from './fireplay-image-processing.mjs';

import { imageDefinitions as definitions, imageContractBytes as contractBytes, imageCatalogBytes as catalogBytes } from './fireplay-offline-test-contracts.mjs';
const id = '11111111-1111-4111-8111-111111111111';
const crop = { tool: 'generation_crop_create', arguments: { workspace_id: id, source_asset_ids: [id], targets: [{ specCode: 'square_1x1', prompt: 'Preserve subject' }], idempotency_key: 'offline-only' } };
const translate = { tool: 'generation_translate_create', arguments: { workspace_id: id, groups: [{ sourceAssetIds: [id] }], target_languages: ['fr'], prompt_template: 'Translate faithfully', idempotency_key: 'offline-only' } };
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'fireplay-crop-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, 'work/fireplay-readonly'), { recursive: true });
  await mkdir(join(root, 'work/production'), { recursive: true });
  await writeFile(join(root, 'work/fireplay-readonly/crop-mcp-contracts-20261104.json'), contractBytes);
  await writeFile(join(root, 'work/fireplay-readonly/crop-mcp-generation-20261104.json'), catalogBytes);
  const calls = [];
  const f = { root, receiptFile: 'work/production/receipt.jsonl', calls };
  f.transport = { call: async (name, args) => {
    calls.push({ name, args });
    if (name === 'tool_catalog') return { structuredContent: structuredClone(definitions.find(item => item.name === args.tool)) };
    // Assert a complete unknown receipt is on disk before every business dispatch.
    const entries = await journal(f);
    assert.equal(entries.length, 1);
    assert.equal(entries[0].state, 'unknown');
    return { structuredContent: { accepted: true, offline: true } };
  } };
  return f;
}
const run = (f, request) => processImage({ ...f, request });
const journal = async f => (await readFile(resolve(f.root, f.receiptFile), 'utf8')).trim().split('\n').map(line => JSON.parse(line));

test('default crop preview dispatches false, records immutable request hash and two journal entries', async t => {
  const f = await fixture(t);
  const request = structuredClone(crop);
  const result = await run(f, request);
  assert.equal(result.businessSuccessNotAssumed, true);
  assert.equal(request.arguments.confirmed, undefined);
  assert.equal(f.calls[1].name, 'tool_write');
  assert.equal(f.calls[1].args.arguments.confirmed, false);
  const entries = await journal(f);
  assert.deepEqual(entries.map(item => item.state), ['unknown', 'received']);
  assert.equal(entries[0].request_sha256, createHash('sha256').update(JSON.stringify(request)).digest('hex'));
  assert.deepEqual(entries[1].result, { accepted: true, offline: true });
  await assert.rejects(run(f, request), /replay forbidden/);
  assert.equal(f.calls.length, 2);
});

test('timeout remains unknown, blocks replay before even catalog refresh', async t => {
  const f = await fixture(t);
  const original = f.transport.call;
  f.transport.call = async (name, args) => {
    const result = await original(name, args);
    if (name !== 'tool_catalog') throw new Error('offline timeout, outcome unknown');
    return result;
  };
  await assert.rejects(run(f, crop), /offline timeout/);
  assert.deepEqual((await journal(f)).map(item => item.state), ['unknown']);
  await assert.rejects(run(f, crop), /replay forbidden/);
  assert.equal(f.calls.length, 2);
});

for (const drift of ['schema_version', 'inputSchema', 'operation_class', 'invoke_via']) {
  test(`live ${drift} drift prevents business call and receipt creation`, async t => {
    const f = await fixture(t);
    const original = f.transport.call;
    f.transport.call = async (name, args) => {
      const result = await original(name, args);
      result.structuredContent[drift] = drift === 'inputSchema' ? { type: 'object' } : 'drift';
      return result;
    };
    await assert.rejects(run(f, crop), /contract drift/);
    assert.equal(f.calls.length, 1);
    await assert.rejects(readFile(resolve(f.root, f.receiptFile)), { code: 'ENOENT' });
  });
}

test('model query oneOf validates both legal branches and rejects ambiguous/invalid shapes', async t => {
  for (const args of [{}, { action: 'get' }, { action: 'list', model_id: 'x' }, { action: 'get', model_id: 'x', type: 'agent' }, { action: 'list', media_type: 'video' }, []]) {
    const f = await fixture(t);
    await assert.rejects(run(f, { tool: 'generation_model_query', arguments: args }), /oneOf|object/);
    assert.equal(f.calls.length, 0);
  }
  for (const args of [{ action: 'list', type: 'generation', media_type: 'image' }, { action: 'get', model_id: 'profile-id' }]) {
    const f = await fixture(t);
    await run(f, { tool: 'generation_model_query', arguments: args });
    assert.equal(f.calls[1].name, 'tool_read');
    assert.deepEqual(f.calls[1].args.arguments, args);
  }
});

test('paid creation requires both explicit controls; authorization stays local', async t => {
  for (const base of [crop, translate]) {
    for (const controls of [{}, { allow_paid: true }, { authorization: 'Approved one offline test' }, { allow_paid: true, authorization: ' ' }, { allow_paid: 'true', authorization: 'Approved' }]) {
      const f = await fixture(t);
      await assert.rejects(run(f, { ...base, ...controls, arguments: { ...base.arguments, confirmed: true } }), /Paid|authorization|boolean/);
      assert.equal(f.calls.length, 0);
    }
    const f = await fixture(t);
    await run(f, { ...base, allow_paid: true, authorization: 'Synthetic offline authorization only', arguments: { ...base.arguments, confirmed: true } });
    assert.equal(f.calls[1].args.arguments.confirmed, true);
    assert.equal(f.calls[1].args.authorization, undefined);
  }
});

test('all scoped read tools route read-only and save receipts', async t => {
  for (const [tool, args] of [['generation_quota', {}], ['generation_workspace_list', {}], ['generation_image_job_query', { job_id: id }]]) {
    const f = await fixture(t);
    f.receiptFile = 'work/fireplay-readonly/read.jsonl';
    await run(f, { tool, arguments: args });
    assert.equal(f.calls[1].name, 'tool_read');
    assert.equal(f.calls[1].args.arguments.confirmed, undefined);
    assert.equal((await journal(f))[1].state, 'received');
  }
});

test('nonpaid confirmed write is journaled before dispatch; errors remain received without success assumption', async t => {
  const f = await fixture(t);
  const original = f.transport.call;
  f.transport.call = async (name, args) => {
    const response = await original(name, args);
    return name === 'tool_catalog' ? response : { isError: true, content: [{ type: 'text', text: 'Denied' }] };
  };
  const result = await run(f, { tool: 'generation_image_resize', arguments: { asset_id: id, options: { width: 100, height: 100, mode: 'contain' }, idempotency_key: 'offline', confirmed: true } });
  assert.equal(result.isError, true);
  assert.equal(result.businessSuccessNotAssumed, true);
  assert.equal((await journal(f))[0].arguments.confirmed, true);
});

test('invalid arguments, scope, envelope and credential URLs fail before transport', async t => {
  const cases = [
    { tool: 'generation_image_retry', arguments: {} },
    { ...crop, arguments: { ...crop.arguments, workspace_id: 'bad' } },
    { ...crop, arguments: { ...crop.arguments, source_asset_ids: [] } },
    { ...crop, arguments: { ...crop.arguments, confirmed: 'true' } },
    { ...crop, arguments: { ...crop.arguments, targets: [{ specCode: 'unknown', prompt: 'x' }] } },
    { ...crop, arguments: { ...crop.arguments, token: 'not allowed' } },
    { ...crop, secret: true },
    ...['file:///tmp/a.png', 'https://user:pass@example.invalid/x.png', 'https://example.invalid/x.png?token=secret'].map(source_url => ({ tool: 'generation_asset_upload_url', arguments: { source_url } })),
  ];
  for (const request of cases) {
    const f = await fixture(t);
    await assert.rejects(run(f, request));
    assert.equal(f.calls.length, 0);
  }
});

test('output scope rejects traversal and symlink escape, existing empty receipt blocks replay', async t => {
  const f = await fixture(t);
  f.receiptFile = 'work/production/../../outside.jsonl';
  await assert.rejects(run(f, crop), /Receipt must stay/);
  assert.equal(f.calls.length, 0);
  const outside = await mkdtemp(join(tmpdir(), 'fireplay-crop-outside-'));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await symlink(outside, join(f.root, 'work/production/escape'), process.platform === 'win32' ? 'junction' : 'dir');
  f.receiptFile = 'work/production/escape/no.jsonl';
  await assert.rejects(run(f, crop), /Receipt must stay/);
  f.receiptFile = 'work/production/receipt.jsonl';
  await writeFile(resolve(f.root, f.receiptFile), '');
  await assert.rejects(run(f, crop), /replay forbidden/);
  assert.equal(f.calls.length, 0);
});

test('exclusive receipt permits at most one concurrent business dispatch', async t => {
  const f = await fixture(t);
  const results = await Promise.allSettled([run(f, crop), run(f, crop)]);
  assert.equal(results.filter(item => item.status === 'fulfilled').length, 1);
  assert.equal(results.filter(item => item.status === 'rejected').length, 1);
  assert.equal(f.calls.filter(item => item.name === 'tool_write').length, 1);
  assert.deepEqual((await journal(f)).map(item => item.state), ['unknown', 'received']);
});

test('UUID sentinel values expressly allowed by pinned patterns remain valid', async t => {
  const f = await fixture(t);
  const nil = '00000000-0000-0000-0000-000000000000';
  await run(f, { ...crop, arguments: { ...crop.arguments, workspace_id: nil, source_asset_ids: [nil] } });
  assert.equal(f.calls[1].args.arguments.source_asset_ids[0], nil);
});

test('CLI requires exactly the two supported options without connecting', async () => {
  for (const args of [[], ['--request', 'x'], ['--request', 'x', '--receipt', 'y', '--paid', 'true'], ['--request', 'x', '--request', 'y']]) await assert.rejects(main(args), /Expected/);
});
