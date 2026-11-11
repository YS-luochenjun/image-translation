import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { test } from 'node:test';

const source = await readFile(new URL('./client.js', import.meta.url), 'utf8');
test('open navigation passes the official SessionTarget string', () => {
  assert.match(source, /ctx\.uiWorkspace\.openSession\(controlSessionId\)/);
  assert.doesNotMatch(source, /openSession\(\{\s*kind:\s*'session'/);
});
const plain = (value) => JSON.parse(JSON.stringify(value));
function load(react = { createElement() {} }, globals = {}) {
  let entry;
  const storage = new Map();
  const window = { __ModuleLoader__: { load(value) { entry = value; } },
    sessionStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    addEventListener() {}, removeEventListener() {} };
  vm.runInNewContext(source, { window, AbortController, setTimeout, clearTimeout, setInterval: () => 1, clearInterval() {}, crypto: { randomUUID: () => 'request-fixed' }, ...globals });
  return { entry, plugin: entry.factory(() => react), storage };
}
const { plugin, entry } = load();
const api = plugin.__test;
const hash = 'a'.repeat(64);

// The catalog is a project vocabulary, not evidence that a stage was planned.
test('catalog labels enrich only the route nodes the server actually planned', () => {
  const catalog = [{ id: 'semantic.analyze', title: '识别与分析' }, { id: 'job.freeze', title: '冻结任务' }];
  assert.deepEqual(plain(api.workflowNodes({ route: 'basic', nodes: [] }, catalog)), []);
  const nodes = api.workflowNodes({ route: 'basic', nodes: [{ id: 'semantic.analyze', status: 'planned' }] }, catalog);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].title, '识别与分析');
  assert.equal(nodes[0].status, 'planned');
  assert.equal(nodes[0].artifacts.length, 0);
  const observed = api.workflowNodes({ nodes: [{ id: 'custom.node', title: '实际步骤', status: 'failed', reason: 'actual failure' }] }, catalog);
  assert.equal(observed[0].id, 'custom.node');
  assert.equal(observed[0].reason, 'actual failure');
});

test('artifact URLs remain same-origin and project-scoped', () => {
  assert.equal(api.assetUrl('/local-image-review/workflow/artifact?session_id=s&artifact_id=a'), '/local-image-review/workflow/artifact?session_id=s&artifact_id=a');
  assert.equal(api.assetUrl('/api/assets?path=one.png'), '/local-image-review/image?path=one.png');
  for (const value of ['https://evil.test/image.png', '//evil.test/image.png', 'javascript:alert(1)', '/unrelated/file', null]) assert.equal(api.assetUrl(value), '');
  assert.equal(api.canTarget({ cwd: api.PROJECT, origin: 'fork' }), true);
  assert.equal(api.canTarget({ cwd: api.PROJECT, origin: 'subagent' }), false);
  assert.equal(api.canTarget({ cwd: api.PROJECT + '-other' }), false);
});

test('saved comment identity rejects wrong session/run/node/hash/request and unexpected scope', () => {
  const expected = { session_id: 'owner', run_id: 'run', node_id: 'node', artifact_id: 'artifact', artifact_sha256: hash, region_id: 'region', bbox: [.1, .2, .3, .4], note: '调整字层', request_id: 'same-request' };
  const result = { session_id: 'owner', comment: { id: 'comment', ...expected } };
  assert.equal(api.validComment(result, expected), true);
  assert.equal(api.validComment({ ...result, session_id: 'other' }, expected), false);
  for (const key of ['session_id', 'run_id', 'node_id', 'artifact_id', 'artifact_sha256', 'region_id', 'request_id', 'note']) {
    assert.equal(api.validComment({ ...result, comment: { ...result.comment, [key]: 'wrong' } }, expected), false, key);
  }
  assert.equal(api.validComment({ ...result, comment: { ...result.comment, bbox: [0, 0, .3, .4] } }, expected), false);
  const nodeOnly = { session_id: 'owner', run_id: 'run', node_id: 'node', request_id: 'q', note: '节点意见' };
  assert.equal(api.validComment({ session_id: 'owner', comment: { id: 'c', ...nodeOnly, artifact_id: 'unexpected' } }, nodeOnly), false);
});

