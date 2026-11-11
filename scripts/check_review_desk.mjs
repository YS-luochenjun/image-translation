// Browser smoke checks through Chrome's local debugging protocol; no npm dependencies.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { once } from 'node:events';

const fixtureMode = process.argv.includes('--fixture');
let target = process.argv.find((arg, index) => index > 1 && arg.startsWith('http')) || 'http://127.0.0.1:8765/';
let fixture;
if (fixtureMode) {
  fixture = spawn('C:/Users/Echo/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe', ['-u', 'scripts/review_fixture_server.py'], { stdio: ['ignore', 'pipe', 'inherit'] });
  target = await new Promise((resolveTarget, reject) => {
    const timer = setTimeout(() => { fixture.kill(); reject(new Error('Fixture did not start')); }, 15000);
    fixture.on('error', reject);
    fixture.stdout.on('data', (chunk) => {
      const match = chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (match) { clearTimeout(timer); resolveTarget(match[0]); }
    });
  });
}
const output = resolve('.review-desk/verification', fixtureMode ? 'fixture' : '.');
await mkdir(output, { recursive: true });
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${resolve('.review-desk/browser-profile')}`, 'about:blank'
], { stdio: ['ignore', 'ignore', 'pipe'] });
let socket;
try {
  const endpoint = await new Promise((resolveEndpoint, reject) => {
    let stderr = '';
    const timeout = setTimeout(() => reject(new Error('Chrome debugging endpoint did not start')), 20000);
    chrome.on('error', reject);
    chrome.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) { clearTimeout(timeout); resolveEndpoint(match[1]); }
    });
  });
  const origin = new URL(endpoint).origin.replace('ws:', 'http:');
  const targets = await (await fetch(`${origin}/json/list`)).json();
  const page = targets.find((item) => item.type === 'page');
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await once(socket, 'open');
  let sequence = 0;
  const pending = new Map();
  const errors = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const request = pending.get(message.id);
      pending.delete(message.id);
      if (request) message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text + ' ' + (message.params.exceptionDetails.exception?.description || ''));
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') errors.push(message.params.entry.text);
  };
  const send = (method, params = {}) => new Promise((resolveCommand, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve: resolveCommand, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const waitFor = async (expression) => {
    const start = Date.now();
    while (Date.now() - start < 20000) {
      if (await evaluate(expression)) return;
      await new Promise((done) => setTimeout(done, 150));
    }
    const debug = await evaluate("({task:window.ImageReviewDesk?.getCurrentTask()?.id,language:window.ImageReviewDesk?.getCurrentTask()?.language,languageSelection:document.querySelector('#language-select')?.value,versionSelection:document.querySelector('#version-select')?.value,dialog:document.querySelector('#draft-dialog')?.open,notice:document.querySelector('#notice')?.textContent,refreshDisabled:document.querySelector('#refresh')?.disabled,busy:document.querySelector('#task-content')?.getAttribute('aria-busy'),dirty:typeof state!=='undefined'?state.dirty:null,saving:typeof state!=='undefined'?state.saving:null})");
    console.error('Browser wait timeout diagnostics:', JSON.stringify(debug));
    throw new Error(`Timed out: ${expression}; state=${JSON.stringify(debug)}`);
  };
  await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: target });
  await waitFor("document.querySelectorAll('.task-item').length > 0 && !document.querySelector('#task-content').hidden && document.querySelector('#task-content').getAttribute('aria-busy') !== 'true'");
  await waitFor("[...document.querySelectorAll('#image-stage img')].every(i => i.complete && i.naturalWidth > 0)");
  const initial = await evaluate("({title: document.title, groups:document.querySelectorAll('.image-group').length, expandedGroups:document.querySelectorAll('.group-toggle[aria-expanded=true]').length, tasks:document.querySelectorAll('.task-item').length, regions:document.querySelectorAll('.region-row').length, rowHeight:document.querySelector('.region-row')?.getBoundingClientRect().height, images:[...document.querySelectorAll('#image-stage img')].map(i=>({w:i.naturalWidth,h:i.naturalHeight})),overflow:document.documentElement.scrollWidth>innerWidth, notice:document.querySelector('#notice').textContent})");
  assert(initial.expandedGroups === 1, 'Exactly the current picture group should be expanded');
  if (initial.regions) assert(initial.rowHeight >= 40 && initial.rowHeight <= 56, `Region rows are not compact: ${initial.rowHeight}`);
  if (fixtureMode) {
    assert(initial.groups === 2, 'Fixture must group five versions into two source pictures');
    const grouping = await evaluate("({languages:document.querySelector('#language-select').options.length, versions:document.querySelector('#version-select').options.length, task:window.ImageReviewDesk.getCurrentTask()?.id})");
    assert(grouping.languages === 2 && grouping.versions === 2, `Fixture language/version grouping failed: ${JSON.stringify(grouping)}; initial=${JSON.stringify(initial)}`);
  }
  assert(initial.images.length === 2, 'Expected source and translated images');
  assert(!initial.overflow, 'Desktop horizontal overflow');
  assert(!initial.notice, `Task loading failed: ${initial.notice}`);
  await writeFile(resolve(output, 'desktop.png'), Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  await evaluate("document.querySelector('[data-mode=wipe]').click(); document.querySelector('#wipe-range').value=25;document.querySelector('#wipe-range').dispatchEvent(new Event('input',{bubbles:true}))");
  assert(await evaluate("!document.querySelector('#wipe-control').hidden && document.querySelector('.wipe-image').style.clipPath.includes('75%')"), 'Wipe control did not update');
  await evaluate("document.querySelector('[data-mode=output]').click();document.querySelector('#actual-size').click()");
  assert(await evaluate("document.querySelector('#image-viewer').classList.contains('actual')"), 'Actual size toggle failed');
  await evaluate("document.querySelector('#actual-size').click();document.querySelector('[data-mode=compare]').click();document.querySelector('#search').value='no-matching-task-xyz';document.querySelector('#search').dispatchEvent(new Event('input'))");
  assert(await evaluate("document.querySelectorAll('.task-item').length === 0"), 'Search filtering failed');
  await evaluate("document.querySelector('#search').value='';document.querySelector('#search').dispatchEvent(new Event('input'))");
  if (initial.regions) {
    await evaluate("document.querySelector('.region-jump').click();document.querySelector('.region-expanded input').value='Browser test draft';document.querySelector('.region-expanded input').dispatchEvent(new Event('input'));document.querySelector('#refresh').click()");
    assert(await evaluate("document.querySelector('#draft-dialog').open"), 'Unsaved draft guard failed');
    await evaluate("document.querySelector('#keep-editing').click()");
    await evaluate("document.querySelector('.region-controls select').value='changes_requested';document.querySelector('.region-controls select').dispatchEvent(new Event('change'));document.querySelector('#region-filter').value='changes_requested';document.querySelector('#region-filter').dispatchEvent(new Event('change'))");
    assert(await evaluate("document.querySelectorAll('.region-row').length === 1 && document.querySelector('.region-expanded input').value === 'Browser test draft'"), 'Region filter or note preservation failed');
    await evaluate("document.querySelector('#approve-visible').click()");
    assert(await evaluate("document.querySelectorAll('.region-row').length === 0 && document.querySelector('#region-batch-hint').textContent.includes('待保存')"), 'Filtered batch approval did not update pending draft');
    await evaluate("document.querySelector('#region-filter').value='all';document.querySelector('#region-filter').dispatchEvent(new Event('change'))");
    assert(await evaluate("document.querySelector('.region-controls select').value === 'approved' && document.querySelector('.region-expanded input').value === 'Browser test draft'"), 'Batch approval discarded region note');
    await evaluate("document.querySelector('#region-filter').value='pending';document.querySelector('#region-filter').dispatchEvent(new Event('change'))");
    assert(await evaluate(`document.querySelectorAll('.region-row').length === ${initial.regions - 1}`), 'Pending filter did not exclude approved region');
    await evaluate("document.querySelector('#region-filter').value='all';document.querySelector('#region-filter').dispatchEvent(new Event('change'))");
  }
  if (fixtureMode) {
    await evaluate("window.__startingTask=window.ImageReviewDesk.getCurrentTask().id;window.__startingLanguage=document.querySelector('#language-select').value;window.__startingVersion=document.querySelector('#version-select').value;document.querySelector('#language-select').value=[...document.querySelector('#language-select').options].find(o=>o.value!==window.__startingLanguage).value;document.querySelector('#language-select').dispatchEvent(new Event('change'))");
    assert(await evaluate("document.querySelector('#draft-dialog').open"), 'Language switch did not protect unsaved draft');
    await evaluate("document.querySelector('#keep-editing').click()");
    assert(await evaluate("document.querySelector('#language-select').value===window.__startingLanguage && window.ImageReviewDesk.getCurrentTask().id===window.__startingTask"), 'Cancelled language switch changed current task');
    await evaluate("document.querySelector('#version-select').value=[...document.querySelector('#version-select').options].find(o=>o.value!==window.__startingVersion).value;document.querySelector('#version-select').dispatchEvent(new Event('change'))");
    assert(await evaluate("document.querySelector('#draft-dialog').open"), 'Version switch did not protect unsaved draft');
    await evaluate("document.querySelector('#keep-editing').click()");
    assert(await evaluate("document.querySelector('#version-select').value===window.__startingVersion"), 'Cancelled version switch lost selection');
    await evaluate("document.querySelector('#reviewer').value='Browser fixture tester';document.querySelector('#decision').value='changes_requested';document.querySelector('#feedback-note').value='Automated isolated test: title needs more spacing.';document.querySelector('#feedback-note').dispatchEvent(new Event('input'));document.querySelector('#feedback-form').requestSubmit()");
    await waitFor("document.querySelectorAll('.history-item').length === 1 && document.querySelector('#notice').textContent.includes('已保存')");
    assert(await evaluate("document.querySelector('.history-item').textContent.includes('Automated isolated test') && document.querySelector('.history-item').textContent.includes('对应当前版本') && document.querySelector('.history-item').textContent.includes('Browser test draft')"), 'Saved feedback did not preserve current-version region notes');
    const revision = await evaluate("window.ImageReviewDesk.getRevisionPrompt()");
    assert(revision.task_id && revision.prompt.includes('Automated isolated test') && revision.prompt.includes('Browser test draft'), 'Revision hook did not include saved feedback and task identity');
    await evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('Clipboard fixture refusal')}}});document.querySelector('#copy-revision').click()");
    await waitFor("!document.querySelector('#revision-fallback').hidden && document.querySelector('#revision-prompt').value.includes('Automated isolated test')");
    await evaluate("document.querySelector('#refresh').click()");
    await waitFor("document.querySelectorAll('.history-item').length === 1 && document.querySelector('#task-content').getAttribute('aria-busy') !== 'true' && !document.querySelector('#refresh').disabled");
    await evaluate("document.querySelector('#language-select').value=[...document.querySelector('#language-select').options].find(o=>o.value!==document.querySelector('#language-select').value).value;document.querySelector('#language-select').dispatchEvent(new Event('change'))");
    await waitFor("window.ImageReviewDesk.getCurrentTask().language!==window.__startingLanguage && document.querySelector('#task-content').getAttribute('aria-busy') !== 'true'");
    assert(await evaluate("document.querySelectorAll('.history-item').length === 0"), 'Feedback leaked across language tasks');
    await evaluate("window.__beforeVersion=window.ImageReviewDesk.getCurrentTask().id;document.querySelector('#version-select').value=[...document.querySelector('#version-select').options].find(o=>o.value!==window.__beforeVersion).value;document.querySelector('#version-select').dispatchEvent(new Event('change'))");
    await waitFor("window.ImageReviewDesk.getCurrentTask().id!==window.__beforeVersion && document.querySelector('#task-content').getAttribute('aria-busy') !== 'true'");
    await evaluate("document.querySelector('.group-toggle[aria-expanded=false]').click()");
    await waitFor("document.querySelector('#language-select').options.length===1 && document.querySelector('#task-content').getAttribute('aria-busy') !== 'true'");
    assert(await evaluate("document.querySelectorAll('.group-toggle[aria-expanded=true]').length===1"), 'Switching pictures expanded multiple groups');
  }
  const widths = [1440, 1024, 768, 390];
  for (const width of widths) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    assert(!(await evaluate('document.documentElement.scrollWidth > innerWidth')), `Page overflow at ${width}px`);
  }
  await writeFile(resolve(output, 'mobile.png'), Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  assert(!errors.length, `Browser errors: ${errors.join('\n')}`);
  const report = { checked_at: new Date().toISOString(), target, fixtureMode, feedback_save_verified: fixtureMode, initial, widths, checks: ['source picture grouping', 'single expanded group', 'compact region row height', 'source/output rendering', 'wipe slider', 'actual size toggle', 'search', 'region detail expansion', 'region filters', 'filtered batch approval preserves notes', 'unsaved draft guard', ...(fixtureMode ? ['language and version guarded switches', 'feedback persistence and language isolation', 'revision instruction hook', 'clipboard refusal manual fallback', 'picture group switch'] : []), 'responsive overflow', 'no browser exceptions'], errors };
  await writeFile(resolve(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally {
  if (socket) socket.close();
  chrome.kill();
  fixture?.kill();
}
