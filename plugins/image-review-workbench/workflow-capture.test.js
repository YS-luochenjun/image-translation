import assert from 'node:assert/strict';
import { test } from 'node:test';
import { installWorkflowCapture, parseWorkflowTool, summarizeWorkflowResult, selectWorkflowHighlights } from './workflow-capture.js';

const PROJECT = 'C:/project';
const options = { project: PROJECT, projectId: 'project-test' };
const cli = (command = 'render', run = 'runs/one') => ({ command: `python bin/it.py ${command} ${run}`, workdir: PROJECT });
const header = (id, extra = {}) => ({ id, cwd: PROJECT, createdAt: 1000, isSeeded: false, ...extra });
const callEvent = (seq, callId, args = cli(), name = 'ops_bash') => ({ type: 'tool/call', seq, time: 1000 + seq, data: { turn: 1, step: 1, callId, name, arguments: JSON.stringify(args) } });
const resultEvent = (seq, callId, value, isError = false) => ({ type: 'tool/result', seq, time: 1000 + seq, data: { turn: 1, step: 1, message: { role: 'tool', toolCallId: callId, source: { kind: 'tool', callId }, isError, content: [{ type: 'text', text: JSON.stringify(value) }] } } });
function fixture(snapshots = [], captureOptions = {}) {
  const listeners = new Map(), timers = new Map(), requests = [], readIds = [];
  const sessions = new Map(snapshots.map((snapshot) => [snapshot.session.id, { id: snapshot.session.id, header: snapshot.session, inheritedEventCount: snapshot.inheritedEventCount, effectiveCwd: snapshot.effectiveCwd }]));
  let fail = false, nextTimer = 0;
  const ctx = {
    on(name, handler) { assert.ok(!listeners.has(name)); listeners.set(name, handler); return () => listeners.delete(name); },
    sessions: { get: (id) => sessions.get(id), list: () => [...sessions.values()] },
    workingDirectory: { get: (session) => session.effectiveCwd || session.header.cwd },
    sessionQuery: {
      async listSessions() { return snapshots.map((snapshot) => ({ header: snapshot.session, live: true, persisted: true })); },
      async readSession(id) { readIds.push(id); return snapshots.find((snapshot) => snapshot.session.id === id); },
    },
  };
  const backend = { async request(path, request) { const payload = JSON.parse(request.body.toString()); const status = fail ? 503 : request.body.length > (captureOptions.serverMaxBodyBytes || Infinity) ? 413 : 200; requests.push({ path, payload, signal: request.signal, bytes: request.body.length, status }); return { status }; } };
  const capture = installWorkflowCapture(ctx, backend, { ...options, ...captureOptions, now: () => 5000, setTimeout: (callback) => { const key = ++nextTimer; timers.set(key, callback); return key; }, clearTimeout: (key) => timers.delete(key) });
  return { ctx, backend, listeners, requests, sessions, timers, readIds, capture, setFailure(value) { fail = value; }, events() { return requests.flatMap((request) => request.payload.sessions.flatMap((session) => session.events)); } };
}

test('literal CLI and file tools reveal only known paths and dotted node IDs', () => {
  const parsed = parseWorkflowTool('functions.ops_bash', cli(), { cwd: PROJECT, project: PROJECT });
  assert.equal(parsed.run_root, PROJECT + '/runs/one');
  assert.equal(parsed.stage, 'basic.render');
  assert.equal('command' in parsed, false);
  const multi = parseWorkflowTool('ops_bash', cli('prepare-job', 'runs/one/job.json --output runs/prepared'), { cwd: PROJECT, project: PROJECT });
  assert.equal(multi.stage, 'job.freeze');
  assert.equal(multi.run_root, PROJECT + '/runs/prepared');
  assert.deepEqual(multi.input_run_roots, [PROJECT + '/runs/one']);
  const file = parseWorkflowTool('write', { file_path: 'runs/one/manifest.json', content: 'DO NOT LEAK SECRET', justification: 'private' }, { cwd: PROJECT, project: PROJECT });
  assert.equal(file.stage, 'semantic.analyze');
  assert.ok(!JSON.stringify(file).includes('SECRET'));
});

test('exclude plugin development, unrelated projects, shell programs, traversal and unbound image generation', () => {
  const rejected = [
    ['ops_bash', { command: 'python -c "print(1)"', workdir: PROJECT }],
    ['ops_bash', { command: 'python bin/it.py render runs/one; echo SECRET', workdir: PROJECT }],
    ['ops_bash', { command: 'python bin/it.py render "$(secret)"', workdir: PROJECT }],
    ['ops_bash', { command: 'python bin/it.py render ../../other/runs/x', workdir: PROJECT }],
    ['ops_bash', { ...cli(), workdir: 'C:/other' }],
    ['write', { file_path: 'plugins/image-review-workbench/workflow-capture.js', content: 'x' }],
    ['write', { file_path: 'runs/one/credentials.json', content: 'x' }],
    ['write', { file_path: 'runs/one/../../secrets/manifest.json', content: 'x' }],
    ['image_gen', { prompt: 'translate poster', output: 'runs/one/image.png' }],
  ];
  for (const [name, args] of rejected) assert.equal(parseWorkflowTool(name, args, { cwd: PROJECT, project: PROJECT }), undefined, name + JSON.stringify(args));
  assert.equal(parseWorkflowTool('ops_bash', cli(), { cwd: 'C:/project-other', project: PROJECT }), undefined);
});