test('pixel regions normalize once and explicitly normalized artifact regions stay normalized', () => {
  assert.deepEqual(plain(api.normalizedBox([10, 20, 40, 30], 100, 100)), [.1, .2, .4, .3]);
  assert.deepEqual(plain(api.normalizedBox([-10, -10, 40, 40], 100, 100)), [0, 0, .3, .3]);
  const region = { coordinate_space: 'source_normalized', bbox: [.1, .2, .4, .3], bbox_pixels: [100, 200, 400, 300] };
  assert.deepEqual(plain(api.regionBox(region, 1000, 1000)), region.bbox);
  assert.deepEqual(plain(api.regionBox({ bbox: [100, 200, 400, 300] }, 1000, 1000)), region.bbox);
  assert.equal(api.normalizedBox([0, 0, 0, 10], 100, 100), null);
  assert.equal(api.normalizedBox([200, 200, 10, 10], 100, 100), null);
  assert.equal(api.normalizedBox([0, Infinity, 10, 10], 100, 100), null);
  assert.equal(api.regionBox({ coordinate_space: 'source_normalized', bbox: [0, 0, 2, .3] }, 100, 100), null);
});

test('free selections clamp to canvas, support reverse drags and keep a point inside the edge', () => {
  const reverse = api.selectionBox([.8, .9], [.2, .3], [100, 200]);
  assert.ok(Math.abs(reverse.bbox[0] - .2) < 1e-12);
  assert.ok(Math.abs(reverse.bbox[2] - .6) < 1e-12);
  assert.equal(reverse.isPoint, false);
  assert.deepEqual(plain(api.selectionBox([-2, -2], [2, 2], [100, 100]).bbox), [0, 0, 1, 1]);
  const edge = api.selectionBox([1, 1], [1, 1], [100, 200]);
  assert.equal(edge.isPoint, true);
  assert.deepEqual(plain(edge.bbox), [.99, .995, .01, .005]);
  assert.ok(edge.bbox[0] + edge.bbox[2] <= 1);
  assert.ok(edge.bbox[1] + edge.bbox[3] <= 1);
  assert.equal(api.selectionBox([NaN, 0], [1, 1], [100, 100]), null);
});

test('multi-language results require a choice and never default to historical outputs', () => {
  const ja = { id: 'ja', kind: 'image', sha256: hash }, zh = { id: 'zh', kind: 'image', sha256: 'b'.repeat(64) };
  const run = { results: [ja, zh], result: null, nodes: [{ id: 'result', artifacts: [{ id: 'old', kind: 'image', sha256: 'c'.repeat(64) }] }] };
  assert.equal(api.selectedResult(run, ''), null);
  assert.equal(api.selectedResult(run, 'missing'), null);
  assert.equal(api.selectedResult(run, 'zh').id, 'zh');
  assert.equal(api.selectedResult({ ...run, results: [] }, ''), null);
  assert.equal(api.selectedResult({ results: [ja], result: ja }, '').id, 'ja');
  assert.equal(api.selectedResult({ ...run, result: zh }, '').id, 'zh');
  assert.equal(api.resultCandidates({ ...run, results: [ja, ja] }).length, 1);
});

test('comment payload keeps the captured owner when another main session is selected', () => {
  const draft = { session_id: 'original', run: { id: 'original-run' }, node: { id: 'lettering.render' }, artifact: { id: 'layer', sha256: hash }, bbox: [.1, .2, .3, .4], note: '  改小字距  ', request_id: 'stable-request' };
  const otherMainSession = { session_id: 'other', run: { id: 'other-run' } };
  const payload = api.commentPayload(draft, otherMainSession);
  assert.equal(payload.session_id, 'original');
  assert.equal(payload.run_id, 'original-run');
  assert.equal(payload.request_id, 'stable-request');
  assert.equal(payload.note, '改小字距');
  draft.bbox[0] = .9;
  assert.equal(payload.bbox[0], .1);
});

test('metadata polling retains expanded history only for the same id and root', () => {
  const metadata = { id: 'old-a', root: 'runs/a', title: '历史流程', route: 'basic' };
  const detail = { ...metadata, nodes: [{ id: 'source.normalize', artifacts: [{ id: 'image', url: '/local-image-review/workflow/artifact?session_id=__unassigned__' }] }], results: [{ id: 'output' }] };
  const merged = api.mergeUnassignedRun({ ...metadata, nodes: [] }, detail);
  assert.equal(merged.nodes[0].artifacts[0].id, 'image');
  assert.equal(merged.results[0].id, 'output');
  assert.equal(merged.session_id, undefined);
  assert.equal(api.mergeUnassignedRun({ ...metadata, id: 'other' }, detail).nodes, undefined);
  assert.equal(api.mergeUnassignedRun({ ...metadata, root: 'runs/other' }, detail).nodes, undefined);
  const refreshed = api.mergeWorkflowSnapshot({ sessions: [{ session_id: 'current' }], unassigned: [metadata] }, { unassigned: [detail] });
  assert.equal(refreshed.unassigned[0].nodes[0].id, 'source.normalize');
  assert.equal(refreshed.sessions[0].session_id, 'current');
  assert.equal(api.mergeWorkflowSnapshot({ sessions: [], unassigned: [] }, { unassigned: [detail] }).unassigned.length, 0);
});

