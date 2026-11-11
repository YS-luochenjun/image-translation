import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { APP_ID, PREFIX, PROJECT, PYTHON, createBackend, createHandler, localRequest, routeTarget } from './host-adapter.js';

const json = (value, status = 200) => ({ status, type: 'application/json', body: Buffer.from(JSON.stringify(value)) });
async function listen(server) {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return server.address().port;
}
async function close(server) {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
function browserRequest(port, target, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = request({ hostname: '127.0.0.1', port, path: target, method, headers }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject); req.end(body);
  });
}

test('closed routes reject arbitrary upstream URLs, query injection and wrong methods', () => {
  for (const target of [PREFIX + 'bootstrap', PREFIX + 'tasks?url=http://evil', PREFIX + 'image?path=../secret.png', PREFIX + 'image?path=http://evil/a.png', PREFIX + 'image?path=C%3A%2Fsecret.png', PREFIX + 'image?path=a&path=b', PREFIX + 'tasks/%00', PREFIX + 'tasks/%zz', 'http://evil/api/local-image-review/tasks']) {
    assert.throws(() => routeTarget(target, 'GET'), undefined, target);
  }
  assert.throws(() => routeTarget(PREFIX + 'tasks', 'PUT'), /Method/);
  assert.throws(() => routeTarget(PREFIX + 'tasks/a/feedback', 'GET'), /Method/);
  assert.throws(() => routeTarget(PREFIX + 'tasks/a/revision-prompt', 'POST'), /Method/);
  assert.equal(routeTarget(PREFIX + 'tasks/' + encodeURIComponent('runs/中文::manifest.json'), 'GET').path, '/api/tasks/' + encodeURIComponent('runs/中文::manifest.json'));
});

test('raw carrier auth and same-origin CSRF checks run before backend access', async () => {
  let calls = 0, admissions = 0;
  const backend = { async ready() { calls++; }, async request() { calls++; return json({ ok: true }); } };
  let origin;
  const connection = { admit(req) { admissions++; assert.ok(req.socket); return req.headers.cookie === 'test-session=valid' ? { peer: {} } : { rejection: 401 }; } };
  const handler = createHandler({ connection, getOrigin: () => origin, backend });
  const server = createServer(handler); const port = await listen(server); origin = `http://127.0.0.1:${port}`;
  try {
    const auth = { Cookie: 'test-session=valid' };
    assert.equal((await browserRequest(port, PREFIX + 'health')).status, 401);
    for (const headers of [{ Origin: 'http://evil.example' }, { Origin: 'null' }, { 'Sec-Fetch-Site': 'same-site' }, { Host: 'evil.example' }]) {
      assert.equal((await browserRequest(port, PREFIX + 'tasks', { headers: { ...auth, ...headers } })).status, 403);
    }
    assert.equal((await browserRequest(port, PREFIX + 'tasks/a/feedback', { method: 'POST', headers: auth, body: '{}' })).status, 403);
    assert.equal(calls, 0);
    const healthy = await browserRequest(port, PREFIX + 'health', { headers: auth });
    assert.equal(healthy.status, 200); assert.equal(JSON.parse(healthy.body).ready, true); assert.ok(!healthy.body.includes('token'));
    const writeHeaders = { ...auth, Origin: origin, 'Content-Type': 'application/json', 'Content-Length': '2' };
    assert.equal((await browserRequest(port, PREFIX + 'tasks/a/feedback', { method: 'POST', headers: writeHeaders, body: '{}' })).status, 200);
    assert.equal((await browserRequest(port, PREFIX + 'tasks/a/feedback', { method: 'POST', headers: { ...writeHeaders, 'Content-Type': 'text/plain' }, body: '{}' })).status, 415);
    assert.equal((await browserRequest(port, PREFIX + 'tasks/a/feedback', { method: 'POST', headers: { ...writeHeaders, 'Content-Length': '140000' }, body: '{}' })).status, 413);
    assert.equal(calls, 2); assert.ok(admissions >= 10);
  } finally { await close(server); }
});

test('backend never starts or kills another service or a mismatched project', async () => {
  for (const identity of [{ app: 'unrelated', project_id: 'same', token: 'secret' }, { app: APP_ID, project_id: 'other', token: 'secret' }]) {
    let spawnCount = 0;
    const occupied = createServer((_req, res) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(identity)); });
    const port = await listen(occupied);
    const backend = createBackend({ port, projectId: 'same', spawnProcess() { spawnCount++; throw new Error('must not spawn'); } });
    try { await assert.rejects(backend.ready(), /another application or project/); assert.equal(spawnCount, 0); await backend.dispose(); assert.equal((await localRequest(port, '/still-alive')).status, 200); }
    finally { await backend.dispose(); await close(occupied); }
  }
});

test('verified existing Python protocol service is reused regardless of obsolete iframe origin', async () => {
  const seen = [];
  const existing = createServer((req, res) => {
    seen.push({ path: req.url, headers: req.headers }); res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(req.url === '/api/bootstrap' ? { app: APP_ID, project_id: 'same', token: 'host-only-token', host_origin: 'http://127.0.0.1:OLD' } : { tasks: [] }));
  });
  const port = await listen(existing);
  const backend = createBackend({ port, projectId: 'same', spawnProcess() { throw new Error('must not spawn'); } });
  try {
    assert.equal((await backend.request('/api/tasks')).status, 200);
    assert.equal(seen.at(-1).headers['x-review-token'], 'host-only-token');
    assert.equal(seen.at(-1).headers.origin, undefined);
    await backend.dispose(); assert.equal((await localRequest(port, '/api/bootstrap')).status, 200);
  } finally { await backend.dispose(); await close(existing); }
});

