'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { token: '', tasks: [], groups: [], expandedGroup: null, task: null, mode: 'compare', actual: false, selected: null,
  regions: new Map(), dirty: false, requestId: 0, pendingAction: null, saving: false };
const decisions = { approved: '认可', changes_requested: '需要修改', comment: '补充意见' };
const statuses = { needs_review: '待审阅', pending: '待制作', fail: '检查未通过', pass: '技术检查通过' };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function notify(message, error = false) {
  const notice = $('#notice');
  notice.textContent = message;
  notice.className = error ? 'notice error' : 'notice';
  notice.hidden = !message;
}
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'X-Review-Token': state.token,
    ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  let data;
  try { data = await response.json(); } catch { throw new Error('服务返回了无法读取的结果，请刷新页面。'); }
  if (!response.ok) throw new Error(data.error || `读取失败（${response.status}）`);
  return data;
}
function dateLabel(value) {
  if (!value) return '';
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}
function confirmDiscard(action) {
  if (state.saving) { notify('正在保存意见，请稍候再切换任务。'); return; }
  if (!state.dirty) { action(); return; }
  state.pendingAction = action;
  $('#draft-dialog').showModal();
}
function markDirty() { state.dirty = true; }
function taskUrl(id) { return `/api/tasks/${encodeURIComponent(id)}`; }

