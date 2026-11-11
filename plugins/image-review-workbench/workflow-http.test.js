import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createWorkflowHandler } from './workflow-http.js';
import { PROJECT } from './host-adapter.js';
const ROOT = '/local-image-review/workflow';
const HASH = 'a'.repeat(64);
const artifact = { id: 'image-a', path: 'runs/test/translated.png', name: 'translated.png', kind: 'image', sha256: HASH };
const saved = { session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH, path: 'saved-results/A/result.png' };
const data = () => ({ sessions: ['A', 'B'].map(session_id => ({ session_id, title: session_id, runs: [{ id: 'run-' + session_id.toLowerCase(), session_id, nodes: [{ id: 'result', artifacts: [{ ...artifact }] }], result: { ...artifact }, results: [{ ...artifact }], saved_results: session_id === 'A' ? [{ ...saved }] : [] }] })), unassigned: [{ root: 'runs/unassigned' }] });
const response = value => ({ status: 200, type: 'application/json; charset=utf-8', body: Buffer.from(JSON.stringify(value)) });
async function fixture(run) {
  let origin; const calls = []; const decisions = [];
  const ctx = {
    connection: { admit: req => req.headers['x-test-auth'] === 'yes' ? { peer: { principal: { kind: 'test-reader', name: 'actual-principal' } } } : { rejection: 401 } },
    webServer: { getOrigin: () => origin },
    get: name => name === 'sessionController' ? { list: async () => ({ items: ['A', 'B'].map((sessionId, index) => ({ sessionId, running: false, projections: { kind: 'sequenced', asOfSeq: 1, values: { title: `SHE-20261103-00${index + 1}` } } })) }) } : undefined,
    sessions: { list: () => [], get: () => undefined },
    sessionQuery: { listSessions: async () => [{ header: { id: 'A', cwd: PROJECT } }, { header: { id: 'B', cwd: PROJECT } }, { header: { id: 'foreign', cwd: 'D:/another-project' } }] },
    remoteAccess: { decide: request => { decisions.push(request); return { kind: request.principal?.name === 'actual-principal' && request.resources[0].kind === 'session' && request.resources[0].id === 'A' ? 'allow' : 'deny' }; } },
  };
  const backend = { request: async (path, options = {}) => {
    if (path === '/api/workflow/session-ids') return response({ session_ids: [] });
    calls.push({ path, payload: options.body ? JSON.parse(options.body.toString()) : null });
    if (path === '/api/workflow/index') return response({ ...data(), sessions: data().sessions.map(row => ({ ...row, runs: row.runs.map(run => ({ id: run.id, session_id: run.session_id, title: 'Test flow', detail_loaded: false })) })) });
    if (path === '/api/workflow/snapshot') return response({ ...data(), node_catalog: [{ id: 'result' }], warnings: ['test warning'] });
    if (path.startsWith('/api/workflow/asset?')) return { status: 200, type: 'image/png', body: Buffer.from('test image bytes') };
    if (path === '/api/workflow/comments') return response({ session_id: 'A', comment: { session_id: 'A' }, prompt: 'test prompt' });
    if (path === '/api/workflow/results/save') return { ...response({ saved: { ...saved } }), status: 201 };
    return response({ accepted: true });
  } };
  const server = createServer(createWorkflowHandler(ctx, backend, (_req, res) => { res.writeHead(204); res.end(); }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  const request = (path = '', options = {}) => fetch(origin + ROOT + path, { ...options, headers: { 'x-test-auth': 'yes', ...(options.method === 'POST' ? { Origin: origin, 'Content-Type': 'application/json' } : {}), ...options.headers } });
  try { await run({ request, ctx, backend, calls, decisions, origin }); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}

test('snapshot delegates the actual admitted principal and removes unauthorized sessions', () => fixture(async ({ request, decisions, calls }) => {
  const result = await request(); assert.equal(result.status, 200); const value = await result.json();
  assert.deepEqual(value.sessions.map(row => row.session_id), ['A']); assert.deepEqual(value.unassigned, []);
  assert.deepEqual(calls[0].payload.session_ids, ['A']);
  assert.equal(calls[0].path, '/api/workflow/index');
  assert.equal(value.sessions[0].detail_loaded, false); assert.equal(value.sessions[0].runs[0].detail_loaded, false);
  assert.equal('nodes' in value.sessions[0].runs[0], false);
  assert.ok(!calls.some(row => row.path === '/api/workflow/snapshot'));
  const detail = await (await request('/session?session_id=A')).json();
  assert.equal(detail.session.detail_loaded, true); assert.deepEqual(detail.node_catalog, [{ id: 'result' }]);
  assert.deepEqual(detail.warnings, ['test warning']); assert.equal('unassigned' in detail, false);
  const run = detail.session.runs[0];
  assert.match(run.result.url, /workflow\/artifact\?session_id=A/);
  assert.match(run.results[0].url, /workflow\/artifact\?session_id=A/);
  assert.match(run.saved_results[0].url, /workflow\/saved\?session_id=A/);
  assert.ok(decisions.every(row => row.principal.name === 'actual-principal'));
  assert.ok(decisions.some(row => row.action === 'session/prompt' && row.resources[0].id === 'A'));
}));
test('requirement titles filter hundreds of ordinary sessions before snapshot and hide unassigned', () => fixture(async ({ request, ctx, calls }) => {
  const ids = ['A', 'B', ...Array.from({ length: 140 }, (_, index) => 'ordinary-' + index)];
  ctx.sessionQuery.listSessions = async () => ids.map(id => ({ header: { id, cwd: PROJECT, title: 'SHE-20261103-999' } }));
  ctx.remoteAccess.decide = () => ({ kind: 'allow' });
  ctx.get = () => ({ list: async () => ({ items: ids.map(sessionId => ({ sessionId, projections: { kind: 'sequenced', asOfSeq: 1, values: { title: sessionId === 'A' ? '  she-20261103-068  ' : sessionId === 'B' ? 'APC-20261023-008' : '协助台' } } })) }) });
  const value = await (await request()).json();
  assert.deepEqual(calls.find(row => row.path === '/api/workflow/index').payload.session_ids, ['A', 'B']);
  assert.deepEqual(value.sessions.map(row => row.title), ['she-20261103-068', 'APC-20261023-008']);
  assert.deepEqual(value.unassigned, []);
  assert.equal(value.discovery.matched, 2); assert.equal(value.discovery.hidden_count, 140);
}));

test('renames change requirement visibility on the next request without trusting stale workflow titles', () => fixture(async ({ request, ctx, calls }) => {
  let title = 'SHE-20261103-068';
  ctx.get = () => ({ list: async () => ({ items: [{ sessionId: 'A', projections: { values: { title } } }] }) });
  assert.deepEqual((await (await request()).json()).sessions.map(row => row.session_id), ['A']);
  title = '协助台 SHE-20261103-068';
  const hidden = await (await request()).json(); assert.deepEqual(hidden.sessions, []); assert.deepEqual(hidden.unassigned, []);
  assert.equal(calls.filter(row => row.path === '/api/workflow/index').length, 1);
  title = 'WOW-20261014-027';
  assert.equal((await (await request()).json()).sessions[0].title, title);
}));

test('missing official title projections fail closed while artifact permission scope remains available', () => fixture(async ({ request, ctx, calls }) => {
  ctx.get = () => undefined;
  const value = await (await request()).json();
  assert.deepEqual(value.sessions, []); assert.equal(value.discovery.title_filter_unavailable, true);
  assert.equal(calls.filter(row => row.path === '/api/workflow/snapshot').length, 0);
  const query = new URLSearchParams({ session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH });
  assert.equal((await request('/artifact?' + query)).status, 200);
}));

test('only complete requirement identifiers match, regardless of stale flat projection fields', () => fixture(async ({ request, ctx, calls }) => {
  const titles = ['APC-20261023-008', 'WOW-20261014-027', '需求 SHE-20261103-068', 'SHE-20261103-068 extra', 'S-20261103-068', 'SHE-2026113-068', 'SHE-20261103-06', 'SHE-20261103-0000000', '', null];
  const ids = titles.map((_, index) => 'item-' + index);
  ctx.sessionQuery.listSessions = async () => ids.map(id => ({ header: { id, cwd: PROJECT } }));
  ctx.remoteAccess.decide = () => ({ kind: 'allow' });
  ctx.get = () => ({ list: async () => ({ items: ids.map((sessionId, index) => ({ sessionId, projections: { title: 'SHE-20261103-999', values: { title: titles[index] } } })) }) });
  const value = await (await request()).json();
  assert.deepEqual(calls.find(row => row.path === '/api/workflow/index').payload.session_ids, ids.slice(0, 2));
  assert.equal(value.discovery.matched, 2); assert.equal(value.discovery.hidden_count, 8);
}));

test('official title service failure returns an explicit empty display without snapshot work', () => fixture(async ({ request, ctx, calls }) => {
  ctx.get = () => ({ list: async () => { throw new Error('unavailable'); } });
  const result = await request(); assert.equal(result.status, 200);
  const value = await result.json(); assert.deepEqual(value.sessions, []); assert.equal(value.discovery.title_filter_unavailable, true);
  assert.ok(!calls.some(row => row.path === '/api/workflow/snapshot' || row.path === '/api/workflow/ingest'));
}));

test('session detail loads exactly one authorized requirement graph and rechecks rename', () => fixture(async ({ request, ctx, calls }) => {
  const originalGet = ctx.get;
  const value = await (await request('/session?session_id=A')).json();
  assert.equal(value.session.session_id, 'A'); assert.equal(value.session.runs.length, 1);
  assert.deepEqual(calls.filter(row => row.path === '/api/workflow/snapshot').map(row => row.payload.session_ids), [['A']]);
  assert.ok(!calls.some(row => row.path === '/api/workflow/index'));
  ctx.get = () => ({ list: async () => ({ items: [{ sessionId: 'A', projections: { values: { title: '协助台' } } }] }) });
  assert.equal((await request('/session?session_id=A')).status, 403);
  assert.equal(calls.filter(row => row.path === '/api/workflow/snapshot').length, 1);
  ctx.get = originalGet;
  assert.equal((await request('/session?session_id=B')).status, 403);
  assert.equal((await request('/session?session_id=foreign')).status, 403);
  assert.equal((await request('/session?session_id=__unassigned__')).status, 403);
  for (const suffix of ['', '?session_id=', '?session_id=A&session_id=A', '?session_id=A&run_id=run-a', '?path=private']) assert.equal((await request('/session' + suffix)).status, 400);
  assert.equal(calls.filter(row => row.path === '/api/workflow/snapshot').length, 1);
}));

test('missing metadata is admitted then reloaded only through the lightweight index', () => fixture(async ({ request, backend, calls }) => {
  const original = backend.request; let indexes = 0;
  backend.request = async (path, options) => {
    const reply = await original(path, options);
    if (path === '/api/workflow/index' && ++indexes === 1) return response({ sessions: [], unassigned: [] });
    return reply;
  };
  const value = await (await request()).json();
  assert.deepEqual(value.sessions.map(row => row.session_id), ['A']);
  assert.deepEqual(calls.map(row => row.path), ['/api/workflow/index', '/api/workflow/ingest', '/api/workflow/index']);
  assert.deepEqual(calls[1].payload.sessions.map(row => row.session_id), ['A']);
}));

test('authentication and cross-site write requests are refused', () => fixture(async ({ request }) => {
  assert.equal((await request('', { headers: { 'x-test-auth': 'no' } })).status, 401);
  assert.equal((await request('/comments', { method: 'POST', headers: { Origin: 'https://another.example' }, body: JSON.stringify({ session_id: 'A' }) })).status, 403);
}));
test('a permitted reader cannot request another conversation artifact', () => fixture(async ({ request, calls }) => {
  const query = new URLSearchParams({ session_id: 'B', run_id: 'run-b', artifact_id: artifact.id, sha256: HASH });
  assert.equal((await request('/artifact?' + query)).status, 403);
  assert.ok(!calls.some(row => row.path.includes('/asset?')));
}));
test('artifact identity and current hash are checked before private image transport', () => fixture(async ({ request }) => {
  const query = new URLSearchParams({ session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH });
  assert.equal((await request('/artifact?' + query)).status, 200);
  query.set('sha256', 'b'.repeat(64)); assert.equal((await request('/artifact?' + query)).status, 409);
  query.set('sha256', HASH); query.set('run_id', 'run-b'); assert.equal((await request('/artifact?' + query)).status, 404);
}));
test('saved copies use the same exact session scope and private allowlist', () => fixture(async ({ request, calls }) => {
  const query = new URLSearchParams({ session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH });
  assert.equal((await request('/saved?' + query)).status, 200);
  assert.ok(calls.some(row => row.path.includes(encodeURIComponent(saved.path))));
  const savedResponse = await request('/results/save', { method: 'POST', body: JSON.stringify({ session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH }) });
  assert.equal(savedResponse.status, 201); assert.match((await savedResponse.json()).saved.url, /workflow\/saved\?session_id=A/);
}));
test('comments and delivery cannot substitute a different session', () => fixture(async ({ request, backend, calls }) => {
  for (const action of ['/comments', '/delivery', '/bind']) assert.equal((await request(action, { method: 'POST', body: JSON.stringify({ session_id: 'B' }) })).status, 403);
  assert.ok(!calls.some(row => row.path === '/api/workflow/comments'));
  backend.request = async path => path === '/api/workflow/comments' ? response({ comment: { session_id: 'B' } }) : response({});
  assert.equal((await request('/comments', { method: 'POST', body: JSON.stringify({ session_id: 'A' }) })).status, 502);
}));
test('browser cannot reach trusted ingest or choose an arbitrary backend path', () => fixture(async ({ request, calls }) => {
  assert.equal((await request('/ingest', { method: 'POST', body: JSON.stringify({ session_id: 'A' }) })).status, 404);
  assert.equal((await request('/artifact?path=C:/private.txt')).status, 400);
  assert.equal((await request('?session_id=B')).status, 404);
  assert.equal(calls.length, 0);
}));
test('live effective project cwd discovers an outside-created session without leaking foreign sessions', () => fixture(async ({ request, ctx }) => {
  const live = { id: 'A', header: { id: 'A', cwd: 'D:/outside' } };
  ctx.sessionQuery.listSessions = async () => [{ header: live.header }, { header: { id: 'foreign', cwd: 'D:/outside' } }];
  ctx.sessions = { list: () => [live], get: id => id === 'A' ? live : undefined };
  ctx.workingDirectory = { get: () => PROJECT };
  const result = await request(); assert.equal(result.status, 200);
  const value = await result.json(); assert.deepEqual(value.sessions.map(row => row.session_id), ['A']);
  assert.equal(value.discovery.project_sessions, 1);
}));
test('thumbnail handles reuse one snapshot but never cache permission decisions', () => fixture(async ({ request, calls, ctx }) => {
  assert.equal((await request()).status, 200);
  const query = '/artifact?' + new URLSearchParams({ session_id: 'A', run_id: 'run-a', artifact_id: artifact.id, sha256: HASH });
  const previews = await Promise.all(Array.from({ length: 8 }, () => request(query)));
  assert.ok(previews.every(item => item.status === 200));
  assert.equal(calls.filter(item => item.path === '/api/workflow/snapshot').length, 1);
  ctx.remoteAccess.decide = () => ({ kind: 'deny' });
  assert.equal((await request(query)).status, 403);
}));
test('persisted project member survives a later outside cwd without admitting other sessions', () => fixture(async ({ request, ctx, backend }) => {
  ctx.sessionQuery.listSessions = async () => [{ header: { id: 'A', cwd: 'D:/outside' } }, { header: { id: 'foreign', cwd: 'D:/outside' } }];
  const original = backend.request;
  backend.request = (path, options) => path === '/api/workflow/session-ids' ? Promise.resolve(response({ session_ids: ['A', 'gone'] })) : original(path, options);
  const value = await (await request()).json();
  assert.deepEqual(value.sessions.map(item => item.session_id), ['A']);
}));
test('global project reader may inspect unassigned artifacts but cannot write a fake owner', () => fixture(async ({ request, ctx, backend }) => {
  const decide = ctx.remoteAccess.decide;
  ctx.remoteAccess.decide = value => value.resources[0].kind === 'global' ? { kind: 'allow' } : decide(value);
  const original = backend.request;
  backend.request = (path, options) => path === '/api/workflow/unassigned' ? Promise.resolve(response({ run: { id: 'history', root: 'runs/unassigned', session_id: '__unassigned__', read_only: true, nodes: [{ id: 'source.normalize', artifacts: [{ ...artifact }] }] }, warnings: [] })) : original(path, options);
  const value = await (await request('/unassigned?run_root=runs%2Funassigned')).json();
  assert.equal(value.run.read_only, true);
  const url = new URL(value.run.nodes[0].artifacts[0].url, 'http://test');
  assert.equal(url.searchParams.get('session_id'), '__unassigned__');
  assert.equal((await request('/artifact?' + url.searchParams)).status, 200);
  assert.equal((await request('/comments', { method: 'POST', body: JSON.stringify({ session_id: '__unassigned__', run_id: 'history' }) })).status, 403);
}));