test('job handles are queued and nonzero CLI exit is failed, independent from quality acceptance', () => {
  assert.deepEqual(summarizeWorkflowResult({ isError: false, value: { kind: 'background', jobId: 'job-1', stdout: 'SECRET' } }), { phase: 'queued', job_id: 'job-1' });
  assert.deepEqual(summarizeWorkflowResult({ isError: false, value: { exitCode: 2 } }), { phase: 'failed', exit_code: 2 });
  assert.deepEqual(summarizeWorkflowResult({ isError: true, error: { message: 'SECRET' } }), { phase: 'failed' });
});

test('backfill excludes fork inheritance and chat while retaining own tool seq and call identity', async () => {
  const snapshot = { session: header('root'), inheritedEventCount: 2, events: [callEvent(0, 'inherited'), resultEvent(1, 'inherited', { exitCode: 0 }), { type: 'user/message', seq: 2, time: 1002, data: { content: [{ type: 'text', text: 'CHAT SECRET' }] } }, callEvent(3, 'own'), resultEvent(4, 'own', { exitCode: 0, stdout: 'OUTPUT SECRET' })] };
  const f = fixture([snapshot]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.deepEqual(f.events().map((event) => [event.call_id, event.phase, event.seq]), [['own', 'observed', 3], ['own', 'completed', 4]]);
    assert.ok(!JSON.stringify(f.requests).includes('SECRET'));
    assert.equal(f.events()[0].time, new Date(1003).toISOString());
    await f.capture.flush(); assert.equal(f.requests.length, 1);
  } finally { f.capture(); }
});

test('subagent executor maps only through verified project lineage to original main session', async () => {
  const f = fixture([
    { session: header('parent'), inheritedEventCount: 0, events: [] },
    { session: header('child', { origin: 'subagent', parentSession: 'parent' }), inheritedEventCount: 0, events: [callEvent(0, 'child-call')] },
    { session: header('unresolved-child', { origin: 'subagent', parentSession: 'outside' }), inheritedEventCount: 0, events: [callEvent(0, 'outside-call')] },
  ]);
  try {
    await f.capture.ready; await f.capture.flush();
    const rows = f.requests[0].payload.sessions;
    assert.equal(rows.length, 1);
    assert.equal(rows[0].session_id, 'parent'); assert.equal(rows[0].origin_session_id, 'parent');
    assert.equal(rows[0].executor_session_id, 'child'); assert.equal(rows[0].parent_session_id, 'parent');
    assert.ok(!JSON.stringify(rows).includes('outside-call'));
  } finally { f.capture(); }
});

test('ingest failure retries stable event IDs and does not advance away from queued history', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'call'), resultEvent(1, 'call', { exitCode: 0 })] }]);
  try {
    await f.capture.ready; f.setFailure(true); await f.capture.flush();
    assert.equal(f.capture.status().pending, 2);
    f.setFailure(false); await f.capture.flush();
    assert.equal(f.capture.status().pending, 0);
    assert.deepEqual(f.requests[0].payload, f.requests[1].payload);
  } finally { f.capture(); }
});

test('tools execute returns next result immediately and preserves caller signal and exact cwd snapshot', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    const signal = new AbortController().signal, value = { opaque: 'original' }, execution = { agent: { id: 'root' }, callId: 'live', rootCallId: 'root-live', name: 'ops_bash', arguments: cli(), signal };
    let nextCalls = 0;
    const result = f.listeners.get('tools/execute')(execution, () => { nextCalls++; return value; });
    assert.strictEqual(result, value); assert.equal(nextCalls, 1); assert.strictEqual(execution.signal, signal); assert.equal(f.requests.length, 0);
    f.sessions.get('root').effectiveCwd = 'C:/other';
    f.listeners.get('tools/result')(execution, { isError: false, value: { exitCode: 0 } });
    await f.capture.flush();
    assert.deepEqual(f.events().map((event) => event.phase), ['started', 'completed']);
    assert.ok(f.events().every((event) => event.cwd === PROJECT));
  } finally { f.capture(); }
});

test('job output closes only the known originating operation and ignores partial or cross-session job handles', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }, { session: header('other'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    const execution = { agent: { id: 'root' }, callId: 'job-call', name: 'ops_bash', arguments: cli() };
    f.listeners.get('tools/execute')(execution, () => undefined);
    f.listeners.get('tools/result')(execution, { isError: false, value: { kind: 'promoted', jobId: 'job-1' } });
    f.listeners.get('tools/result')({ agent: { id: 'root' }, callId: 'poll', name: 'job_output', arguments: { job_id: 'job-1' } }, { isError: false, value: { status: 'running' } });
    f.listeners.get('tools/result')({ agent: { id: 'other' }, callId: 'wrong', name: 'job_output', arguments: { job_id: 'job-1' } }, { isError: false, value: { status: 'completed', exitCode: 0 } });
    await f.capture.flush();
    assert.deepEqual(f.events().map((event) => event.phase), ['started', 'queued']);
    f.listeners.get('tools/result')({ agent: { id: 'root' }, callId: 'done', name: 'job_output', arguments: { job_id: 'job-1' } }, { isError: false, value: { status: 'completed', exitCode: 0 } });
    await f.capture.flush();
    assert.equal(f.events().at(-1).phase, 'completed'); assert.equal(f.events().at(-1).call_id, 'job-call');
  } finally { f.capture(); }
});