test('session titles use official metadata without requiring its creation cwd', () => {
  const row = { session_id: 'session-12345678-1234-1234-1234-123456789abc', title: 'session-12345678-1234-1234-1234-123456789abc' };
  assert.equal(api.sessionTitle(row, { [row.session_id]: { cwd: 'C:/other', displayTitle: '正在翻译的图片' } }), '正在翻译的图片');
  assert.equal(api.sessionTitle(row, {}), '会话 12345678');
  assert.equal(api.routeLabel('basic'), '标准制作');
  assert.equal(api.routeLabel('layered'), '分层制作');
  assert.equal(api.routeLabel('native'), '分阶段制作');
  assert.equal(api.statusName('idle'), '空闲');
  assert.equal(api.statusName('queued'), '排队中');
});

test('gallery folds equal bytes without changing exact artifact identities or result choices', () => {
  const snapshot = { id: 'snap', kind: 'image', name: hash + '.png', sha256: hash };
  const named = { id: 'named', kind: 'image', name: 'translated.png', sha256: hash };
  const current = { id: 'current', kind: 'image', name: 'translated.png', sha256: hash, capture: 'current' };
  const record = { id: 'json', kind: 'json', name: 'manifest.json', sha256: hash };
  const list = [snapshot, named, current, record];
  const shown = api.galleryArtifacts(list);
  assert.equal(shown.primary.length, 2);
  assert.equal(shown.primary[0], current);
  assert.equal(shown.primary[1], record);
  assert.deepEqual(shown.history.map((item) => item.id).join(','), 'snap,named');
  assert.equal(list.length, 4);
  assert.equal(api.selectedResult({ results: [snapshot, current] }, 'snap'), snapshot);
  assert.equal(api.artifactName({ ...snapshot, source_path: 'runs/a/source.png' }), 'source.png');
  assert.equal(api.artifactName(snapshot), '历史图片');
  assert.equal(api.auxiliary({ id: 'lettering.render', status: 'skipped' }), true);
  assert.equal(api.galleryArtifacts([{ id: 'a', kind: 'text' }, { id: 'b', kind: 'text' }]).primary.length, 2);
});

test('original module ID, dual slot registration, injection and native test selectors survive', () => {
  const registrations = [];
  plugin.apply({ effect(callback) { callback(); }, locale: { register() {}, bind: () => () => '图片流程' }, slots: { inject(name, callback) { callback(); }, register(options, component) { registrations.push({ options, component }); } } });
  assert.equal(entry.id, '@local/image-review-ui');
  assert.deepEqual(plain(plugin.inject), ['slots', 'locale', 'sessions', 'remote', 'remote.session', 'uiWorkspace', 'layout']);
  assert.equal(registrations[0].options.name, 'sidebar.panellist');
  assert.equal(registrations[0].options.id, 'local-image-review-ui');
  assert.equal(registrations[1].options.name, 'main');
  assert.equal(registrations[1].options.key, 'local-image-review-ui');
  for (const selector of ['image-review-workbench', 'workflow-desk', 'session-row', 'workflow-node', 'node-artifact', 'node-comment-dialog', 'workflow-refresh', 'save-result']) assert.ok(source.includes(`'${selector}'`), selector);
  assert.ok(!source.includes("h('iframe'"));
});

test('metadata, empty plans and unknown objects never become readable stages', () => {
  const empty = { paragraphs: [], regions: [], checks: [] };
  assert.deepEqual(plain(api.semanticContent({ schema_version: 1, sha256: hash, canvas: { width: 1200 }, bundle: { secrets: 'do-not-render' }, rows: [] })), empty);
  assert.deepEqual(plain(api.artifactContent({ kind: 'json', preview: '{"schema_version":1,"rows":[]}' })), empty);
  assert.deepEqual(plain(api.artifactContent({ kind: 'json', preview: '{"regions":[' })), empty);
  assert.equal(api.usefulNode({ id: 'route.select', status: 'planned', artifacts: [{ kind: 'json', preview: { rows: [] } }] }), false);
  assert.deepEqual(plain(api.nodeContent({ artifacts: [], highlights: { text: 'unknown shape' }, outputs: { kind: 'text', text: 'unknown shape' } })), empty);
});

