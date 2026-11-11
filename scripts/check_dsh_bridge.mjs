// Standalone VM contract tests; no browser, server, temporary directory or npm packages.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Script, createContext } from 'node:vm';

const code = await readFile(new URL('../src/image_localizer/review_web/dsh-bridge.js', import.meta.url), 'utf8');
const bridge = new Script(code, { filename: 'dsh-bridge.js' });
const HOST = 'http://127.0.0.1:64626';
const SECRET = 'server-token-must-never-cross-bridge';
const task = (id = 'task-a') => ({ id, source_sha256: 'source-hash', output_sha256: 'output-hash', manifest_sha256: 'manifest-hash', token: SECRET, private_note: 'private metadata' });
const result = (extra = {}) => ({ task_id: 'task-a', prompt: 'Saved region feedback', actionable: true, feedback_id: 'feedback-a', ...extra });
function harness(options = {}) {
  const listeners = new Map();
  const messages = [];
  const state = { task: task(), dirty: false, calls: 0, config: { host_origin: HOST, project_id: 'project-a', token: SECRET }, prompt: async () => result() };
  if (options.hostOrigin === null) delete state.config.host_origin;
  const parent = { postMessage(message, origin) { messages.push({ message: structuredClone(message), origin }); } };
  const window = {
    parent,
    addEventListener(name, callback) { listeners.set(name, callback); },
    ImageReviewDesk: {
      getHostConfig: () => state.config,
      getCurrentTask: () => structuredClone(state.task),
      hasUnsavedChanges: () => state.dirty,
      getRevisionPrompt: async () => { state.calls += 1; return state.prompt(); }
    }
  };
  if (options.standalone) window.parent = window;
  bridge.runInContext(createContext({ window }));
  const emit = (name, event = {}) => listeners.get(name)?.(event);
  const message = (data, event = {}) => emit('message', { source: parent, origin: HOST, data: { version: 1, ...data }, ...event });
  const context = (sessionId = 'session-a') => message({ type: 'image-review-host-context', sessionId, projectRoot: 'C:/project' });
  const revision = (requestId = 'request-a') => message({ type: 'image-review-request-revision', requestId });
  return { state, parent, window, messages, emit, message, context, revision };
}
const tests = [];
function test(name, check) { tests.push({ name, check }); }
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function response(h) { return h.messages.at(-1)?.message; }