test('bounded queue resumes historical replay without dropping events during backend failure', async () => {
  const events = [callEvent(0, 'one'), resultEvent(1, 'one', { exitCode: 0 }), callEvent(2, 'two'), resultEvent(3, 'two', { exitCode: 0 })];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }], { maxPending: 2, batchSize: 1 });
  try {
    await f.capture.ready; f.setFailure(true); await f.capture.flush(); assert.equal(f.capture.status().pending, 2);
    f.setFailure(false);
    for (let index = 0; index < 5; index++) await f.capture.flush();
    const acknowledged = f.requests.slice(1).flatMap((request) => request.payload.sessions.flatMap((row) => row.events));
    assert.deepEqual(acknowledged.map((event) => event.seq), [0, 1, 2, 3]);
    assert.equal(f.capture.status().pending, 0);
  } finally { f.capture(); }
});

test('historical job polling joins the original known job and unknown result remains observed', async () => {
  const events = [callEvent(0, 'original'), resultEvent(1, 'original', { kind: 'background', jobId: 'known-job' }), callEvent(2, 'poll', { job_id: 'known-job' }, 'job_output'), resultEvent(3, 'poll', { status: 'completed', exitCode: 0 }), callEvent(4, 'unknown'), resultEvent(5, 'unknown', { truncated: true })];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.deepEqual(f.events().map((event) => [event.call_id, event.phase]), [['original', 'observed'], ['original', 'queued'], ['original', 'completed'], ['unknown', 'observed'], ['unknown', 'observed']]);
  } finally { f.capture(); }
});

test('quality pending exit is an executed check without fabricated failure or visual pass', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'quality', cli('check-job')), resultEvent(1, 'quality', { exitCode: 2 })] }]);
  try {
    await f.capture.ready; await f.capture.flush();
    const event = f.events().at(-1);
    assert.equal(event.phase, 'completed'); assert.equal(event.exit_code, 2); assert.equal('verdict' in event, false);
  } finally { f.capture(); }
});

test('session status changes ingest empty events once, clear on idle, and never invent a title', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    f.listeners.get('api-session/status')('root', true);
    f.listeners.get('agent/status')({ agent: { id: 'root' }, status: 'running' });
    await f.capture.flush();
    assert.equal(f.requests.length, 1);
    assert.deepEqual(f.requests[0].payload.sessions[0].events, []);
    assert.equal(f.requests[0].payload.sessions[0].status, 'running');
    assert.equal('title' in f.requests[0].payload.sessions[0], false);
    f.listeners.get('api-session/status')('root', true); await f.capture.flush(); assert.equal(f.requests.length, 1);
    f.listeners.get('agent/status')({ agent: { id: 'root' }, status: 'idle' });
    f.setFailure(true); await f.capture.flush(); assert.equal(f.capture.status().status_pending, 1);
    f.setFailure(false); await f.capture.flush(); assert.equal(f.capture.status().status_pending, 0);
    assert.equal(f.requests.at(-1).payload.sessions[0].status, 'idle');
  } finally { f.capture(); }
});

test('running descendants keep origin running until all observed executors idle', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }, { session: header('child', { origin: 'subagent', parentSession: 'root' }), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    f.listeners.get('api-session/status')('root', true);
    f.listeners.get('api-session/status')('child', true);
    f.listeners.get('api-session/status')('root', false);
    await f.capture.flush(); assert.equal(f.requests.at(-1).payload.sessions[0].status, 'running');
    f.listeners.get('session/event')(f.sessions.get('child'), { type: 'turn/end', seq: 10, time: 2000, data: {} });
    await f.capture.flush(); assert.equal(f.requests.at(-1).payload.sessions[0].status, 'idle');
    assert.equal(f.requests.at(-1).payload.sessions[0].session_id, 'root');
    f.listeners.get('api-session/status')('outside', true);
    await f.capture.flush(); assert.equal(f.requests.length, 2);
  } finally { f.capture(); }
});

test('status updated during in-flight ingest survives acknowledgement of the old batch', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    const request = f.backend.request.bind(f.backend);
    let duringFirst = true;
    f.backend.request = async (...args) => {
      const result = await request(...args);
      if (duringFirst) { duringFirst = false; f.listeners.get('api-session/status')('root', false); }
      return result;
    };
    f.listeners.get('api-session/status')('root', true); await f.capture.flush();
    assert.equal(f.capture.status().status_pending, 1);
    await f.capture.flush();
    assert.deepEqual(f.requests.map((entry) => entry.payload.sessions[0].status), ['running', 'idle']);
  } finally { f.capture(); }
});

test('optional official sessionController seeds running state without replaying old turn status', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [{ type: 'turn/end', seq: 0, time: 1000, data: {} }] }]);
  f.ctx.get = (name) => name === 'sessionController' ? { async list() { return { items: [{ sessionId: 'root', cwd: PROJECT, running: true }] }; } } : undefined;
  try {
    await f.capture.ready; await f.capture.flush();
    assert.equal(f.requests.at(-1).payload.sessions[0].status, 'running');
    assert.deepEqual(f.requests.at(-1).payload.sessions[0].events, []);
    await f.capture.flush(); assert.equal(f.requests.length, 1);
  } finally { f.capture(); }
});