test('authoritative recognition and final translation rows override stale manifest previews', () => {
  const stale = { kind: 'json', preview: { regions: [{ id: 'headline', source_text: 'Wrong source', target_text: 'Draft' }] } };
  const recognition = api.nodeContent({ id: 'semantic.analyze', content: { kind: 'recognition', rows: [{ region_id: 'headline', source_text: 'Hello', recognition_verified: false, notes: '小字待确认' }] }, artifacts: [stale] });
  assert.equal(recognition.regions.length, 1);
  assert.equal(recognition.regions[0].id, 'headline');
  assert.equal(recognition.regions[0].source_text, 'Hello');
  assert.equal(recognition.regions[0].target_text, '');
  assert.equal(recognition.regions[0].recognition_verified, false);
  const translation = api.nodeContent({ content: { kind: 'translation', rows: [{ region_id: 'headline', source_text: 'Hello', target_text: 'Draft', final_text: 'こんにちは', language: 'ja' }] }, artifacts: [stale] });
  assert.equal(translation.regions[0].target_text, 'こんにちは');
  assert.equal(stale.preview.regions[0].target_text, 'Draft');
});

test('plain conversation highlights and review observations are useful without images', () => {
  const node = { id: 'review.visual', artifacts: [], highlights: [{ text: '标题边缘仍需修复', event_id: 'e1' }, { text: { nested: 'not text' } }], outputs: [{ kind: 'text', text: '背景保留完整' }, { kind: 'json', text: 'not a text output' }] };
  assert.deepEqual(plain(api.nodeContent(node).paragraphs), ['标题边缘仍需修复', '背景保留完整']);
  assert.equal(api.usefulNode(node), true);
  const review = api.semanticContent({ stage_notes: { background: '主体没有可见变化', framework: { hash } }, issues: [{ note: '右侧留白过大', severity: 'major' }], style_comparisons: [{ target_observation: '字重仍弱于原图', verdict: 'fail' }] });
  assert.deepEqual(plain(review.paragraphs), ['主体没有可见变化']);
  assert.equal(review.checks.length, 2);
  assert.equal(review.checks[1].text, '字重仍弱于原图');
});

test('visible selection excludes empty stages but preserves running and actual failure', () => {
  const image = { id: 'output', kind: 'image', sha256: hash, url: '/local-image-review/workflow/artifact?session_id=owner' };
  const nodes = [{ id: 'empty', status: 'planned', artifacts: [] }, { id: 'skipped', status: 'skipped', artifacts: [] }, { id: 'result', status: 'artifact_ready', artifacts: [image] }, { id: 'active', status: 'running', artifacts: [] }, { id: 'failed', status: 'failed', reason: 'font missing\nlong trace', exit_code: 2, artifacts: [] }];
  const visible = api.visibleNodes(nodes);
  assert.deepEqual(plain(visible.map(item => item.id)), ['result', 'active', 'failed']);
  assert.equal(api.selectedNode(visible, 'empty').id, 'active');
  assert.equal(api.selectedNode(visible.filter(item => item.id !== 'active'), 'empty').id, 'result');
  assert.equal(api.nodeContent(nodes[4]).paragraphs[0], 'font missing · 退出码 2');
  assert.equal(visible[0].artifacts[0], image);
  assert.equal(api.nodeContent({ id: 'quality.layered', verdict: 'pass', artifacts: [] }).paragraphs[0], '技术检查通过');
});

