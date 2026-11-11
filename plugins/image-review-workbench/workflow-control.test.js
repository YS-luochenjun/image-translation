import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareControl } from './workflow-control.js';

const state = () => ({ sessions: [{ session_id: 'A', plan: { event_id: 'plan-v2', items: [{ content: 'Translate headline', status: 'pending' }] }, runs: [{ id: 'run-a', session_id: 'A', root: 'runs/alpha', nodes: [{ id: 'semantic.translate', title: '翻译' }] }] }, { session_id: 'B', runs: [] }] });
function setup(denied = '') {
  const decisions = [];
  const peer = { principal: { kind: 'test', id: 'real-peer' } };
  const ctx = { remoteAccess: { decide: req => { decisions.push(req); return { kind: req.principal === peer.principal && req.action !== denied && req.resources[0].id === 'A' ? 'allow' : 'deny' }; } } };
  return { decisions, invoke: body => prepareControl(ctx, peer, new Set(['A', 'B']), { request_id: 'request-123', session_id: 'A', ...body }, async () => state()) };
}
test('step instruction freezes exact session, run, node and official queue DTO', async () => {
  const { invoke, decisions } = setup();
  const intent = await invoke({ action: 'prompt', run_id: 'run-a', node_id: 'semantic.translate', instruction: 'Fix this translation.' });
  assert.equal(intent.prepared, true); assert.deepEqual(intent.scope, { kind: 'node', run_id: 'run-a', node_id: 'semantic.translate' });
  assert.equal(intent.request.sessionId, 'A'); assert.equal(intent.request.requestId, 'request-123'); assert.equal(intent.request.mode, 'queue');
  assert.match(intent.request.content[0].text, /runs\/alpha/); assert.match(intent.request.content[0].text, /Fix this translation/);
  assert.deepEqual(decisions.at(-1).args.request, intent.request);
});
test('rootless conversation and current plan are independently controllable', async () => {
  const { invoke } = setup();
  assert.equal((await invoke({ action: 'prompt', instruction: 'Continue the current task.' })).scope.kind, 'session');
  const plan = await invoke({ action: 'prompt', instruction: 'Run this step.', plan_event_id: 'plan-v2', plan_index: 0, mode: 'steer' });
  assert.equal(plan.scope.kind, 'plan'); assert.match(plan.request.content[0].text, /Translate headline/); assert.equal(plan.request.mode, 'steer');
});
test('changed plans and crossed flow nodes fail without business dispatch', async () => {
  const { invoke } = setup();
  await assert.rejects(invoke({ action: 'prompt', instruction: 'Run.', plan_event_id: 'plan-v1', plan_index: 0 }), { status: 409 });
  await assert.rejects(invoke({ action: 'prompt', instruction: 'Run.', run_id: 'run-b', node_id: 'semantic.translate' }), { status: 404 });
  await assert.rejects(invoke({ action: 'prompt', instruction: 'Run.', run_id: 'run-a', node_id: 'invented' }), { status: 404 });
});
test('cancel scope is the whole current turn and uses its own capability', async () => {
  const { invoke } = setup();
  const intent = await invoke({ action: 'cancel' }); assert.deepEqual(intent.request, { sessionId: 'A' }); assert.equal(intent.scope.kind, 'session');
  await assert.rejects(invoke({ action: 'cancel', node_id: 'semantic.translate' }), { status: 400 });
  await assert.rejects(setup('session/cancel').invoke({ action: 'cancel' }), { status: 403 });
  await assert.rejects(setup('session/prompt').invoke({ action: 'prompt', instruction: 'Run.' }), { status: 403 });
});
test('foreign, unassigned, forbidden and unverified request fields are rejected', async () => {
  const { invoke } = setup();
  for (const session_id of ['foreign', '__unassigned__', 'B']) await assert.rejects(invoke({ action: 'cancel', session_id }), { status: 403 });
  await assert.rejects(invoke({ action: 'prompt', instruction: 'Run.', request: { sessionId: 'B' } }), { status: 400 });
  await assert.rejects(invoke({ action: 'prompt', instruction: 'Run.', mode: 'immediate' }), { status: 400 });
  await assert.rejects(invoke({ action: 'prompt', instruction: ' ' }), { status: 400 });
});