test('header outside, live effective cwd project: include exact live match and never read unrelated history', async () => {
  const f = fixture([
    { session: header('moved-parent', { cwd: 'C:/initial' }), effectiveCwd: PROJECT, inheritedEventCount: 0, events: [] },
    { session: header('moved-child', { cwd: 'C:/initial', origin: 'subagent', parentSession: 'moved-parent' }), effectiveCwd: PROJECT, inheritedEventCount: 0, events: [callEvent(0, 'explicit'), callEvent(1, 'ambiguous', { command: 'python bin/it.py render runs/wrong' }), resultEvent(2, 'explicit', { exitCode: 0 })] },
    { session: header('foreign', { cwd: 'C:/foreign' }), effectiveCwd: 'C:/foreign', inheritedEventCount: 0, events: [callEvent(0, 'not-project')] },
  ]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.ok(f.readIds.includes('moved-parent')); assert.ok(f.readIds.includes('moved-child')); assert.equal(f.readIds.includes('foreign'), false);
    assert.deepEqual(f.events().map((event) => event.call_id), ['explicit', 'explicit']);
    assert.equal(f.requests[0].payload.sessions[0].session_id, 'moved-parent');
    assert.equal(f.requests[0].payload.sessions[0].executor_session_id, 'moved-child');
    assert.equal(f.capture.status().known_sessions, 2); assert.equal(f.capture.status().history_candidates, 2); assert.equal(f.capture.status().recorded_calls, 1);
    const exec = { agent: { id: 'moved-child' }, callId: 'live', name: 'ops_bash', arguments: cli() };
    f.listeners.get('tools/execute')(exec, () => undefined); await f.capture.flush();
    assert.equal(f.events().at(-1).phase, 'started');
  } finally { f.capture(); }
});

test('metadata comparison handles Windows slash and case spelling without querying foreign bodies', async () => {
  const f = fixture([{ session: header('spelling', { cwd: 'c:\\PROJECT\\' }), effectiveCwd: 'C:/other', inheritedEventCount: 0, events: [callEvent(0, 'spelling-call')] }]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.equal(f.events()[0].call_id, 'spelling-call'); assert.equal(f.capture.status().history_candidates, 1);
  } finally { f.capture(); }
});

const assistantEvent = (seq, text, turn = 1, blocks = []) => ({ seq, time: 1000 + seq, type: 'assistant/message', data: { turn, step: seq, message: { role: 'assistant', content: [...blocks, { type: 'text', text }] } } });
const emittedHighlights = f => f.requests.flatMap(request => request.payload.sessions.flatMap(row => (row.highlights || []).map(highlight => ({ ...highlight, origin: row.session_id }))));
const writeCall = (seq, id, root = 'runs/one', turn = 1) => ({ ...callEvent(seq, id, { file_path: root + '/manifest.json', content: '{}' }, 'write'), data: { ...callEvent(seq, id, { file_path: root + '/manifest.json', content: '{}' }, 'write').data, turn } });

test('important sections remain verbatim, text-only, bounded and exclude broadcasts, secrets and commands', () => {
  const message = text => ({ role: 'assistant', content: [{ type: 'reasoning', text: 'api_key=do-not-observe' }, { type: 'text', text }] });
  const text = '我会先读取图片。\n\n## 识别结果\n原图标题已确认。\n\n## 译文\n地图导航。\n\n## 审核结论\n右侧字距仍需调整。\n\n## 最终结果\n第四段不纳入。';
  const selected = selectWorkflowHighlights(message(text));
  assert.equal(selected.length, 3);
  assert.deepEqual(selected.map(item => item.text), ['## 识别结果\n原图标题已确认。', '## 译文\n地图导航。', '## 审核结论\n右侧字距仍需调整。']);
  for (const body of ['我会先检查。', 'I will check the output.', '准备运行命令。', '正在检查。', '```json\n{"x":1}\n```', '{"x": [1, 2]}', 'python bin/it.py render runs/one', 'api_key=abcdefghijklmnop', 'Bearer abcdefghijklmnop', 'runs/one/a.png\nruns/one/b.png']) assert.deepEqual(selectWorkflowHighlights(message('## 最终结果\n' + body)), [], body);
  assert.deepEqual(selectWorkflowHighlights(message('普通聊天\n已读取文件。')), []);
  assert.deepEqual(selectWorkflowHighlights({ role: 'user', content: [{ type: 'text', text }] }), []);
  assert.deepEqual(selectWorkflowHighlights(message('## 译文\n' + '字'.repeat(1400))), []);
  const long = selectWorkflowHighlights(message('## 译文\n短句完整。\n\n' + '字'.repeat(1400)));
  assert.equal(long[0].text, '## 译文\n短句完整。');
  assert.ok(('## 译文\n短句完整。\n\n' + '字'.repeat(1400)).includes(long[0].text));
  assert.ok(selected.every(item => item.text.length <= 1200));
});