test('standalone without parent never enables bridge', async () => {
  const h = harness({ standalone: true }); await h.context(); h.emit('image-review-bridge-ready'); await h.revision();
  assert.equal(h.messages.length, 0); assert.equal(h.state.calls, 0);
});
test('unconfigured host origin never enables bridge', async () => {
  const h = harness({ hostOrigin: null }); await h.context(); h.emit('image-review-task-change'); await h.revision();
  assert.equal(h.messages.length, 0); assert.equal(h.state.calls, 0);
});
test('untrusted origin and untrusted source receive no reply', async () => {
  const h = harness();
  for (const event of [{ origin: 'https://evil.example' }, { origin: 'http://127.0.0.1:64627' }, { source: {} }, { source: null }]) {
    await h.message({ type: 'image-review-host-context', sessionId: 'session-a', projectRoot: 'C:/project' }, event);
    await h.message({ type: 'image-review-request-revision', requestId: 'attack' }, event);
  }
  assert.equal(h.messages.length, 0); assert.equal(h.state.calls, 0);
});
test('malformed messages and unsupported versions are ignored', async () => {
  const h = harness();
  for (const data of [null, 'text', 1, { version: 2, type: 'image-review-host-context', sessionId: 's', projectRoot: 'p' }, { version: 1, type: 'unknown' }]) {
    await h.emit('message', { source: h.parent, origin: HOST, data });
  }
  for (const bad of [{ sessionId: 1, projectRoot: 'p' }, { sessionId: 's'.repeat(201), projectRoot: 'p' }, { sessionId: 's', projectRoot: 'p'.repeat(1001) }]) {
    await h.message({ type: 'image-review-host-context', ...bad });
  }
  assert.equal(h.messages.length, 0);
});
test('valid context publishes only task identity and configured project', async () => {
  const h = harness(); await h.context();
  assert.deepEqual(response(h), { version: 1, type: 'image-review-task', project_id: 'project-a', taskId: 'task-a', source_sha256: 'source-hash', output_sha256: 'output-hash', manifest_sha256: 'manifest-hash' });
  assert.equal(h.messages[0].origin, HOST); assert.ok(!JSON.stringify(h.messages).includes(SECRET));
});
test('task changes and bridge readiness publish fresh task identity', async () => {
  const h = harness(); h.emit('image-review-bridge-ready'); h.state.task = task('task-b'); h.emit('image-review-task-change');
  assert.equal(h.messages.length, 2); assert.equal(response(h).taskId, 'task-b');
  h.state.task = null; h.emit('image-review-task-change'); assert.equal(response(h).taskId, null);
});
test('revision without context or with malformed request id is ignored', async () => {
  const h = harness(); await h.revision(); assert.equal(h.state.calls, 0);
  await h.context(); const count = h.messages.length;
  for (const requestId of [null, 4, 'x'.repeat(101)]) await h.message({ type: 'image-review-request-revision', requestId });
  assert.equal(h.state.calls, 0); assert.equal(h.messages.length, count);
});
test('revision from another origin/source cannot exploit trusted context', async () => {
  const h = harness(); await h.context(); const count = h.messages.length;
  await h.message({ type: 'image-review-request-revision', requestId: 'bad-origin' }, { origin: 'https://evil.example' });
  await h.message({ type: 'image-review-request-revision', requestId: 'bad-source' }, { source: {} });
  assert.equal(h.state.calls, 0); assert.equal(h.messages.length, count);
});
test('unsaved draft returns error without reading revision API', async () => {
  const h = harness(); await h.context(); h.state.dirty = true; await h.revision();
  assert.equal(h.state.calls, 0); assert.equal(response(h).type, 'image-review-revision-error');
  assert.equal(response(h).requestId, 'request-a'); assert.match(response(h).error, /保存/);
});
test('rejected revision API returns correlated error and no prompt', async () => {
  const h = harness(); await h.context(); h.state.prompt = async () => { throw new Error('Version conflict'); }; await h.revision('failed-a');
  assert.deepEqual(response(h), { version: 1, type: 'image-review-revision-error', requestId: 'failed-a', error: 'Version conflict' });
});
test('actionable revision preserves correlation and does not leak bootstrap token', async () => {
  const h = harness(); await h.context(); await h.revision();
  assert.deepEqual(response(h), { version: 1, ...result(), type: 'image-review-revision', requestId: 'request-a', project_id: 'project-a', sessionId: 'session-a' });
  assert.ok(!JSON.stringify(h.messages).includes(SECRET)); assert.ok(h.messages.every((entry) => entry.origin === HOST));
});
test('nonactionable response stays nonactionable for host policy', async () => {
  const h = harness(); await h.context(); h.state.prompt = async () => result({ actionable: false, prompt: 'Current version approved' }); await h.revision();
  assert.equal(response(h).actionable, false); assert.equal(response(h).prompt, 'Current version approved');
});
test('task switch during revision rejects old task result', async () => {
  const h = harness(); await h.context(); const pending = deferred(); h.state.prompt = () => pending.promise;
  const request = h.revision(); h.state.task = task('task-b'); pending.resolve(result()); await request;
  assert.equal(response(h).type, 'image-review-revision-error'); assert.match(response(h).error, /变化/); assert.equal(response(h).prompt, undefined);
});
test('new unsaved draft during revision rejects saved result', async () => {
  const h = harness(); await h.context(); const pending = deferred(); h.state.prompt = () => pending.promise;
  const request = h.revision(); h.state.dirty = true; pending.resolve(result()); await request;
  assert.equal(response(h).type, 'image-review-revision-error'); assert.equal(response(h).prompt, undefined);
});
test('context switch cannot reassign pending revision to another session', async () => {
  const h = harness(); await h.context(); const pending = deferred(); h.state.prompt = () => pending.promise;
  const request = h.revision(); await h.context('session-b'); pending.resolve(result()); await request;
  const reply = response(h);
  assert.ok(reply.type === 'image-review-revision-error' || reply.sessionId === 'session-a', 'Pending session-a request was reassigned to session-b');
});

const failures = [];
for (const { name, check } of tests) {
  try { await check(); console.log(`PASS ${name}`); }
  catch (error) { failures.push({ name, error: error.message }); console.error(`FAIL ${name}: ${error.message}`); }
}
console.log(JSON.stringify({ total: tests.length, passed: tests.length - failures.length, failures }, null, 2));
if (failures.length) process.exitCode = 1;