test('comparison inputs resolve exact existing protected image artifacts only', () => {
  const sourceImage = { id: 'source-id', kind: 'image', sha256: hash, url: '/local-image-review/workflow/artifact?artifact_id=source-id' };
  const resultImage = { id: 'result-id', kind: 'image', sha256: 'b'.repeat(64), url: '/local-image-review/workflow/artifact?artifact_id=result-id' };
  const nodes = [{ id: 'source.normalize', artifacts: [sourceImage] }, { id: 'composite.render', artifacts: [resultImage] }];
  const compare = { id: 'evidence.compare', artifacts: [], comparison_inputs: [{ source_artifact_id: 'source-id', result_artifact_id: 'result-id', language: 'ja', generated: false }] };
  const pairs = api.comparisonPairs(compare, nodes);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0].source, sourceImage);
  assert.equal(pairs[0].result, resultImage);
  assert.equal(api.usefulNode(compare, nodes), true);
  assert.equal(api.comparisonPairs({ comparison_inputs: [{ source_artifact_id: 'unknown', result_artifact_id: 'result-id' }] }, nodes).length, 0);
  assert.equal(api.comparisonPairs({ comparison_inputs: { unexpected: true } }, nodes).length, 0);
  assert.equal(api.comparisonArtifact({ kind: 'image', name: 'comparison.png' }), true);
  assert.equal(api.comparisonArtifact({ kind: 'image', name: 'contact-sheet.png' }), false);
  for (const selector of ['stage-content', 'translation-row', 'translation-source', 'translation-target', 'final-result', 'result-comparison']) assert.ok(source.includes(`'${selector}'`));
  assert.ok(!source.includes('展开 JSON'));
  assert.ok(!source.includes('辅助步骤 ·'));
});

test('comparison stage folds hundreds of detail images and recognizes suffix overview names', () => {
  const overview = [{ id: 'board', kind: 'image', name: 'board.png' }, { id: 'suffix', kind: 'image', name: 'product-native-comparison.png' }];
  const details = Array.from({ length: 372 }, (_, index) => ({ id: 'detail-' + index, kind: 'image', name: 'region-' + index + '-comparison.png', path: 'runs/compare/regions/' + index + '.png' }));
  const capturedDetail = { id: 'captured', kind: 'image', name: hash + '.png', path: '.review-desk/workflows/content/' + hash + '.png', source_path: 'C:\\runs\\compare\\differences\\headline-comparison.png' };
  const imageSet = [...overview, ...details, capturedDetail];
  const sections = api.comparisonSections({ id: 'evidence.compare' }, imageSet);
  assert.equal(sections.comparisons.length, 2);
  assert.equal(sections.ordinary.length, 0);
  assert.equal(sections.details.length, 373);
  assert.equal(sections.details[0], details[0]);
  assert.equal(sections.details.at(-1), capturedDetail);
  assert.equal(api.comparisonArtifact(capturedDetail), false);
  assert.equal(api.comparisonArtifact({ kind: 'image', name: hash + '.png', source_path: 'runs/compare/image-comparison.png' }), true);
  const regular = api.comparisonSections({ id: 'background.prepare' }, [{ kind: 'image', name: 'background.png' }]);
  assert.equal(regular.ordinary.length, 1);
  assert.equal(regular.details.length, 0);
  assert.ok(source.includes('局部对比 ·'));
  assert.ok(source.includes("loading: hero ? 'eager' : 'lazy'"));
});

test('result stage current IDs gate main gallery and historical artifacts remain exact', () => {
  const old = { id: 'old', kind: 'image', name: 'translated.png', sha256: hash, url: '/local-image-review/workflow/artifact?artifact_id=old', preview: { summary: 'Historical output only' } };
  const current = { id: 'current', kind: 'image', name: 'translated.png', sha256: 'b'.repeat(64), url: '/local-image-review/workflow/artifact?artifact_id=current' };
  const result = { id: 'result', status: 'artifact_ready', current_artifact_ids: ['current'], artifacts: [old, current] };
  assert.deepEqual(plain(api.nodeGallery(result).primary.map(item => item.id)), ['current']);
  assert.equal(api.nodeGallery(result).history[0], old);
  assert.equal(api.usefulNode(result), true);
  const unavailable = { ...result, current_artifact_ids: [] };
  assert.equal(api.nodeGallery(unavailable).primary.length, 0);
  assert.equal(api.nodeGallery(unavailable).history.length, 2);
  assert.equal(api.usefulNode(unavailable), false);
  const unknown = { ...result, current_artifact_ids: ['missing'] };
  assert.equal(api.usefulNode(unknown), false);
  const legacy = { id: 'result', artifacts: [old] };
  assert.equal(api.nodeGallery(legacy).primary[0], old);
  assert.equal(api.usefulNode(legacy), true);
  assert.equal(result.artifacts[0], old);
  assert.equal(old.sha256, hash);
  assert.ok(source.includes('版本记录 ·'));
});