test('historical assistant output attaches only to a completed unique production context', async () => {
  const events = [writeCall(0, 'write'), resultEvent(1, 'write', { status: 'completed' }), assistantEvent(2, '## 识别结果\n原文已确认。\n\n## 译文\n目标文字。', 1, [{ type: 'reasoning', text: 'private thought' }])];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try {
    await f.capture.ready; await f.capture.flush();
    const highlights = emittedHighlights(f);
    assert.equal(highlights.length, 2);
    assert.ok(highlights.every(item => item.origin === 'root' && item.executor_session_id === 'root' && item.stage === 'semantic.analyze' && item.run_root === PROJECT + '/runs/one'));
    assert.match(highlights[0].event_id, /^summary:[a-f0-9]{64}$/);
    assert.ok(!JSON.stringify(f.requests).includes('private thought'));
    assert.ok(f.requests.every(request => request.payload.sessions.every(row => row.events.every(event => event.phase !== 'summary'))));
  } finally { f.capture(); }
});

test('read/check consumers, failed writes, pending CLI and unknown operations establish no summary ownership', async () => {
  const events = [];
  for (const [index, [name, args, result]] of [
    ['read_image', { file_path: 'runs/one/source.png' }, {}],
    ['ops_bash', cli('check-layered'), { exitCode: 0 }],
    ['ops_bash', cli('packet'), { exitCode: 0 }],
    ['write', { file_path: 'runs/one/manifest.json' }, { status: 'rejected' }],
    ['ops_bash', cli(), { truncated: true }],
  ].entries()) events.push(callEvent(index * 3, 'consumer-' + index, args, name), resultEvent(index * 3 + 1, 'consumer-' + index, result), assistantEvent(index * 3 + 2, '## 最终结果\n无可信归属不应展示。'));
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try { await f.capture.ready; await f.capture.flush(); assert.deepEqual(emittedHighlights(f), []); }
  finally { f.capture(); }
});

test('multiple runs require an exact known root; foreign, unknown and multiple root references are rejected', async () => {
  const events = [writeCall(0, 'a', 'runs/one'), resultEvent(1, 'a', {}), writeCall(2, 'b', 'runs/two'), resultEvent(3, 'b', {}),
    assistantEvent(4, '## 译文\n不能猜测目标。'),
    assistantEvent(5, '对应 runs/one。\n\n## 译文\n只属于第一个工作包。'),
    assistantEvent(6, '对应 runs/missing。\n\n## 译文\n未知目录不纳入。'),
    assistantEvent(7, '对应 runs/one 与 runs/two。\n\n## 译文\n跨工作包不纳入。'),
    assistantEvent(8, '对应 D:/outside/runs/one。\n\n## 译文\n外部同名不纳入。'),
    assistantEvent(9, '对应 ' + PROJECT.toUpperCase() + '/runs/one。\n\n## 译文\n绝对路径精确匹配。')];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try { await f.capture.ready; await f.capture.flush(); const highlights = emittedHighlights(f); assert.equal(highlights.length, 2); assert.ok(highlights.every(item => item.run_root.endsWith('/runs/one'))); }
  finally { f.capture(); }
});

test('different turns never borrow implicit context and multiple stages require explicit root plus supported section', async () => {
  const events = [writeCall(0, 'write'), resultEvent(1, 'write', {}), assistantEvent(2, '## 译文\n后续回合不能隐式继承。', 2),
    assistantEvent(3, '对应 runs/one。\n\n## 译文\n明确引用已有制作工作包。', 2), callEvent(4, 'render'), resultEvent(5, 'render', { exitCode: 0 }),
    assistantEvent(6, '## 最终结果\n多阶段不得猜测。'), assistantEvent(7, '对应 runs/one。\n\n## 最终结果\n合成结果已生成，视觉待审。')];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try { await f.capture.ready; await f.capture.flush(); const highlights = emittedHighlights(f); assert.deepEqual(highlights.map(item => item.stage), ['semantic.analyze', 'basic.render']); }
  finally { f.capture(); }
});

test('live assistant events use exact executor lineage and do not borrow another executor production', async () => {
  const snapshots = [{ session: header('root'), inheritedEventCount: 0, events: [] }, { session: header('child', { origin: 'subagent', parentSession: 'root' }), inheritedEventCount: 0, events: [] }];
  const f = fixture(snapshots);
  try {
    await f.capture.ready;
    const emit = (id, event) => f.listeners.get('session/event')(f.sessions.get(id), event);
    emit('root', writeCall(0, 'root-write')); emit('root', resultEvent(1, 'root-write', {}));
    emit('child', assistantEvent(0, '对应 runs/one。\n\n## 译文\n不能借用父执行器操作。'));
    emit('child', writeCall(1, 'child-write', 'runs/two')); emit('child', resultEvent(2, 'child-write', {}));
    emit('child', assistantEvent(3, '## 译文\n子执行器自己的定稿。'));
    await f.capture.flush(); const highlights = emittedHighlights(f);
    assert.equal(highlights.length, 1); assert.equal(highlights[0].origin, 'root'); assert.equal(highlights[0].executor_session_id, 'child'); assert.equal(highlights[0].run_root, PROJECT + '/runs/two');
  } finally { f.capture(); }
});

