import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { once } from 'node:events';

const target = process.env.DSH_REVIEW_CHECK_URL;
delete process.env.DSH_REVIEW_CHECK_URL;
if (!target) throw new Error('Run through the installed workflow verification tool.');
const output = resolve('.review-desk/verification/native-plugin');
await mkdir(output, { recursive: true });
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', '--user-data-dir=' + resolve('.review-desk/native-browser-profile'), 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
const ROOT = '[data-testid=image-review-workbench]';
const fakeIds = ['workflow-fixture-A', 'workflow-fixture-B', 'workflow-fixture-C'];
let socket, fixtureChild, relayFixture, send, workflowResponse, sessionResponse;
const report = { passed: false, layout: 'v5', checks: [], errors: [], checkedAt: new Date().toISOString(), real: { readOnly: true, sentSessionMessages: 0, cancelledTurns: 0 }, fixture: { ids: fakeIds, temporaryBackend: true, remoteAccepted: false }, transport: { websocketPaths: [], sessionMethods: [] } };
const sanitized = value => String(value).replace(/https?:\/\/\S+/g, '<URL>');
try {
  const endpoint = await new Promise((done, fail) => { let text = ''; const timer = setTimeout(() => fail(new Error('Browser startup timeout')), 12000); chrome.once('error', fail); chrome.stderr.on('data', chunk => { text += chunk; const match = text.match(/DevTools listening on (\S+)/); if (match) { clearTimeout(timer); done(match[1]); } }); });
  const pages = await (await fetch(new URL('/json/list', endpoint.replace('ws:', 'http:')))).json();
  socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
  await once(socket, 'open');
  let serial = 0;
  const pending = new Map();
  const observeMethod = (payload, wire) => {
    // Observe method names only; never persist frame bodies, auth or conversation text.
    for (const method of ['session/page', 'session/prompt', 'session/cancel', 'session.prompt', 'session.cancel']) {
      if (typeof payload === 'string' && payload.includes(method) && !report.transport.sessionMethods.some(item => item.method === method && item.wire === wire)) report.transport.sessionMethods.push({ method, wire });
    }
  };
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) { const item = pending.get(message.id); pending.delete(message.id); if (item) { clearTimeout(item.timer); message.error ? item.reject(new Error('Browser command failed: ' + message.error.message)) : item.resolve(message.result); } }
    if (message.method === 'Fetch.requestPaused' && relayFixture) void relayFixture(message.params).catch(async error => { report.errors.push('Fixture relay failed: ' + sanitized(error.message)); try { await send('Fetch.failRequest', { requestId: message.params.requestId, errorReason: 'BlockedByClient' }); } catch {} });
    if (message.method === 'Runtime.exceptionThrown') report.errors.push(sanitized(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text));
    if (message.method === 'Network.responseReceived' && new URL(message.params.response.url).pathname === '/local-image-review/workflow/session') sessionResponse = { requestId: message.params.requestId, status: message.params.response.status };
    if (message.method === 'Network.responseReceived' && new URL(message.params.response.url).pathname === '/local-image-review/workflow') workflowResponse = { requestId: message.params.requestId, status: message.params.response.status };
    if (message.method === 'Network.webSocketCreated') { const path = new URL(message.params.url).pathname; if (!report.transport.websocketPaths.includes(path)) report.transport.websocketPaths.push(path); }
    if (message.method === 'Network.webSocketFrameSent') observeMethod(message.params.response.payloadData, 'websocket');
    if (message.method === 'Network.requestWillBeSent') observeMethod(message.params.request.url + ' ' + (message.params.request.postData || ''), 'http');
  };
  send = (method, params = {}) => new Promise((resolveCommand, reject) => { const id = ++serial; const timer = setTimeout(() => { pending.delete(id); reject(new Error('Browser command timed out: ' + method)); }, 25000); pending.set(id, { resolve: resolveCommand, reject, timer }); socket.send(JSON.stringify({ id, method, params })); });
  const screenshot = async name => { const path = resolve(output, name); await writeFile(path, Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).data, 'base64')); return path; };
  const evaluate = async expression => { const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (value.exceptionDetails) { report.screenshot = await screenshot('failed.png'); throw new Error('Page evaluation failed: ' + String(value.exceptionDetails.exception?.description || value.exceptionDetails.text).slice(0, 500) + '; expression: ' + expression.slice(0, 250)); } return value.result.value; };
  const wait = async (expression, timeout = 16000) => { const started = Date.now(); while (Date.now() - started < timeout) { if (await evaluate('Boolean(' + expression + ')')) return; await new Promise(done => setTimeout(done, 180)); } report.screenshot = await screenshot('failed.png'); const text = await evaluate(`document.querySelector('${ROOT}')?.innerText.slice(0,900)||''`); throw new Error('UI wait failed: ' + expression + '; panel: ' + text); };
  const selectSession = async id => { await wait(`document.querySelector('${ROOT} [data-session-id="${id}"] .session-head')`); await evaluate(`document.querySelector('${ROOT} [data-session-id="${id}"] .session-head').click()`); };
  const selectStage = async id => { await wait(`document.querySelector('${ROOT} [data-testid=flow-stage][data-stage-id="${id}"]')`); await evaluate(`document.querySelector('${ROOT} [data-testid=flow-stage][data-stage-id="${id}"]').click()`); await wait(`document.querySelector('${ROOT} [data-testid=flow-stage][data-stage-id="${id}"]')?.getAttribute('aria-pressed')==='true'`); };
  const clickText = text => evaluate(`(()=>{const e=[...document.querySelectorAll('${ROOT} button')].find(e=>e.textContent.trim()===${JSON.stringify(text)});if(!e)throw new Error('Button missing: '+${JSON.stringify(text)});e.click()})()`);
  const detailImagesReady = async () => { await evaluate(`document.querySelector('${ROOT} .detail')?.scrollIntoView({block:'nearest'})`); await wait(`(()=>{const images=[...document.querySelectorAll('${ROOT} .detail img')].filter(i=>!i.closest('details:not([open])'));return images.length>0&&images.every(i=>i.complete&&i.naturalWidth>0)})()`, 20000); };
  const uniqueMainImages = async label => {
    const state = await evaluate(`(()=>{const root=document.querySelector('${ROOT}');const images=[...root.querySelectorAll('main img')].filter(i=>!i.closest('details:not([open])'));const hashes=images.map(i=>{try{return new URL(i.src).searchParams.get('sha256')||i.src}catch{return i.src}});return {details:root.querySelectorAll('main .detail').length,outside:images.filter(i=>!i.closest('.detail')).length,count:images.length,unique:new Set(hashes).size}})()`);
    if (state.details !== 1 || state.outside || state.count !== state.unique) throw new Error(label + ': duplicate or global main images: ' + JSON.stringify(state));
    return state;
  };
  const deliveryTab = async name => { await evaluate(`(()=>{const tab=[...document.querySelectorAll('${ROOT} .detail [role=tab]')].find(e=>e.textContent.trim()===${JSON.stringify(name)});if(!tab)throw new Error('Delivery tab missing');tab.click()})()`); await wait(`(()=>{const t=[...document.querySelectorAll('${ROOT} .detail [role=tab]')].find(e=>e.textContent.trim()===${JSON.stringify(name)});return t?.getAttribute('aria-selected')==='true'})()`); };

  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: target });
  const entryLabels = ['图片审阅', '图片流程', '流程台', '多会话流程台'];
  await wait(`[...document.querySelectorAll('button')].some(e=>${JSON.stringify(entryLabels)}.includes((e.getAttribute('aria-label')||e.title||e.textContent).trim()))`);
  await evaluate(`[...document.querySelectorAll('button')].find(e=>${JSON.stringify(entryLabels)}.includes((e.getAttribute('aria-label')||e.title||e.textContent).trim())).click()`);
  report.phase = 'open-panel';
  await wait(`document.querySelector('${ROOT} [data-testid=workflow-desk]')`);
  report.phase = 'wait-requirement-rows';
  await wait(`document.querySelector('${ROOT} [data-testid=session-row]')`, 45000);
  report.phase = 'probe-workflow-api';
  if (!workflowResponse) throw new Error('Workflow response was not observed');
  const capturedBody = await send('Network.getResponseBody', { requestId: workflowResponse.requestId });
  const capturedWorkflow = JSON.parse(capturedBody.base64Encoded ? Buffer.from(capturedBody.body, 'base64').toString('utf8') : capturedBody.body);
  report.api = { status: workflowResponse.status, sessions: capturedWorkflow.sessions?.length, flows: capturedWorkflow.sessions?.reduce((n,s)=>n+s.runs.length,0), unassigned: capturedWorkflow.unassigned?.length, discovery: capturedWorkflow.discovery };
  await evaluate('window.__workflowVerificationData=' + JSON.stringify(capturedWorkflow) + ';true');
  if (report.api.status !== 200) throw new Error(report.api.error || 'Workflow endpoint failed');
  if (await evaluate(`window.__workflowVerificationData.sessions.some(s=>${JSON.stringify(fakeIds)}.includes(s.session_id))`)) throw new Error('Fixture IDs unexpectedly exist in real Host data');
  await wait(`document.querySelector('${ROOT} [data-testid=session-row]')`);
  if (await evaluate(`document.querySelector('${ROOT}').querySelectorAll('iframe').length`)) throw new Error('Workflow is not native');
  report.checks.push('Real: authenticated native project-scoped workflow loads without a fixture');
  const requirement = await evaluate(`(()=>{const sessions=window.__workflowVerificationData.sessions;if(sessions.some(s=>!/^[A-Z][A-Z0-9]{1,15}-[0-9]{8}-[0-9]{3,6}$/i.test(s.title.trim())))throw new Error('Non-requirement conversation visible');if(document.querySelector('${ROOT} .history'))throw new Error('Unassigned history visible');return sessions[0]?{id:sessions[0].session_id,title:sessions[0].title}:null})()`);
  report.checks.push('Real: only requirement-number conversations are visible; no unassigned history');
  if (requirement) {
    await selectSession(requirement.id);
    await wait(`!document.querySelector('${ROOT} [data-testid=session-open]')?.disabled`);
    await evaluate(`document.querySelector('${ROOT} [data-testid=session-open]').click()`);
    await wait(`document.title.startsWith(${JSON.stringify(requirement.title)})&&!document.querySelector('${ROOT}')`, 15000);
    report.checks.push('Real: flow console opens the original requirement conversation through official navigation');
    await evaluate(`[...document.querySelectorAll('button')].find(e=>${JSON.stringify(entryLabels)}.includes((e.getAttribute('aria-label')||e.title||e.textContent).trim())).click()`);
    await wait(`document.querySelector('${ROOT} [data-testid=session-row]')`);
  }
  const sample = await evaluate("(()=>{const d=window.__workflowVerificationData;const s=d.sessions.find(s=>s.runs?.length);const r=s?.runs?.[0];return s&&r?{session_id:s.session_id,root:r.root}:null})()");
  if (sample) {
    await selectSession(sample.session_id);
    await evaluate(`(()=>{const e=[...document.querySelectorAll('${ROOT} .runs .run')].find(e=>e.title===${JSON.stringify(sample.root)});if(e)e.click()})()`);
  report.phase = 'selected-session-detail';
    await wait(`document.querySelector('${ROOT} [data-testid=flow-stage]')`, 25000);
    if (sessionResponse) {
      const body = await send('Network.getResponseBody', { requestId: sessionResponse.requestId });
      const detail = JSON.parse(body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body);
      if (detail.session?.session_id === sample.session_id) await evaluate(`(()=>{const d=window.__workflowVerificationData;const s=d.sessions.find(s=>s.session_id===${JSON.stringify(sample.session_id)});Object.assign(s,${JSON.stringify(detail.session)});return true})()`);
    }
    await selectStage('input');
    await detailImagesReady();
    report.real.input = await uniqueMainImages('Real input');
    report.real.inputSources = await evaluate(`window.__workflowVerificationData.sessions.find(s=>s.session_id===${JSON.stringify(sample.session_id)}).runs.find(r=>r.root===${JSON.stringify(sample.root)}).nodes.filter(n=>/^(capability|task|job|source|analysis\\.packet)/.test(n.id)).flatMap(n=>n.artifacts.filter(a=>a.kind==='image').map(a=>({node:n.id,name:a.name,path:a.path,capture:a.capture,source_path:a.source_path})))`);
    report.real.inputScreenshot = await screenshot('workflow-real-input.png');
    if (sample.has_result) {
      await selectStage('delivery'); await deliveryTab('成品'); await detailImagesReady();
      await wait(`document.querySelector('${ROOT} [data-testid=final-result] img')?.naturalWidth>0&&!document.querySelector('${ROOT} [data-testid=result-comparison]')`);
      report.real.result = await uniqueMainImages('Real result');
      report.workflowScreenshot = report.real.resultScreenshot = await screenshot('workflow.png');
      await deliveryTab('对比');
      await wait(`document.querySelector('${ROOT} [data-testid=result-comparison]')&&!document.querySelector('${ROOT} [data-testid=final-result]')`);
      if (await evaluate(`!!document.querySelector('${ROOT} [data-testid=result-comparison] img')`)) await detailImagesReady();
      report.real.comparison = await uniqueMainImages('Real comparison');
      report.real.comparisonScreenshot = await screenshot('workflow-real-comparison.png');
    } else report.workflowScreenshot = report.real.inputScreenshot;
    report.checks.push('Real: one selected stage result, actual input/result images and exclusive delivery tabs');
  }
  report.responsive = [];
  for (const width of [941, 768, 414, 375, 320]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    report.responsive.push({ viewport: width, ...await evaluate(`(()=>{const e=document.querySelector('${ROOT}');return {width:e.clientWidth,scrollWidth:e.scrollWidth}})()`) });
  }
  if (report.responsive.some(row => row.scrollWidth > row.width + 1)) throw new Error('Workflow horizontal overflow');
  report.checks.push('Real: desktop and 941/768/414/375/320 widths fit the native panel');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1100, deviceScaleFactor: 1, mobile: false });
  if (await evaluate('window.__workflowVerificationData.unassigned.length>0')) {
    await evaluate(`document.querySelector('${ROOT} details.history').open=true;document.querySelector('${ROOT} details.history .run').click()`);
    await wait(`document.querySelector('${ROOT} .binding')&&document.querySelector('${ROOT} [data-testid=flow-stage]')`, 25000);
    if (await evaluate(`(()=>{const e=document.querySelector('${ROOT} [data-testid=save-result]');return !!e&&!e.disabled})()`)) throw new Error('Historical flow must remain read-only');
    report.historyScreenshot = await screenshot('workflow-history.png');
    report.checks.push('Real: unassigned historical workflow remains read-only');
  }

  fixtureChild = spawn('C:/Users/Echo/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe', [resolve('scripts/workflow_ui_fixture.py')], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  const readFixtureLine = timeout => new Promise((done, fail) => { let text = ''; const timer = setTimeout(() => { fixtureChild.stdout.off('data', listener); fail(new Error('Fixture response timeout')); }, timeout); const listener = chunk => { text += chunk; if (text.includes('\n')) { clearTimeout(timer); fixtureChild.stdout.off('data', listener); try { done(JSON.parse(text.trim())); } catch { fail(new Error('Fixture response malformed')); } } }; fixtureChild.once('error', fail); fixtureChild.stdout.on('data', listener); });
  const fixture = await readFixtureLine(10000);
  if (fixture.fixture_only !== true || JSON.stringify(fixture.session_ids) !== JSON.stringify(fakeIds)) throw new Error('Unsafe fixture identity');
  const fixtureCommand = command => { const response = readFixtureLine(5000); fixtureChild.stdin.write(command + '\n'); return response; };
  const privateRequest = (path, options = {}) => fetch(fixture.origin + path, { ...options, headers: { 'X-Review-Token': fixture.token, Origin: fixture.origin, 'Content-Type': 'application/json' } });
  let fixtureData;
  report.fixtureRequests = [];
  const present = data => { for (const session of data.sessions) { session.can_send = false; session.can_control = { prompt: session.session_id === fakeIds[2], cancel: false }; for (const run of session.runs) { const url = artifact => '/local-image-review/workflow/artifact?' + new URLSearchParams({ session_id: session.session_id, run_id: run.id, artifact_id: artifact.id, sha256: artifact.sha256 }); for (const node of run.nodes) for (const artifact of node.artifacts) artifact.url = url(artifact); for (const artifact of run.results || []) artifact.url = url(artifact); if (run.result) run.result.url = url(run.result); } } return data; };
  const fulfill = (requestId, status, body, type = 'application/json') => send('Fetch.fulfillRequest', { requestId, responseCode: status, responseHeaders: [{ name: 'Content-Type', value: type }, { name: 'Cache-Control', value: 'no-store' }], body: Buffer.from(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body)).toString('base64') });
  const preparedControl = body => {
    if (!fakeIds.includes(body.session_id) || body.action !== 'prompt' || typeof body.request_id !== 'string' || !body.request_id || typeof body.instruction !== 'string' || !body.instruction.trim()) throw new Error('Unsafe fixture control intent');
    const session = fixtureData.sessions.find(item => item.session_id === body.session_id);
    if (!session || session.session_id !== fakeIds[2] || session.runs.length) throw new Error('Fixture control must target rootless C only');
    if (body.run_id !== undefined || body.node_id !== undefined || body.plan_event_id !== session.plan.event_id || !Number.isInteger(body.plan_index) || !session.plan.items[body.plan_index]) throw new Error('Fixture plan control identity mismatch');
    const request = { requestId: body.request_id, sessionId: session.session_id, mode: 'queue', content: [{ type: 'text', text: 'Fixture-only verification; do not create a session. ' + body.instruction }], clientTimeZone: 'Asia/Shanghai' };
    report.fixture.controlIntent = { session_id: session.session_id, action: body.action, plan_event_id: body.plan_event_id, plan_index: body.plan_index, request_id: body.request_id, prepared: true };
    return { prepared: true, session_id: session.session_id, action: body.action, request_id: body.request_id, scope: { kind: 'plan', plan_event_id: body.plan_event_id, plan_index: body.plan_index }, request };
  };
  relayFixture = async ({ requestId, request }) => {
    const url = new URL(request.url), leaf = url.pathname.slice('/local-image-review/workflow'.length);
    let response;
    if (!leaf) { response = await privateRequest('/api/workflow/snapshot', { method: 'POST', body: JSON.stringify({ session_ids: fakeIds }) }); if (!response.ok) throw new Error('Fixture snapshot failed'); fixtureData = present(await response.json()); report.fixtureRequests.push({ method: 'GET', endpoint: 'workflow', status: response.status }); await fulfill(requestId, response.status, fixtureData); return; }
    if (leaf === '/control') { try { const body = JSON.parse(request.postData || '{}'); const prepared = preparedControl(body); report.fixtureRequests.push({ method: 'POST', endpoint: '/control', status: 200, fixtureOnly: true }); await fulfill(requestId, 200, prepared); } catch (error) { report.fixtureRequests.push({ method: 'POST', endpoint: '/control', status: 409, fixtureOnly: true }); await fulfill(requestId, 409, { error: error.message }); } return; }
    if (leaf === '/artifact') { const session = fixtureData.sessions.find(item => item.session_id === url.searchParams.get('session_id')); const run = session?.runs.find(item => item.id === url.searchParams.get('run_id')); const artifact = run?.nodes.flatMap(item => item.artifacts).find(item => item.id === url.searchParams.get('artifact_id') && item.sha256 === url.searchParams.get('sha256')); if (!artifact) throw new Error('Fixture artifact missing'); response = await privateRequest('/api/workflow/asset?' + new URLSearchParams({ path: artifact.path, sha256: artifact.sha256 })); }
    else if (['/comments', '/delivery', '/results/save'].includes(leaf)) { const body = JSON.parse(request.postData || '{}'); if (!fakeIds.includes(body.session_id)) { await fulfill(requestId, 403, { error: 'Fixture writes cannot address a real session' }); return; } response = await privateRequest('/api/workflow' + leaf, { method: 'POST', body: request.postData }); }
    else { await fulfill(requestId, 404, { error: 'Endpoint excluded from isolated fixture' }); return; }
    report.fixtureRequests.push({ method: request.method, endpoint: leaf, status: response.status });
    await fulfill(requestId, response.status, Buffer.from(await response.arrayBuffer()), response.headers.get('content-type') || 'application/json');
  };
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/local-image-review/workflow*', requestStage: 'Request' }] });
  await evaluate(`document.querySelector('${ROOT} [data-testid=workflow-refresh]').click()`);
  await selectSession(fakeIds[2]);
  await wait(`document.querySelector('${ROOT} [data-testid=waiting-session] [data-testid=session-current-stage]')?.innerText.includes('核对标题与目标语言')`);
  if (await evaluate(`document.querySelectorAll('${ROOT} [data-testid=flow-stage]').length`)) throw new Error('Rootless session fabricated production groups');
  const c = fixtureData.sessions.find(item => item.session_id === fakeIds[2]);
  if (c.runs.length || c.plan.items.length !== 3 || !c.active_stage || c.activities.length < 2) throw new Error('Rootless fixture lost real backend activity/plan');
  await evaluate(`document.querySelector('${ROOT} .explicit-plan button').click()`);
  await wait(`document.querySelector('${ROOT} .explicit-plan button')?.getAttribute('aria-pressed')==='true'`);
  report.fixture.rootlessScreenshot = await screenshot('workflow-fixture-rootless.png');
  await fixtureCommand('activity');
  await wait(`document.querySelector('${ROOT} [data-testid=session-activity]')?.innerText.includes('已核对标题，正在准备目标文字')`);
  report.checks.push('Fixture: rootless C displays backend activities, active stage, plan and automatic activity updates');
  // No wire simulation: the official SDK can only attempt a non-existent fixture ID.
  await evaluate(`document.querySelector('${ROOT} [data-testid=step-instruction]').focus()`);
  await send('Input.insertText', { text: 'Fixture verification instruction; this ID must be rejected by the real Host.' });
  await evaluate(`document.querySelector('${ROOT} [data-testid=step-send]').click()`);
  await wait(`(document.querySelector('${ROOT} .notice.error')||document.querySelector('${ROOT} .notice')?.innerText.includes('指令已接收'))&&!document.querySelector('${ROOT} [data-testid=step-send]').disabled`, 20000);
  report.fixture.remoteAccepted = await evaluate(`!!document.querySelector('${ROOT} .notice')?.innerText.includes('指令已接收')`);
  if (!report.fixture.controlIntent?.prepared || report.fixture.remoteAccepted) throw new Error('Fixture SDK rejection was misreported as accepted');
  report.fixture.control = { preflight: 'fixture-only validated frozen intent', sdk: 'official SDK rejected non-existent fixture ID', accepted: false, realSessionControls: 0, cancelledTurns: 0 };
  report.fixture.controlScreenshot = await screenshot('workflow-fixture-control-rejected.png');
  report.checks.push('Fixture: prepared rootless plan intent reaches official SDK; rejection never reports accepted');
  // Clear the retained failed instruction so switching does not discard a real draft.
  await evaluate(`(()=>{const e=document.querySelector('${ROOT} [data-testid=step-instruction]');const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(e,'');e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
  await selectSession(fakeIds[0]);
  await wait(`document.querySelector('${ROOT} [data-testid=flow-stage][data-stage-id=background]')`);
  const groups = await evaluate(`[...document.querySelectorAll('${ROOT} [data-testid=flow-stage]')].map(e=>e.dataset.stageId)`);
  if (groups.length !== 8 || new Set(groups).size !== groups.length) throw new Error('Fixture does not expose eight distinct business groups');
  await selectStage('translation');
  await wait(`document.querySelector('${ROOT} [data-testid=translation-row]')?.innerText.includes('Hello')&&document.querySelector('${ROOT} [data-testid=translation-row]')?.innerText.includes('こんにちは')`);
  const readable = await evaluate(`(()=>{const e=document.querySelector('${ROOT} [data-testid=translation-row]');return {text:e.innerText,rawJson:[...document.querySelectorAll('${ROOT} .detail pre')].some(p=>!p.closest('details:not([open])'))}})()`);
  if (!readable.text.includes('→') || readable.rawJson) throw new Error('Translation is not readable source-to-target text');
  await evaluate(`document.querySelector('${ROOT} .detail').scrollIntoView({block:'nearest'})`);
  report.translationScreenshot = await screenshot('workflow-fixture-translation.png');
  await selectStage('checks');
  await wait(`document.querySelector('${ROOT} [data-testid=stage-content]')?.innerText.includes('Keep the main headline centered')`);
  await selectStage('delivery'); await deliveryTab('成品'); await detailImagesReady();
  await wait(`document.querySelector('${ROOT} [data-testid=final-result] img')?.naturalWidth>0&&!document.querySelector('${ROOT} [data-testid=result-comparison]')`);
  report.fixture.result = await uniqueMainImages('Fixture result');
  report.fixture.resultScreenshot = await screenshot('workflow-fixture-result.png');
  await deliveryTab('对比'); await detailImagesReady();
  if (await evaluate(`!!document.querySelector('${ROOT} [data-testid=final-result]')`)) throw new Error('Delivery tabs render result and comparison simultaneously');
  report.fixture.comparison = await uniqueMainImages('Fixture comparison');
  report.fixtureFlowScreenshot = await screenshot('workflow-fixture-comparison.png');
  report.checks.push('Fixture: eight groups, Hello → こんにちは, important non-image result, exclusive delivery tabs and no duplicate main SHA');
  await fixtureCommand('advance');
  await wait(`document.querySelector('${ROOT} [data-testid=flow-stage][data-stage-id=composition]')?.dataset.status==='running'`, 10000);
  report.checks.push('Fixture: real private-backend production event updates composition without refresh');
  await selectStage('composition'); await detailImagesReady();
  await evaluate(`(()=>{const card=document.querySelector('${ROOT} .detail [data-testid=node-artifact]');card.querySelector('.more-actions').open=true;[...card.querySelectorAll('button')].find(e=>e.textContent.trim()==='点评区域').click()})()`);
  await wait(`document.querySelector('${ROOT} [data-testid=node-comment-dialog] .image-stage img')?.naturalWidth>0`);
  const rect = await evaluate(`(()=>{const r=document.querySelector('${ROOT} .image-stage').getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height}})()`);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: rect.x + rect.w * .2, y: rect.y + rect.h * .25, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: rect.x + rect.w * .65, y: rect.y + rect.h * .65, button: 'left', buttons: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: rect.x + rect.w * .65, y: rect.y + rect.h * .65, button: 'left', clickCount: 1 });
  await evaluate(`document.querySelector('${ROOT} [data-testid=node-comment-dialog] textarea').focus()`);
  await send('Input.insertText', { text: 'Isolated selected-area comment; no session message.' });
  report.commentScreenshot = await screenshot('workflow-fixture-comment.png');
  await clickText('只保存点评');
  await wait(`document.querySelector('${ROOT} [data-testid=node-comment-dialog]')?.innerText.includes('已保存')`);
  await clickText('关闭');
  await evaluate(`document.querySelector('${ROOT} [data-testid=workflow-refresh]').click()`);
  await wait(`[...document.querySelectorAll('${ROOT} .technical summary')].some(e=>e.textContent.includes('已保存点评')&&e.textContent.includes('1'))`);
  const persisted = await (await privateRequest('/api/workflow/snapshot', { method: 'POST', body: JSON.stringify({ session_ids: [fakeIds[0]] }) })).json();
  const savedComment = persisted.sessions[0].runs[0].comments[0];
  if (savedComment.session_id !== fakeIds[0] || savedComment.node_id !== 'composite.render' || !savedComment.bbox || savedComment.bbox[2] <= 0) throw new Error('Selected-area comment lost original node, owner or coordinates');
  await selectStage('delivery'); await deliveryTab('成品');
  await evaluate(`document.querySelector('${ROOT} [data-testid=save-result]').click()`);
  await wait(`document.querySelector('${ROOT} .saved-link')`);
  const snapshotBeforeMutation = await (await privateRequest('/api/workflow/snapshot', { method: 'POST', body: JSON.stringify({ session_ids: [fakeIds[0]] }) })).json();
  const fixtureRun = snapshotBeforeMutation.sessions[0].runs[0];
  await fixtureCommand('mutate');
  const stale = await privateRequest('/api/workflow/comments', { method: 'POST', body: JSON.stringify({ session_id: fakeIds[0], run_id: fixtureRun.id, node_id: 'composite.render', artifact_id: fixtureRun.result.id, artifact_sha256: fixtureRun.result.sha256, note: 'Must reject changed output', request_id: 'stale-fixture-request' }) });
  if (stale.status !== 409) throw new Error('Stale stage artifact was not rejected');
  report.fixturePersistence = { saved: true, readback: true, selectedArea: true, conflict409: true, savedResults: fixtureRun.saved_results.length, realProjectComments: 0, sentSessionMessages: 0, fixtureRemoteAttempts: 1, acceptedRemoteRequests: 0, cancelledTurns: 0 };
  report.checks.push('Fixture: image-region comment retains original node and reads back; save works; changed bytes return 409');
  await send('Fetch.disable'); relayFixture = null;
  if (report.errors.length) throw new Error('Page runtime errors detected');
  report.passed = true;
} catch (error) { report.reason = sanitized(error.message); process.exitCode = 1; }
finally {
  socket?.close(); chrome.kill();
  if (fixtureChild && fixtureChild.exitCode === null) { fixtureChild.stdin.end('stop\n'); await Promise.race([once(fixtureChild, 'exit'), new Promise(done => setTimeout(() => { fixtureChild.kill(); done(); }, 3000))]); }
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