test('business stages group raw nodes and deduplicate bytes without changing identities', () => {
  const image = { id: 'one', kind: 'image', sha256: hash, url: '/local-image-review/workflow/artifact?artifact_id=one' };
  const duplicate = { ...image, id: 'two' };
  const nodes = [
    { id: 'task.initialize', status: 'completed', artifacts: [image] },
    { id: 'source.normalize', status: 'artifact_ready', artifacts: [duplicate] },
    { id: 'job.render', status: 'running', artifacts: [] },
    { id: 'composite.render', status: 'completed', artifacts: [image] },
    { id: 'capability.inspect', status: 'planned', artifacts: [] },
  ];
  const stages = api.businessStages(nodes);
  assert.deepEqual(plain(stages.map(stage => stage.id)), ['input', 'composition']);
  assert.equal(stages[0].artifacts.length, 1);
  assert.equal(stages[0].artifacts[0], image);
  assert.equal(stages[0].sources.get(hash).length, 2);
  assert.equal(stages[1].status, 'running');
  assert.equal(api.selectedStage(stages, 'source.normalize').id, 'input');
  assert.equal(api.selectedStage(stages, '').id, 'composition');
  assert.equal(api.selectedStage(stages, 'input').id, 'input');
  assert.equal(api.selectedStage(stages, 'removed-stage'), undefined);
  assert.equal(nodes[0].artifacts[0], image);
});

test('rootless activities and explicit plans are readable without inferred completion', () => {
  const row = { active_stage: { text: 'Preparing background', stage: 'background.prepare' }, activities: [{ text: 'The source title is ready.' }], plan: { event_id: 'plan:1', items: [{ content: 'Read source', status: 'completed' }, { content: 'Translate', status: 'in_progress' }, { content: 'Deliver', status: 'mystery' }] }, runs: [] };
  assert.ok(api.sessionActivity(row));
  assert.equal(api.sessionProgress(row).total, 0);
  assert.deepEqual(plain(api.explicitPlan(row).map(item => item.status)), ['completed', 'in_progress', 'pending']);
  assert.equal(api.explicitPlan({ plan: { items: [{ arbitrary: 'hidden' }] } }).length, 0);
});

function preparedControl(input) {
  return { prepared: true, session_id: input.session_id, request_id: input.request_id, action: input.action,
    scope: { run_id: input.run_id, node_id: input.node_id, plan_event_id: input.plan_event_id, plan_index: input.plan_index },
    request: input.action === 'cancel' ? { sessionId: input.session_id } : { sessionId: input.session_id, requestId: input.request_id, mode: input.mode || 'queue', content: [{ type: 'text', text: input.instruction }], clientTimeZone: 'Asia/Shanghai' } };
}

test('uncertain control retries freeze request ID, scope and payload', async () => {
  const input = { action: 'prompt', session_id: 'A', run_id: 'runA', node_id: 'background.prepare', instruction: 'Continue this background', mode: 'queue' };
  const intent = api.controlIntentFor(null, input, 'req-A');
  input.session_id = 'B';
  let preparedCalls = 0; const requests = [];
  const prepare = async value => { preparedCalls++; return preparedControl(value); };
  const remote = { async prompt(request) { requests.push(request); if (requests.length === 1) throw new Error('uncertain'); return { ok: true, value: { accepted: true } }; } };
  await assert.rejects(api.dispatchControlIntent(intent, prepare, remote), /uncertain/);
  await api.dispatchControlIntent(intent, prepare, remote);
  assert.equal(preparedCalls, 1);
  assert.equal(requests[0], requests[1]);
  assert.equal(requests[1].sessionId, 'A');
  assert.equal(requests[1].requestId, 'req-A');
  assert.equal(api.controlIntentFor(intent, { ...intent.input, instruction: 'Changed request', request_id: undefined }, 'unused') === intent, false);
  assert.equal(api.controlIntentFor(intent, { action: 'prompt', session_id: 'A', run_id: 'runA', node_id: 'background.prepare', instruction: 'Continue this background', mode: 'queue' }, 'unused'), intent);
});

test('mismatched prepared control and unaccepted remote replies are rejected', async () => {
  const intent = api.controlIntentFor(null, { action: 'prompt', session_id: 'A', node_id: 'composite.render', run_id: 'r', instruction: 'Keep original title' }, 'req');
  let sent = 0;
  const remote = { async prompt() { sent++; return { ok: true, value: { accepted: false } }; } };
  const wrong = preparedControl(intent.input); wrong.scope.node_id = 'result';
  await assert.rejects(api.dispatchControlIntent(intent, async () => wrong, remote), /目标不匹配/);
  assert.equal(sent, 0);
  await assert.rejects(api.dispatchControlIntent(intent, async input => preparedControl(input), remote), /未确认接收/);
  assert.equal(sent, 1);
});