test('fork-inherited summaries are skipped and live replay retains stable summary ids', async () => {
  const own = [writeCall(3, 'write'), resultEvent(4, 'write', {}), assistantEvent(5, '## 审核结论\n当前源字节仍需复核。')];
  const inherited = [writeCall(0, 'inherited-write'), resultEvent(1, 'inherited-write', {}), assistantEvent(2, '## 译文\ninherited-body')];
  const make = () => fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }, { session: header('child', { origin: 'subagent', parentSession: 'root' }), inheritedEventCount: 3, events: [...inherited, ...own] }]);
  const f = make(), second = make();
  try {
    await f.capture.ready; await f.capture.flush(); await second.capture.ready; await second.capture.flush();
    assert.equal(emittedHighlights(f).length, 1); assert.ok(!JSON.stringify(f.requests).includes('inherited-body'));
    assert.equal(emittedHighlights(f)[0].event_id, emittedHighlights(second)[0].event_id);
    f.listeners.get('session/event')(f.sessions.get('child'), own.at(-1)); await f.capture.flush(); assert.equal(emittedHighlights(f).length, 1);
  } finally { f.capture(); second.capture(); }
});

test('highlight overflow resumes without truncation, reordering production ownership or duplicate acknowledged text', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [writeCall(0, 'write'), resultEvent(1, 'write', {}), assistantEvent(2, '## 识别结果\n第一段。\n\n## 译文\n第二段。\n\n## 审核结论\n第三段。')] }], { maxPending: 2, batchSize: 1 });
  try {
    await f.capture.ready;
    for (let index = 0; index < 12; index++) await f.capture.flush();
    const highlights = emittedHighlights(f); assert.equal(highlights.length, 3); assert.equal(new Set(highlights.map(item => item.event_id)).size, 3); assert.equal(f.capture.status().pending, 0);
    assert.ok(f.requests.findIndex(request => request.payload.sessions.some(row => row.events.some(event => event.phase === 'completed'))) < f.requests.findIndex(request => request.payload.sessions.some(row => row.highlights?.length)));
  } finally { f.capture(); }
});

test('foreign cwd barriers invalidate implicit summaries and preserve only a paragraph explicitly citing a trusted project run', async () => {
  for (const [tool, args] of [['working_directory', { cd: 'D:/other-project' }], ['ops_bash', { command: 'python bin/it.py render runs/foreign', workdir: 'D:/other-project' }]]) {
    const events = [writeCall(0, 'write'), resultEvent(1, 'write', {}), callEvent(2, 'foreign', args, tool),
      assistantEvent(3, '## 最终结果\n外项目的最终结果不应归入旧工作包。'),
      assistantEvent(4, '对应 runs/one。\n\n## 译文\n前文提及本项目不能认领这一段。'),
      assistantEvent(5, '## 译文\n\nruns/one：本项目定稿。\n\n另一个项目的结论不能纳入。')];
    const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
    try { await f.capture.ready; await f.capture.flush(); const highlights = emittedHighlights(f); assert.equal(highlights.length, 1, tool); assert.equal(highlights[0].text, 'runs/one：本项目定稿。'); }
    finally { f.capture(); }
  }
});

test('a real live execution cwd outside the project blocks old implicit production context', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    const emit = event => f.listeners.get('session/event')(f.sessions.get('root'), event);
    emit(writeCall(0, 'write')); emit(resultEvent(1, 'write', {}));
    f.sessions.get('root').effectiveCwd = 'D:/outside';
    emit(callEvent(2, 'read', { file_path: 'runs/outside/source.png' }, 'read_image'));
    emit(assistantEvent(3, '## 审核结论\n外项目结果不可附到旧run。'));
    emit(assistantEvent(4, '## 译文\n\nruns/one：明确引用仍属于已知项目工作包。'));
    await f.capture.flush();
    assert.equal(emittedHighlights(f).length, 1); assert.equal(emittedHighlights(f)[0].text, 'runs/one：明确引用仍属于已知项目工作包。');
  } finally { f.capture(); }
});

test('failed highlight ingestion retries the identical stable batch and preserves the pending evidence', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [writeCall(0, 'write'), resultEvent(1, 'write', {}), assistantEvent(2, '## 译文\n重试保留原文。')] }]);
  try {
    await f.capture.ready; f.setFailure(true); await f.capture.flush(); assert.equal(f.capture.status().pending, 5);
    f.setFailure(false); await f.capture.flush(); assert.deepEqual(f.requests[0].payload, f.requests[1].payload); assert.equal(f.capture.status().pending, 0);
  } finally { f.capture(); }
});

test('native file producers use published path fields while rejected results remain failed', () => {
  for (const name of ['ops_write_file', 'ops_edit_file']) {
    const parsed = parseWorkflowTool(name, { path: 'runs/one/manifest.json' }, { cwd: PROJECT, project: PROJECT });
    assert.equal(parsed.stage, 'semantic.analyze'); assert.deepEqual(parsed.output_paths, [PROJECT + '/runs/one/manifest.json']);
    assert.equal(parseWorkflowTool(name, { path: 'runs/one/manifest.json', dryRun: true }, { cwd: PROJECT, project: PROJECT }), undefined);
  }
  assert.equal(summarizeWorkflowResult({ value: { status: 'rejected' } }).phase, 'failed');
});