test('proxy refuses redirects, nonimage data and accidental credential response', async () => {
  let response = { status: 302, type: 'application/json', body: Buffer.from('{}') };
  let origin;
  const server = createServer(createHandler({ connection: { admit: () => ({ peer: {} }) }, getOrigin: () => origin, backend: { request: async () => response } }));
  const port = await listen(server); origin = `http://127.0.0.1:${port}`;
  try {
    assert.equal((await browserRequest(port, PREFIX + 'tasks')).status, 502);
    response = json({ token: 'secret' });
    const denied = await browserRequest(port, PREFIX + 'tasks'); assert.equal(denied.status, 502); assert.ok(!denied.body.includes('secret'));
    response = { status: 200, type: 'text/html', body: Buffer.from('<script>') };
    assert.equal((await browserRequest(port, PREFIX + 'image?path=source.png')).status, 502);
  } finally { await close(server); }
});

test('real Python auto-start, list/detail/image/save/conflict/revision, lifecycle cleanup', { timeout: 35000 }, async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'review-host-test-'));
  const run = path.join(root, 'runs', 'sample'); await mkdir(run, { recursive: true });
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
  await writeFile(path.join(run, 'source.png'), png); await writeFile(path.join(run, 'translated.png'), png);
  const manifest = JSON.stringify({ source: { path: 'source.png' }, target_language: 'zh-CN', regions: [{ id: 'r1', action: 'translate', source_text: 'Hello', target_text: '你好', bbox: [0, 0, 1, 1] }] });
  await writeFile(path.join(run, 'manifest.json'), manifest);
  await writeFile(path.join(run, 'receipt.json'), JSON.stringify({ output_sha256: createHash('sha256').update(png).digest('hex'), manifest_sha256: createHash('sha256').update(manifest).digest('hex') }));
  const resolved = await realpath(root);
  const projectId = createHash('sha256').update(process.platform === 'win32' ? path.win32.normalize(resolved).toLowerCase() : resolved).digest('hex');
  const reservation = createServer(); const port = await listen(reservation); await close(reservation);
  let launches = 0, processDiagnostic = '';
  const backend = createBackend({ port, project: root, projectId, python: PYTHON,
    spawnProcess(executable, args, options) {
      launches++; args[0] = path.join(PROJECT, 'bin', 'it.py');
      const child = spawn(executable, args, { ...options, stdio: ['ignore', 'ignore', 'pipe'] });
      child.stderr.on('data', (chunk) => { processDiagnostic = (processDiagnostic + chunk).slice(-2000); });
      child.on('error', (error) => { processDiagnostic += error.code; });
      child.on('exit', (code) => { processDiagnostic += ' exit=' + code; });
      return child;
    } });
  let origin;
  const host = createServer(createHandler({ connection: { admit: () => ({ peer: {} }) }, getOrigin: () => origin, backend, projectId }));
  const hostPort = await listen(host); origin = `http://127.0.0.1:${hostPort}`;
  const get = (endpoint) => browserRequest(hostPort, PREFIX + endpoint);
  try {
    const health = await Promise.all([get('health'), get('health')]);
    assert.ok(health.every((item) => item.status === 200), JSON.stringify(health.map((item) => [item.status, item.body.toString()])) + processDiagnostic); assert.equal(launches, 1);
    const tasks = await get('tasks'); assert.equal(tasks.status, 200); assert.ok(tasks.body.includes('sample'));
    const taskId = 'runs/sample'; const encoded = encodeURIComponent(taskId);
    const detail = await get('tasks/' + encoded); assert.equal(detail.status, 200);
    const data = JSON.parse(detail.body); const task = data.task || data;
    assert.ok(task.source_sha256); assert.ok(task.output_sha256);
    const image = await get('image?path=' + encodeURIComponent('runs/sample/source.png'));
    assert.equal(image.status, 200); assert.deepEqual(image.body, png);
    assert.equal((await get('image?path=' + encodeURIComponent('runs/sample/manifest.json'))).status, 403);
    const payload = { reviewer: 'Host regression', decision: 'changes_requested', note: 'Change the translated label.', regions: [], source_sha256: task.source_sha256, output_sha256: task.output_sha256, manifest_sha256: task.manifest_sha256 };
    const post = (value) => { const body = JSON.stringify(value); return browserRequest(hostPort, PREFIX + 'tasks/' + encoded + '/feedback', { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json', 'Content-Length': String(Buffer.byteLength(body)) }, body }); };
    const saved = await post(payload); assert.equal(saved.status, 201, saved.body.toString());
    const revision = await get('tasks/' + encoded + '/revision-prompt'); assert.equal(revision.status, 200); assert.equal(JSON.parse(revision.body).actionable, true);
    await writeFile(path.join(run, 'translated.png'), Buffer.concat([png, Buffer.from('changed')]));
    assert.equal((await post(payload)).status, 409);
    await backend.dispose();
    await assert.rejects(localRequest(port, '/api/bootstrap'), (error) => error.code === 'ECONNREFUSED');
  } finally { await backend.dispose(); await close(host); await rm(root, { recursive: true, force: true }); }
});