const languageNames = { 'zh-CN': '简体中文', 'zh-Hant': '繁體中文', 'zh-TW': '繁體中文', ja: '日语', en: '英语', fr: '法语', ko: '韩语', unknown: '语言未标注' };
function languageLabel(value) { return languageNames[value] || value || '语言未标注'; }
function groupTasks(group) { return group.languages.flatMap((item) => item.tasks); }
function currentGroup() { return state.groups.find((group) => groupTasks(group).some((task) => task.id === state.task?.id)); }
function friendlyTitle(value) {
  const title = String(value || '未命名图片').replace(/^(runs|work)\//, '').split('/')[0]
    .replace(/(?:[-_](?:prepared|render|languages?|zh|ja|en|traditional|native|bilingual|art|direction|redo|v\d+|\d{8}))+$/gi, '')
    .replace(/[-_]+/g, ' ').trim();
  return title || '未命名图片';
}
function selectedSummary(group, language) {
  const tasks = group.languages.find((entry) => entry.language === language)?.tasks || groupTasks(group);
  return tasks.find((task) => task.id === state.task?.id) || tasks.find((task) => task.has_output) || tasks[0];
}
function renderTaskList() {
  const query = $('#search').value.trim().toLocaleLowerCase();
  const filter = $('#filter').value;
  const list = $('#task-list'); list.replaceChildren();
  let count = 0;
  for (const group of state.groups) {
    const matching = group.languages.map((entry) => ({ ...entry, tasks: entry.tasks.filter((task) =>
      `${group.title} ${task.title} ${task.language} ${languageLabel(task.language)} ${task.id}`.toLocaleLowerCase().includes(query)
      && (filter === 'all' || (filter === 'output' ? task.has_output : !task.has_output))) })).filter((entry) => entry.tasks.length);
    if (!matching.length) continue;
    count += 1;
    const section = element('section', 'image-group'); section.dataset.groupId = group.id;
    const expanded = group.id === state.expandedGroup;
    const button = element('button', 'group-toggle'); button.type = 'button'; button.setAttribute('aria-expanded', String(expanded));
    if (group.source_url) { const image = element('img', 'group-thumbnail'); image.src = group.source_url; image.alt = ''; image.loading = 'lazy'; button.append(image); }
    else button.append(element('span', 'group-thumbnail missing-thumb', '图'));
    const text = element('span', 'group-title'); text.append(element('strong', '', friendlyTitle(group.title)), element('small', '', `${group.language_count} 种语言 · ${group.version_count} 个版本`));
    button.append(text, element('span', 'group-chevron', expanded ? '−' : '+'));
    button.addEventListener('click', () => {
      if (expanded) { state.expandedGroup = null; renderTaskList(); return; }
      const target = selectedSummary({ ...group, languages: matching });
      if (target && state.task?.id !== target.id) confirmDiscard(() => loadTask(target.id));
      else { state.expandedGroup = group.id; renderTaskList(); }
    });
    section.append(button);
    if (expanded) {
      const languages = element('div', 'group-languages');
      for (const entry of matching) {
        const target = selectedSummary({ ...group, languages: matching }, entry.language);
        const item = element('button', 'task-item', `${languageLabel(entry.language)} · ${entry.tasks.length} 版`); item.type = 'button';
        item.setAttribute('aria-current', String(entry.tasks.some((task) => task.id === state.task?.id)));
        item.addEventListener('click', () => { if (target?.id !== state.task?.id) confirmDiscard(() => loadTask(target.id)); });
        languages.append(item);
      }
      section.append(languages);
    }
    list.append(section);
  }
  $('#task-count').textContent = `${count} / ${state.groups.length}`;
  if (!count) list.append(element('p', 'muted', '没有符合条件的图片。'));
}
function renderSwitchers() {
  const group = currentGroup();
  const languages = $('#language-select'); const versions = $('#version-select');
  languages.replaceChildren(); versions.replaceChildren();
  if (!group) return;
  const active = group.languages.find((entry) => entry.tasks.some((task) => task.id === state.task.id));
  for (const entry of group.languages) { const option = element('option', '', languageLabel(entry.language)); option.value = entry.language; languages.append(option); }
  languages.value = active.language;
  active.tasks.forEach((task, index) => {
    const label = task.version_label || (task.has_output ? `版本 ${active.tasks.length - index}` : '待制作');
    const option = element('option', '', `${label} · ${task.has_output ? '已有译图' : '待制作'}${task.updated_at ? ` · ${dateLabel(task.updated_at)}` : ''}`);
    option.value = task.id; versions.append(option);
  });
  versions.value = state.task.id;
  $('#version-count').textContent = `此语言 ${active.tasks.length} 个版本`;
}
async function refreshTasks() {
  const button = $('#refresh');
  const selectionEpoch = state.requestId;
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  try {
    const result = await api('/api/tasks');
    state.groups = result.groups || (result.tasks || []).map((task) => ({ id: task.id, title: task.title, source_url: task.source_url, language_count: 1, version_count: 1, languages: [{ language: task.language || 'unknown', tasks: [task] }] }));
    state.tasks = state.groups.flatMap(groupTasks);
    renderTaskList();
    const warnings = result.warnings || [];
    $('#warnings').hidden = !warnings.length;
    $('#warnings div').replaceChildren(...warnings.map((warning) => element('p', '', typeof warning === 'string' ? warning : JSON.stringify(warning))));
    if (selectionEpoch !== state.requestId) { renderSwitchers(); return; }
    const hashId = (() => { try { return decodeURIComponent(location.hash.slice(1)); } catch { return ''; } })();
    const selected = state.task?.id || hashId;
    const initial = state.tasks.find((task) => task.id === selected) || state.tasks.find((task) => task.has_output) || state.tasks[0];
    if (initial) await loadTask(initial.id);
    else {
      state.task = null;
      $('#task-content').hidden = true;
      $('#empty').hidden = false;
      $('#empty p').textContent = '尚未找到任务。先在项目中完成 init-job 或 init，再刷新任务。';
    }
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
}
async function loadTask(id) {
  const requestId = ++state.requestId;
  $('#task-content').setAttribute('aria-busy', 'true');
  notify('正在读取任务…');
  try {
    const task = await api(taskUrl(id));
    if (requestId !== state.requestId) return;
    state.task = task;
    state.regions = new Map();
    state.selected = null;
    state.dirty = false;
    $('#feedback-note').value = '';
    $('#decision').value = task.output_url ? 'changes_requested' : 'comment';
    $('#decision option[value=approved]').disabled = !task.output_url;
    $('#form-error').hidden = true;
    history.replaceState(null, '', `#${encodeURIComponent(id)}`);
    state.expandedGroup = currentGroup()?.id || null;
    $('#task-title').textContent = friendlyTitle(currentGroup()?.title || task.title);
    $('#task-path').textContent = task.id;
    $('#task-meta').textContent = `${task.language || '语言待确认'} · ${task.kind === 'layered' ? '分层制作' : '基础制作'} · ${task.regions.length} 个文字区域`;
    $('#task-status').textContent = task.status === 'fail' ? '检查未通过 · 待处理' : task.output_url ? '待审阅当前版本' : '等待制作结果';
    $('#source-hash').textContent = task.source_sha256 || '尚无原图';
    $('#manifest-label').textContent = task.quality?.manifest_binding === 'render_plan' ? '配置指纹' : '译稿指纹';
    $('#output-hash').textContent = task.output_sha256 || '尚无译图';
    $('#output-hash').title = task.output_sha256 || '';
    $('#manifest-hash').textContent = task.manifest_sha256 || '暂无译稿记录';
    $('#empty').hidden = true;
    $('#task-content').hidden = false;
    $('#layer').value = task.output_url ? 'output' : 'source';
    for (const option of $('#layer').options) option.disabled = !layerUrl(option.value);
    $('#revision-fallback').hidden = true;
    $('#revision-status').textContent = '使用当前版本最新已保存意见。';
    $('#region-filter').value = 'all';
    $('#region-batch-hint').textContent = '点击编号展开备注；区域标记保存后生效。';
    renderTaskList(); renderSwitchers(); renderImages(); renderRegions(); renderHistory(); renderEvidence();
    window.dispatchEvent(new CustomEvent('image-review-task-change'));
    notify('');
  } catch (error) { if (requestId === state.requestId) notify(error.message, true); }
  finally { if (requestId === state.requestId) $('#task-content').removeAttribute('aria-busy'); }
}
function layerUrl(layer = $('#layer').value) {
  const task = state.task;
  if (!task) return null;
  return ({ source: task.source_url, output: task.output_url, background: task.background_url, text: task.text_layer_url })[layer];
}
function createFigure(url, label, wipe = false) {
  const figure = element('figure', 'image-figure');
  figure.append(element('figcaption', '', label));
  if (!url) { figure.append(element('div', 'missing-image', '当前尚无图片产物')); return figure; }
  const wrap = element('div', 'image-wrap');
  const image = element('img');
  image.alt = `${state.task.title} · ${label}`;
  image.src = url;
  image.addEventListener('load', () => {
    if (!figure.isConnected) return;
    $('#image-size').textContent = `${image.naturalWidth} × ${image.naturalHeight} px · ${state.actual ? '原尺寸，可滚动查看' : '适应画布'}`;
    addOverlays(wrap, image.naturalWidth, image.naturalHeight);
  });
  image.addEventListener('error', () => {
    wrap.replaceChildren(element('div', 'missing-image', '图片无法载入，请刷新任务检查文件是否仍存在。'));
  });
  wrap.append(image);
  if (wipe && state.task.output_url) {
    const overlay = element('img', 'wipe-image');
    overlay.alt = '当前译图叠层'; overlay.src = state.task.output_url;
    overlay.style.clipPath = `inset(0 ${100 - Number($('#wipe-range').value)}% 0 0)`;
    wrap.append(overlay);
  }
  figure.append(wrap);
  return figure;
}
function addOverlays(wrap, width, height) {
  wrap.querySelectorAll('.region-overlay').forEach((node) => node.remove());
  if (!$('#show-regions').checked) return;
  state.task.regions.forEach((region, index) => {
    const box = region.bbox;
    if (!Array.isArray(box) || box.length !== 4 || box.some((n) => !Number.isFinite(n)) || box[2] <= 0 || box[3] <= 0) return;
    const button = element('button', `region-overlay${state.selected === region.id ? ' selected' : ''}`);
    button.type = 'button';
    button.style.left = `${box[0] / width * 100}%`;
    button.style.top = `${box[1] / height * 100}%`;
    button.style.width = `${box[2] / width * 100}%`;
    button.style.height = `${box[3] / height * 100}%`;
    button.setAttribute('aria-label', `定位区域 ${index + 1}：${region.source_text || region.id}`);
    button.title = `${region.id}：${region.source_text || ''}`;
    button.append(element('span', '', index + 1));
    button.addEventListener('click', () => selectRegion(region.id, true));
    wrap.append(button);
  });
}
function renderImages() {
  if (!state.task) return;
  const stage = $('#image-stage');
  stage.replaceChildren();
  stage.className = state.mode === 'compare' ? '' : 'single';
  $('#image-viewer').classList.toggle('actual', state.actual);
  const url = layerUrl();
  const label = $('#layer option:checked').textContent;
  if (state.mode === 'compare') stage.append(createFigure(state.task.source_url, '原图'), createFigure(url, label));
  else if (state.mode === 'wipe') stage.append(createFigure(state.task.source_url, '左侧译图 / 右侧原图', true));
  else stage.append(createFigure(url, label));
  $('#wipe-control').hidden = state.mode !== 'wipe';
  $('#open-image').hidden = !url;
  if (url) $('#open-image').href = url;
  $('#image-hint').textContent = state.mode === 'wipe' ? '滑杆始终比较原图与最终译图' : '';
  document.querySelector('[data-mode=wipe]').disabled = !state.task.output_url || !state.task.source_url;
  if (state.mode === 'wipe' && !state.task.output_url) {
    state.mode = 'compare';
    document.querySelectorAll('[data-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mode === state.mode)));
    renderImages();
  }
}
function selectRegion(id, scroll = false) {
  state.selected = state.selected === id && !scroll ? null : id;
  if (scroll) $('#region-filter').value = 'all';
  renderRegions();
  document.querySelectorAll('.image-wrap').forEach((wrap) => {
    const image = wrap.querySelector('img');
    if (image?.naturalWidth) addOverlays(wrap, image.naturalWidth, image.naturalHeight);
  });
  if (scroll) Array.from(document.querySelectorAll('.region-row')).find((row) => row.dataset.regionId === id)?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
}
function visibleRegions() {
  const filter = $('#region-filter').value;
  return state.task.regions.filter((region) => {
    const decision = state.regions.get(region.id)?.decision;
    return filter === 'all' || (filter === 'pending' ? decision !== 'approved' : decision === 'changes_requested');
  });
}
function updateDraftHint() {
  $('#draft-hint').textContent = state.regions.size ? `已标记 ${state.regions.size} 个区域 · 待保存` : '逐区意见随本轮反馈一起保存。';
}
function renderRegions() {
  const list = $('#regions'); list.replaceChildren();
  const visible = visibleRegions();
  $('#region-count').textContent = `${visible.length} / ${state.task.regions.length}`;
  $('#approve-visible').disabled = !visible.length || !state.task.output_url || state.saving;
  updateDraftHint();
  if (!visible.length) {
    const row = element('tr'); const cell = element('td', 'region-empty', state.task.regions.length ? '没有符合条件的区域。' : '此版本没有绑定区域译稿，仍可保存整图意见。');
    cell.colSpan = 4; row.append(cell); list.append(row);
  }
  for (const region of visible) {
    const index = state.task.regions.indexOf(region);
    const draft = state.regions.get(region.id) || { id: region.id, decision: '', note: '' };
    const selected = state.selected === region.id;
    const row = element('tr', `region-row${selected ? ' selected' : ''}`); row.dataset.regionId = region.id;
    const number = element('td');
    const jump = element('button', 'region-jump', `${selected ? '−' : '+'} ${String(index + 1).padStart(2, '0')}`);
    jump.type = 'button'; jump.title = `${region.id} · 展开备注与说明`; jump.setAttribute('aria-expanded', String(selected));
    jump.addEventListener('click', () => selectRegion(region.id)); number.append(jump);
    const source = element('td', 'region-copy', region.source_text || '—');
    const target = element('td', 'region-copy', region.target_text || (region.action === 'preserve' ? '保留原文' : '—'));
    source.title = region.source_text || ''; target.title = region.target_text || '';
    const conclusion = element('td', 'region-controls'); const decision = element('select');
    decision.setAttribute('aria-label', `区域 ${region.id} 的结论`); decision.disabled = state.saving;
    for (const [value, label] of [['', '待处理'], ...Object.entries(decisions)]) { const option = element('option', '', label); option.value = value; option.disabled = value === 'approved' && !state.task.output_url; decision.append(option); }
    decision.value = draft.decision;
    decision.addEventListener('change', () => {
      markDirty();
      const note = state.regions.get(region.id)?.note || '';
      if (decision.value || note) state.regions.set(region.id, { id: region.id, decision: decision.value || 'comment', note });
      else state.regions.delete(region.id);
      renderRegions();
    });
    conclusion.append(decision); if (draft.note) conclusion.append(element('span', 'note-indicator', '有备注'));
    row.append(number, source, target, conclusion); list.append(row);
    if (selected) {
      const detailRow = element('tr', 'region-expanded'); const cell = element('td'); cell.colSpan = 4;
      const label = element('label', '', `${region.id} · ${region.action === 'preserve' ? '保留原文' : '翻译'} · 区域备注`);
      const note = element('input'); note.type = 'text'; note.maxLength = 2000; note.value = draft.note; note.placeholder = '只写这个区域需要调整的内容'; note.disabled = state.saving; note.setAttribute('aria-label', `区域 ${region.id} 的意见`);
      note.addEventListener('input', () => {
        markDirty(); const previous = state.regions.get(region.id) || draft;
        const updated = { id: region.id, decision: previous.decision || 'comment', note: note.value };
        if (updated.note || previous.decision) state.regions.set(region.id, updated); else state.regions.delete(region.id);
        decision.value = updated.decision; updateDraftHint();
      });
      label.append(note); cell.append(label);
      const full = element('div', 'region-full-copy'); full.append(element('span', '', `原文：${region.source_text || '—'}`), element('span', '', `译文：${region.target_text || '—'}`)); cell.append(full);
      if (region.notes) { const details = element('details', 'region-detail'); details.append(element('summary', '', '制作说明'), element('p', '', typeof region.notes === 'string' ? region.notes : JSON.stringify(region.notes))); cell.append(details); }
      detailRow.append(cell); list.append(detailRow);
    }
  }
}
function renderHistory() {
  const history = $('#history'); history.replaceChildren();
  const records = [...(state.task.feedback || [])].reverse();
  $('#history-count').textContent = `${records.length} 条`;
  if (!records.length) history.append(element('p', 'muted', '尚无用户审阅记录。'));
  for (const record of records) {
    const stale = record.source_sha256 !== state.task.source_sha256 || record.output_sha256 !== state.task.output_sha256 || record.manifest_sha256 !== state.task.manifest_sha256;
    const article = element('article', `history-item${stale ? ' stale' : ''}`);
    const header = element('header'); header.append(element('strong', '', decisions[record.decision] || record.decision), element('span', 'muted', record.reviewer));
    article.append(header, element('p', '', record.note), element('small', '', `${dateLabel(record.created_at || record.timestamp)} · ${stale ? '历史版本意见' : '对应当前版本'}`));
    if (record.regions?.length) {
      const details = element('details'); details.append(element('summary', '', `${record.regions.length} 条逐区意见`));
      record.regions.forEach((region) => details.append(element('p', '', `${region.id} · ${decisions[region.decision] || region.decision}：${region.note || '无补充说明'}`)));
      article.append(details);
    }
    history.append(article);
  }
}
function renderEvidence() {
  const body = $('#evidence-body'); body.replaceChildren();
  body.append(element('p', 'muted', '以下为已有制作记录。技术检查与用户认可分别保存。'));
  body.append(element('pre', '', JSON.stringify({ quality: state.task.quality, artifacts: state.task.artifacts }, null, 2)));
}
async function saveFeedback(event) {
  event.preventDefault();
  if (state.saving || !state.task || $('#task-content').getAttribute('aria-busy') === 'true') return;
  const note = $('#feedback-note').value.trim(); const reviewer = $('#reviewer').value.trim();
  const errorBox = $('#form-error'); errorBox.hidden = true;
  $('#feedback-note').setAttribute('aria-invalid', String(!note)); $('#reviewer').setAttribute('aria-invalid', String(!reviewer));
  if (!note || !reviewer) { errorBox.textContent = '请填写审阅人和具体审阅说明后保存。'; errorBox.hidden = false; return; }
  const regionNotes = [...state.regions.values()];
  if ($('#decision').value === 'approved' && regionNotes.some((region) => region.decision === 'changes_requested')) {
    errorBox.textContent = '仍有区域标记为需要修改，请将整图结论改为需要修改，或调整区域结论。'; errorBox.hidden = false; return;
  }
  const button = $('#save-feedback'); state.saving = true; button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = '正在保存…';
  document.querySelectorAll('#feedback-form input, #feedback-form select, #feedback-form textarea, .region-expanded input, .region-controls select, #approve-visible').forEach((control) => { control.disabled = true; });
  try {
    await api(`${taskUrl(state.task.id)}/feedback`, { method: 'POST', body: JSON.stringify({ decision: $('#decision').value,
      note, reviewer, source_sha256: state.task.source_sha256, output_sha256: state.task.output_sha256, manifest_sha256: state.task.manifest_sha256, regions: regionNotes }) });
    try { localStorage.setItem('image-reviewer-name', reviewer); } catch { /* Storage is optional; feedback is saved on disk. */ }
    state.dirty = false;
    await loadTask(state.task.id);
    notify('审阅意见已保存，并绑定到当前图片版本。');
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
  finally {
    state.saving = false; button.disabled = false; button.removeAttribute('aria-busy'); button.textContent = '保存审阅意见';
    document.querySelectorAll('#feedback-form input, #feedback-form select, #feedback-form textarea, .region-expanded input, .region-controls select, #approve-visible').forEach((control) => { control.disabled = false; });
    $('#approve-visible').disabled = !state.task?.output_url || !visibleRegions().length;
  }
}
$('#language-select').addEventListener('change', (event) => {
  const group = currentGroup(); const target = selectedSummary(group, event.target.value);
  renderSwitchers(); if (target && target.id !== state.task.id) confirmDiscard(() => loadTask(target.id));
});
$('#version-select').addEventListener('change', (event) => {
  const id = event.target.value; renderSwitchers(); if (id !== state.task?.id) confirmDiscard(() => loadTask(id));
});
$('#region-filter').addEventListener('change', renderRegions);
$('#approve-visible').addEventListener('click', () => {
  if (state.saving || !state.task?.output_url) return;
  const visible = visibleRegions();
  for (const region of visible) state.regions.set(region.id, { id: region.id, decision: 'approved', note: state.regions.get(region.id)?.note || '' });
  if (!visible.length) return;
  markDirty(); renderRegions();
  $('#region-batch-hint').textContent = `已将本页 ${visible.length} 个区域标记为认可，原备注已保留；待保存。`;
});
async function getRevisionPrompt() {
  if (!state.task) throw new Error('请先选择图片版本。');
  return api(`${taskUrl(state.task.id)}/revision-prompt`);
}
let hostConfig = null;
window.ImageReviewDesk = Object.freeze({
  getCurrentTask: () => state.task ? JSON.parse(JSON.stringify(state.task)) : null,
  getHostConfig: () => hostConfig ? { ...hostConfig } : null,
  hasUnsavedChanges: () => state.dirty || state.saving || $('#task-content').getAttribute('aria-busy') === 'true',
  getRevisionPrompt
});
$('#copy-revision').addEventListener('click', async () => {
  const button = $('#copy-revision'); const id = state.task?.id;
  button.disabled = true; button.setAttribute('aria-busy', 'true');
  try {
    const result = await getRevisionPrompt();
    if (state.task?.id !== id) return;
    if (typeof result.prompt !== 'string') throw new Error('返工指令内容无法读取。');
    $('#revision-prompt').value = result.prompt;
    try {
      await navigator.clipboard.writeText(result.prompt);
      $('#revision-status').textContent = state.dirty ? '已复制已保存意见的指令；本轮未保存输入尚未包含。' : '已复制，可粘贴到当前项目会话。';
      $('#revision-fallback').hidden = true;
    } catch {
      $('#revision-fallback').hidden = false;
      $('#revision-prompt').focus(); $('#revision-prompt').select();
      $('#revision-status').textContent = state.dirty ? '请选择下方文字手动复制；未保存意见尚未包含。' : '浏览器未允许剪贴板，请选中下方文字手动复制。';
    }
  } catch (error) { $('#revision-status').textContent = error.message; }
  finally { button.disabled = false; button.removeAttribute('aria-busy'); }
});
$('#search').addEventListener('input', renderTaskList);
$('#filter').addEventListener('change', renderTaskList);
$('#refresh').addEventListener('click', () => confirmDiscard(refreshTasks));
$('#feedback-form').addEventListener('submit', saveFeedback);
for (const id of ['reviewer', 'feedback-note', 'decision']) $(`#${id}`).addEventListener('input', markDirty);
$('#layer').addEventListener('change', renderImages);
$('#show-regions').addEventListener('change', renderImages);
$('#actual-size').addEventListener('click', () => { state.actual = !state.actual; $('#actual-size').setAttribute('aria-pressed', String(state.actual)); $('#actual-size').textContent = state.actual ? '适应画布' : '原尺寸'; renderImages(); });
for (const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click', () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll('[data-mode]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  renderImages();
});
$('#wipe-range').addEventListener('input', (event) => {
  const value = Number(event.target.value); $('#wipe-control output').textContent = `${value}%`;
  document.querySelectorAll('.wipe-image').forEach((image) => { image.style.clipPath = `inset(0 ${100 - value}% 0 0)`; });
});
$('#keep-editing').addEventListener('click', () => { state.pendingAction = null; $('#draft-dialog').close(); });
$('#discard-draft').addEventListener('click', () => { const action = state.pendingAction; state.pendingAction = null; state.dirty = false; $('#draft-dialog').close(); action?.(); });
window.addEventListener('beforeunload', (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
(async () => {
  try {
    try { $('#reviewer').value = localStorage.getItem('image-reviewer-name') || ''; } catch { /* Browser storage is optional. */ }
    const response = await fetch('/api/bootstrap');
    if (!response.ok) throw new Error('本地审阅服务尚未就绪，请通过项目启动入口重新打开。');
    const bootstrap = await response.json();
    state.token = bootstrap.token;
    hostConfig = { host_origin: bootstrap.host_origin || null, project_id: bootstrap.project_id };
    window.dispatchEvent(new CustomEvent('image-review-bridge-ready'));
    await refreshTasks();
  } catch (error) { notify(error.message, true); }
})();