test('a live completion arriving before backfill cannot lend future ownership to earlier assistant text', async () => {
  const events = [assistantEvent(0, '## 译文\n生产以前不应归属。'), writeCall(1, 'write'), resultEvent(2, 'write', {}), assistantEvent(3, '## 译文\n生产以后的原文。')];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try {
    f.listeners.get('session/event')(f.sessions.get('root'), events[1]); f.listeners.get('session/event')(f.sessions.get('root'), events[2]);
    await f.capture.ready; await f.capture.flush(); assert.deepEqual(emittedHighlights(f).map(item => item.text), ['## 译文\n生产以后的原文。']);
  } finally { f.capture(); }
});

test('native writes need a declared completed receipt before their text gains production context', async () => {
  const events = [callEvent(0, 'unknown', { path: 'runs/one/manifest.json' }, 'ops_write_file'), resultEvent(1, 'unknown', {}), assistantEvent(2, '## 译文\n未知回执不能建立归属。'), callEvent(3, 'known', { path: 'runs/one/manifest.json' }, 'ops_write_file'), resultEvent(4, 'known', { status: 'completed' }), assistantEvent(5, '## 译文\n成功写入后的定稿。')];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  try { await f.capture.ready; await f.capture.flush(); assert.deepEqual(emittedHighlights(f).map(item => item.text), ['## 译文\n成功写入后的定稿。']); }
  finally { f.capture(); }
});

const activities = (f) => f.requests.flatMap(request => request.payload.sessions.flatMap(row => row.activities || []));

test('rootless project conversations display explicit stages and important results without tool ownership', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [assistantEvent(0, '当前阶段：实现会话控制'), assistantEvent(1, '## 最终结果\n已补充会话实时进度。')] }]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.deepEqual(activities(f).map(item => item.type), ['stage', 'stage', 'result']);
    assert.equal(activities(f)[0].stage, '实现会话控制');
    assert.ok(activities(f).every(item => !('run_root' in item)));
    assert.deepEqual(f.events(), []); assert.deepEqual(emittedHighlights(f), []);
  } finally { f.capture(); }
});

test('conversation todo plans require a successful committed receipt and preserve exact statuses', async () => {
  const todos = [{ content: '实现会话控制', status: 'in_progress' }, { content: '验证界面', status: 'pending' }];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'todo', { todos }, 'todo_write'), resultEvent(1, 'todo', {}), callEvent(2, 'bad', { todos }, 'todo_write'), resultEvent(3, 'bad', {}, true)] }]);
  try {
    await f.capture.ready; await f.capture.flush();
    assert.equal(activities(f).length, 1); assert.equal(activities(f)[0].type, 'plan');
    assert.deepEqual(activities(f)[0].items, todos); assert.equal(activities(f)[0].seq, 1);
    const before = activities(f)[0].event_id;
    f.listeners.get('session/event')(f.sessions.get('root'), resultEvent(1, 'todo', {})); await f.capture.flush();
    assert.equal(activities(f).length, 1); assert.equal(activities(f)[0].event_id, before);
    assert.deepEqual(f.events(), []);
  } finally { f.capture(); }
});

test('activity capture ignores reasoning, ordinary chatter, inherited sections, child plans and secrets', async () => {
  const todos = [{ content: '子任务步骤', status: 'pending' }];
  const f = fixture([
    { session: header('root'), inheritedEventCount: 1, events: [assistantEvent(0, '## 继承步骤'), assistantEvent(1, '我会检查一下。'), assistantEvent(2, '## 结果\napi_key: private-value'), assistantEvent(3, '## 文件\nC:/private/password.txt')] },
    { session: header('child', { origin: 'subagent', parentSession: 'root' }), inheritedEventCount: 0, events: [callEvent(0, 'todo-child', { todos }, 'todo_write'), resultEvent(1, 'todo-child', {})] },
  ]);
  try { await f.capture.ready; await f.capture.flush(); assert.deepEqual(activities(f), []); }
  finally { f.capture(); }
});

test('live lifecycle ends remain observed status and do not mark conversation plans completed', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [] }]);
  try {
    await f.capture.ready;
    f.listeners.get('session/event')(f.sessions.get('root'), { type: 'turn/start', seq: 0, time: 1000, data: { turn: 1 } });
    f.listeners.get('session/event')(f.sessions.get('root'), { type: 'turn/end', seq: 1, time: 1001, data: { turn: 1, reason: { kind: 'blocked' } } });
    await f.capture.flush();
    assert.ok(activities(f).some(item => item.status === 'blocked'));
    assert.equal(f.requests.at(-1).payload.sessions[0].status, 'idle');
    assert.deepEqual(f.events(), []);
  } finally { f.capture(); }
});

test('ordinary and forked project origins retain their own conversation activity', async () => {
  const f = fixture([{ session: header('root', { origin: 'fork' }), inheritedEventCount: 0, events: [assistantEvent(0, '计划：验证计划列表')] }]);
  try { await f.capture.ready; await f.capture.flush(); assert.equal(activities(f)[0].status, 'planned'); assert.equal(activities(f)[0].executor_session_id, 'root'); }
  finally { f.capture(); }
});

test('many activity events flush in bounded twenty-record session batches without loss', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: Array.from({ length: 27 }, (_, seq) => assistantEvent(seq, `当前步骤：处理阶段 ${seq}`)) }]);
  try {
    await f.capture.ready; for (let index = 0; index < 3; index++) await f.capture.flush();
    assert.equal(activities(f).length, 27);
    assert.ok(f.requests.every(request => request.payload.sessions.every(row => (row.activities || []).length <= 20)));
    assert.equal(new Set(activities(f).map(item => item.event_id)).size, 27);
  } finally { f.capture(); }
});