test('stop uses exact session cancellation and plan prompts never invent run identity', async () => {
  const stopped = [];
  const cancel = api.controlIntentFor(null, { action: 'cancel', session_id: 'A' }, 'stop-A');
  await api.dispatchControlIntent(cancel, async input => preparedControl(input), { async cancel(request) { stopped.push(request); return { ok: true, value: { accepted: true } }; } });
  assert.deepEqual(plain(stopped), [{ sessionId: 'A' }]);
  const plan = api.controlIntentFor(null, { action: 'prompt', session_id: 'C', plan_event_id: 'plan:22', plan_index: 0, instruction: 'Continue the selected plan step' }, 'plan-C');
  assert.equal(api.validControlIntent(preparedControl(plan.input), plan.input), true);
  const wrong = preparedControl(plan.input); wrong.scope.plan_index = 1;
  assert.equal(api.validControlIntent(wrong, plan.input), false);
  assert.equal(plan.input.run_id, undefined);
});

test('workflow panel renders a graph and exactly one result region without hero duplication', () => {
  const image = { id: 'output', kind: 'image', sha256: hash, name: 'translated.png', url: '/local-image-review/workflow/artifact?artifact_id=output' };
  const run = { id: 'run-A', root: 'runs/A', session_id: 'A', title: 'Run A', route: 'basic', results: [image], nodes: [{ id: 'source.normalize', status: 'completed', artifacts: [{ ...image, id: 'source' }] }, { id: 'basic.render', status: 'running', artifacts: [image] }, { id: 'result', status: 'review_pending', current_artifact_ids: ['output'], artifacts: [image] }] };
  const snapshot = { sessions: [{ session_id: 'A', title: 'Session A', can_control: { prompt: true, cancel: true }, runs: [run] }], unassigned: [] };
  function render(data, selection, stageId) {
    let stateIndex = 0; const registrations = [];
    const react = { createElement(type, props, ...children) { return { type, props: props || {}, children }; },
      useState(initial) { const supplied = [data, selection, stageId]; const index = stateIndex++; return [index < supplied.length ? supplied[index] : typeof initial === 'function' ? initial() : initial, () => {}]; },
      useRef(value) { return { current: value }; }, useEffect() {}, Fragment: 'fragment' };
    const { plugin } = load(react);
    plugin.apply({ effect(callback) { callback(); }, locale: { register() {}, bind: () => () => '流程台' }, slots: { inject(name, callback) { callback(); }, register(options, component) { registrations.push({ options, component }); } } });
    const panel = registrations.find(row => row.options.name === 'main').component;
    const tree = panel({ useSessions: fn => fn({ byId: {} }), useWorkspaces: fn => fn({ archivedSessionIds: [] }) });
    const nodes = [];
    function visit(value) { if (Array.isArray(value)) value.forEach(visit); else if (value && typeof value === 'object') { nodes.push(value); visit(value.children); } }
    visit(tree); return nodes;
  }
  const nodes = render(snapshot, { session: 'A', run: 'run-A' }, 'delivery');
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'step-result').length, 1);
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'final-result').length, 1);
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'final-artifact').length, 0);
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'result-comparison').length, 0);
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'flow-stage').length, 3);
  assert.equal(nodes.filter(node => node.props?.['data-testid'] === 'step-send').length, 1);
  const rootless = render({ sessions: [{ session_id: 'C', title: 'Rootless C', runs: [], activities: [{ text: 'Source recognition complete.' }], plan: { event_id: 'p', items: [{ content: 'Translate source', status: 'pending' }] }, can_control: { prompt: true, cancel: true } }], unassigned: [] }, { session: 'C', run: '' }, '');
  assert.equal(rootless.filter(node => node.props?.['data-testid'] === 'waiting-session').length, 1);
  assert.equal(rootless.filter(node => node.props?.['data-testid'] === 'flow-stage').length, 0);
  assert.equal(rootless.filter(node => node.props?.['data-testid'] === 'step-instruction').length, 1);
});

test('rootless current step and result activity are not replaced by a plan title', () => {
  const current = { text: '核对标题与目标语言', stage: 'semantic.analyze' };
  assert.equal(api.sessionActivity({ active_stage: current, activities: [{ type: 'plan', text: '三步制作计划' }] }), '核对标题与目标语言');
  assert.equal(api.sessionActivity({ active_stage: current, activities: [{ type: 'result', text: '已确认源标题' }, { type: 'plan', text: '三步制作计划' }] }), '已确认源标题');
  assert.ok(source.includes("'data-testid': 'session-current-stage'"));
});

test('current step gallery keeps differently encoded captures in version history', () => {
  const live = { id: 'live', kind: 'image', capture: 'current', name: 'source.png', sha256: hash, url: '/local-image-review/workflow/artifact?artifact_id=live' };
  const snapshot = { id: 'snapshot', kind: 'image', capture: 'observed_current', name: 'b'.repeat(64) + '.png', sha256: 'b'.repeat(64), url: '/local-image-review/workflow/artifact?artifact_id=snapshot' };
  const sameBytes = { ...live, id: 'same-bytes', capture: 'observed_current' };
  const members = [{ id: 'task.initialize', artifacts: [snapshot, sameBytes] }, { id: 'source.normalize', artifacts: [live] }];
  const gallery = api.stageGallery(members);
  assert.deepEqual(plain(gallery.primary.map(item => item.id)), ['live']);
  assert.equal(gallery.primary[0], live);
  assert.deepEqual(plain(gallery.history.map(item => item.id)), ['snapshot']);
  assert.equal(gallery.history[0], snapshot);
  assert.equal(gallery.sources.get(hash).length, 2);
  const stage = api.businessStages(members)[0];
  assert.equal(stage.artifacts[0], live);
  assert.equal(stage.historyArtifacts[0], snapshot);
});

test('snapshot-only old root shows one labelled history fallback and preserves remaining versions', () => {
  const old = { id: 'old', kind: 'image', capture: 'observed_current', captured_at: '2026-11-01T00:00:00Z', sha256: hash, url: '/local-image-review/workflow/artifact?artifact_id=old' };
  const newest = { ...old, id: 'new', captured_at: '2026-11-02T00:00:00Z', sha256: 'b'.repeat(64) };
  const gallery = api.stageGallery([{ id: 'source.normalize', artifacts: [old] }, { id: 'task.initialize', artifacts: [newest] }]);
  assert.equal(gallery.primary.length, 1);
  assert.equal(gallery.primary[0], newest);
  assert.equal(gallery.history[0], old);
  assert.equal(api.historicalArtifact(gallery.primary[0]), true);
});

test('session activity omits plain idle status but preserves meaningful result text', () => {
  assert.equal(api.sessionActivity({ status: 'idle', active_stage: { text: '会话已空闲', status: 'idle' }, activities: [{ type: 'status', text: '会话已空闲' }] }), '');
  assert.equal(api.sessionActivity({ activities: [{ type: 'result', text: '背景已经复核，等待排字。' }, { type: 'status', text: '会话已空闲' }] }), '背景已经复核，等待排字。');
});

test('lazy session detail preserves latest metadata and cannot resurrect a hidden session', () => {
  const before = { sessions: [{ session_id: 'a', title: 'SHE-20261103-068', status: 'running', detail_loaded: false, activities: [{ text: 'latest' }], runs: [{ id: 'r', root: 'runs/a', nodes: [] }] }] };
  const payload = { session: { session_id: 'a', title: 'old title', status: 'idle', runs: [{ id: 'r', root: 'runs/a', nodes: [{ id: 'result' }] }] } };
  const loaded = api.mergeSessionDetail(before, payload, 'a');
  assert.equal(loaded.sessions[0].title, before.sessions[0].title);
  assert.equal(loaded.sessions[0].status, 'running');
  assert.equal(loaded.sessions[0].detail_loaded, true);
  assert.equal(loaded.sessions[0].runs[0].nodes.length, 1);
  const refreshed = api.mergeWorkflowSnapshot({ sessions: [{ ...before.sessions[0], status: 'idle' }], unassigned: [] }, loaded);
  assert.equal(refreshed.sessions[0].status, 'idle');
  assert.equal(refreshed.sessions[0].runs[0].nodes.length, 1);
  assert.equal(api.mergeSessionDetail(before, payload, 'b'), before);
  const hidden = { sessions: [] };
  assert.equal(api.mergeSessionDetail(hidden, payload, 'a'), hidden);
});

test('stop requires official running status and cancellation permission', () => {
  assert.equal(api.canStopSession({ status: 'running', can_control: { cancel: true } }), true);
  for (const status of ['idle', 'queued', 'unknown', undefined]) assert.equal(api.canStopSession({ status, can_control: { cancel: true }, plan: { items: [{ content: 'Pending task', status: 'in_progress' }] } }), false);
  assert.equal(api.canStopSession({ status: 'running', can_control: { cancel: false } }), false);
});