test('ingest batches include envelope and session metadata in UTF-8 byte limits', async () => {
  const project = 'C:/' + 'p'.repeat(300);
  const snapshots = Array.from({ length: 32 }, (_, index) => ({ session: header('会'.repeat(240) + index, { cwd: project }), inheritedEventCount: 0, events: [callEvent(0, 'call-' + index, { command: 'python bin/it.py render runs/one', workdir: project })] }));
  const f = fixture(snapshots, { project, maxBatchSessions: 100, serverMaxBodyBytes: 128 * 1024 });
  try {
    await f.capture.ready;
    for (let index = 0; index < 40 && f.capture.status().pending; index++) await f.capture.flush();
    assert.equal(f.capture.status().pending, 0, JSON.stringify(f.capture.status()));
    assert.ok(f.requests.every(request => request.status === 200 && request.bytes <= 96000));
    assert.equal(f.events().length, snapshots.length);
    assert.equal(new Set(f.requests.flatMap(request => request.payload.sessions.map(row => row.executor_session_id))).size, snapshots.length);
  } finally { f.capture(); }
});

test('an oversized individual record remains pending with an explicit local budget error', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'call')] }], { maxBatchBytes: 100 });
  try {
    await f.capture.ready; await f.capture.flush(); await f.capture.flush();
    assert.equal(f.requests.length, 0); assert.equal(f.capture.status().pending, 1);
    assert.equal(f.capture.status().error, 'ingest-record-too-large');
  } finally { f.capture(); }
});

test('145 historical sessions and status updates drain in small bounded batches', async () => {
  const snapshots = Array.from({ length: 145 }, (_, index) => ({ session: header('root-' + index), inheritedEventCount: 0, events: [callEvent(0, 'call-' + index)] }));
  const f = fixture(snapshots);
  try {
    await f.capture.ready;
    for (const snapshot of snapshots) f.listeners.get('api-session/status')(snapshot.session.id, true);
    for (let index = 0; index < 80 && (f.capture.status().pending || f.capture.status().status_pending); index++) await f.capture.flush();
    assert.equal(f.capture.status().pending, 0); assert.equal(f.capture.status().status_pending, 0);
    assert.equal(f.events().length, 145); assert.equal(activities(f).length, 145);
    assert.ok(f.requests.every(request => request.payload.sessions.length <= 8 && request.bytes <= 96000));
    assert.equal(new Set(f.requests.flatMap(request => request.payload.sessions.map(row => row.session_id))).size, 145);
  } finally { f.capture(); }
});

test('mixed event, highlight and activity batches retry unchanged with safe rejection reasons', async () => {
  const events = [writeCall(0, 'write'), resultEvent(1, 'write', { status: 'completed' }), assistantEvent(2, '## 译文\n本次生产后的定稿。')];
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events }]);
  const original = f.backend.request;
  let reject = true;
  f.backend.request = async (...args) => { const response = await original(...args); return reject ? { status: 413, body: Buffer.from(JSON.stringify({ error: 'Workflow ledger budget exceeded' })) } : response; };
  try {
    await f.capture.ready; await f.capture.flush();
    assert.equal(f.capture.status().error, 'ingest-unavailable'); assert.equal(f.capture.status().error_reason, 'ledger-budget');
    const queued = f.capture.status().pending; assert.ok(queued > 0);
    reject = false; await f.capture.flush();
    assert.deepEqual(f.requests[0].payload, f.requests[1].payload);
    assert.equal(f.capture.status().pending, 0); assert.equal(f.capture.status().error_reason, undefined);
    assert.ok(emittedHighlights(f).length > 0); assert.ok(activities(f).length > 0);
  } finally { f.capture(); }
});

test('capture reports fixed body and transport reasons without echoing arbitrary error text', async () => {
  for (const [reply, reason] of [
    [{ status: 413, body: Buffer.from('{"error":"Request body too large or empty"}') }, 'body-budget'],
    [{ status: 409, body: Buffer.from('{"error":"Workflow ledger unavailable or exceeds budget"}') }, 'ledger-unavailable'],
    [{ status: 500, body: Buffer.from('{"error":"Bearer PRIVATE credentials"}') }, 'http-500'],
    [Object.assign(new Error('Bearer PRIVATE credentials'), { code: 'ETIMEDOUT' }), 'etimedout'],
  ]) {
    const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'call')] }]);
    f.backend.request = async () => { if (reply instanceof Error) throw reply; return reply; };
    try {
      await f.capture.ready; await f.capture.flush();
      assert.equal(f.capture.status().error_reason, reason); assert.equal(f.capture.status().pending, 1);
      assert.ok(!JSON.stringify(f.capture.status()).includes('PRIVATE'));
    } finally { f.capture(); }
  }
});

test('dispose aborts transport, removes listeners and timers, and stops further capture', async () => {
  const f = fixture([{ session: header('root'), inheritedEventCount: 0, events: [callEvent(0, 'call')] }]);
  await f.capture.ready; await f.capture.flush();
  const signal = f.requests[0].signal;
  f.capture(); f.capture();
  assert.equal(signal.aborted, true); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0);
  await f.capture.flush(); assert.equal(f.requests.length, 1);
});
