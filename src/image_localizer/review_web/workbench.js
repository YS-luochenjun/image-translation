'use strict';

const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);
const displayTime = value => {
  const timestamp = new Date(value);
  return value && Number.isFinite(timestamp.valueOf())
    ? new Intl.DateTimeFormat('zh-CN', {timeZone: 'America/Los_Angeles', dateStyle: 'short', timeStyle: 'medium', hour12: false}).format(timestamp)
    : '未记录';
};
const pages = {
  ledger: ['需求台账', '查看 Fireplay 需求进度、交付核验和花费。'],
  efficiency: ['效率分析', '查看需求开工至结案耗时、费用回执和语种渲染样本。'],
  tasks: ['任务与图片', '查看已下载的原图、工作包和可审阅的译图版本。'],
  sessions: ['会话总览', '查看本项目登记的负责会话及对应需求。'],
  automations: ['自动任务', '查看本项目巡检配置与本地执行回执。'],
  knowledge: ['知识与调用', '查找现行方法、图片案例与暂定方法。'],
  system: ['运行概况', '查看会话用量、费用回执和质量门禁。'],
};
const labels = {
  progress: {done: '已完成', active: '进行中', blocked: '阻塞', waiting: '待开始', closed: '已关闭'},
  platform: {not_started: '未开始', in_progress: '进行中', pending_upload: '待上传', completed: '已完成', done: '已完成', cancelled: '已取消', canceled: '已取消'},
  dispatch: {dispatched: '已派发', accepted: '已承接', executing: '制作中', partial: '部分交付', awaiting_owner_closeout: '交付齐套，待原会话结案', verified_complete: '已核验', delivery_verified: '已核验', blocked: '阻塞', unknown: '待核对', dispatch_unknown: '派发待核对', ready: '待开始'},
  evidence: {pass: '已通过', fail: '未通过', pending: '待检查', unknown: '待核对'},
};
const color = {done: 'good', active: 'warn', blocked: 'bad', pass: 'good', pending: 'warn', fail: 'bad'};
const state = {
  snapshot: null, tasks: null, monitor: null, usage: null, selected: '', page: 'ledger',
  loading: false, refreshQueued: false, token: '', signature: '',
};
const knowledgeState = {category: 'method', query: '', page: 1};

const badge = (value, tone = '') => `<span class="badge ${tone}">${escapeHtml(value)}</span>`;
const status = (category, value) => badge(labels[category][value] || value || '未记录', color[value] || '');
const empty = message => `<div class="empty panel">${escapeHtml(message)}</div>`;
const cards = entries => `<div class="cards">${entries.map(([label, value, note]) =>
  `<div class="card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`).join('')}</div>`;
const fields = entries => `<dl class="info-grid">${entries.map(([label, value]) =>
  `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? '未记录')}</dd></div>`).join('')}</dl>`;
const table = (title, headers, rows, className = '') => `
  <div class="table-wrap ${className}" role="region" aria-label="${escapeHtml(title)}" tabindex="0">
    <table><caption>${escapeHtml(title)}</caption><thead><tr>${headers.map(header => `<th scope="col">${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>
  </div>`;
const fold = (id, title, count, body, open = false) => `
  <details id="${id}" class="fold-panel"${open ? ' open' : ''}>
    <summary><span>${escapeHtml(title)}</span><span class="badge">${count}</span></summary>
    <div class="fold-body">${body}</div>
  </details>`;
const quantity = (value, suffix = '') => value == null ? '未记录' : `${value}${suffix}`;
const taskReviewUrl = id => `/review#${encodeURIComponent(id)}`;
const knownSource = url => typeof url === 'string' && url.startsWith('/api/workbench/source?') ? url : '';
const knownTaskImage = url => typeof url === 'string' && url.startsWith('/api/assets?path=') ? url : '';
const sessionName = row => row.thread_title || (row.thread_id ? '会话名称未记录' : '未绑定');
const locales = row => row.locales.join(' · ') || '语种未记录';
const memberProgress = row => row.expected == null || row.verified == null
  ? '待更新核验' : `${row.verified}/${row.expected}`;
const accounting = row => state.usage?.requirements?.[row.code] || {};
const costText = costs => Object.entries(costs || {}).map(([unit, amount]) => `${amount} ${unit}`).join(' · ');
const formatTokens = amount => Number.isSafeInteger(amount) ? amount.toLocaleString('zh-CN') : '—';
function duration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor(rounded % 3600 / 60);
  if (hours) return `${hours} 小时 ${minutes} 分钟`;
  return minutes ? `${minutes} 分钟 ${rounded % 60} 秒` : `${rounded} 秒`;
}
function costCell(row) {
  const {costs, estimated_hdk: estimate} = accounting(row);
  const recorded = costText(costs);
  if (recorded) return `${escapeHtml(recorded)}<span class="quiet">已登记实耗</span>`;
  return estimate == null ? '<span class="quiet">—</span>' : `<span class="quiet">估算 ${escapeHtml(estimate)} HDK</span>`;
}
const exposeArchivedSelection = () => {
  if (state.selected && state.page === 'ledger'
      && state.snapshot?.requirements.find(row => row.code === state.selected)?.archived) byId('scope').value = 'all';
};

function capacityPanel() {
  const capacity = state.snapshot.capacity;
  if (!capacity) return '<p class="panel capacity quiet">制作名额记录暂不可用</p>';
  return `<details class="panel capacity" id="capacity"><summary>制作名额 <strong>${capacity.active} / ${capacity.limit}</strong></summary>
    <p class="quiet">剩余 ${capacity.available} 个需求名额，按本项目承接和会话登记计算。</p></details>`;
}

function renderLedger() {
  const trackable = state.snapshot.requirements.filter(row => !row.archived);
  const query = byId('search').value.trim().toLowerCase();
  const progress = byId('progress').value;
  const all = byId('scope').value === 'all';
  const rows = (all ? state.snapshot.requirements : trackable).filter(row =>
    (progress === 'all' || progress === row.progress)
    && [row.code, sessionName(row), ...row.locales].some(value => value.toLowerCase().includes(query)));
  byId('overview').innerHTML = cards([
    ['需求总数', trackable.length, '当前跟踪范围'],
    ['进行中', trackable.filter(row => row.progress === 'active').length, '已承接或正在制作'],
    ['交付已核验', state.snapshot.requirements.filter(row => row.delivery_verified).length, '含历史核验记录'],
  ]);
  const rowMarkup = rows.map(row => `
    <tr data-progress="${row.progress}" data-selected="${state.selected === row.code}">
      <td><button class="table-action" data-select="${escapeHtml(row.code)}" aria-controls="detail" aria-expanded="${state.selected === row.code}">${escapeHtml(row.code)}</button>
        <span class="quiet">${escapeHtml(quantity(row.source_count, ' 张原图'))} · ${escapeHtml(locales(row))}</span></td>
      <td>${status('progress', row.progress)}${row.progress === 'active' && ['partial', 'awaiting_owner_closeout'].includes(row.state)
        ? `<span class="quiet">${escapeHtml(labels.dispatch[row.state])}</span>` : ''}${row.platform_status && labels.platform[row.platform_status] !== labels.progress[row.progress]
        ? `<span class="quiet">平台${escapeHtml(labels.platform[row.platform_status] || row.platform_status)}</span>` : ''}</td>
      <td>${duration(row.timing?.elapsed_seconds)}${row.timing?.started_at && !row.timing.closed_at ? '<span class="quiet">待结案</span>' : ''}</td>
      <td>${costCell(row)}</td>
      <td>${escapeHtml(sessionName(row))}</td>
      <td>${escapeHtml(memberProgress(row))}<span class="quiet">${row.delivery_verified ? '主控已核验' : '已核验 / 计划成员'}</span></td>
      <td>${escapeHtml(row.request_date || '未记录')}<span class="quiet">${escapeHtml(row.priority)}</span></td>
    </tr>`).join('');
  const capacityOpen = byId('capacity')?.open;
  byId('content').innerHTML = capacityPanel() + (rows.length
    ? table(`${rows.length} 条需求记录`, ['需求', '进度与平台', '开工至结案', '花费', '负责会话', '交付核验', '需求日期'], rowMarkup, 'ledger-table requirement-table')
    : empty('没有符合当前筛选条件的需求。'));
  if (capacityOpen && byId('capacity')) byId('capacity').open = true;
  renderDetail(rows.find(row => row.code === state.selected));
}

function sourceCard(source) {
  const url = knownSource(source.url);
  const title = source.name || `原图 ${source.index}`;
  const size = source.width && source.height ? ` · ${source.width} × ${source.height}` : '';
  const preview = url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">
    <img src="${escapeHtml(url)}" alt="${escapeHtml(title)}" loading="lazy"></a>`
    : '<div class="image-placeholder quiet">暂无本地原图</div>';
  return `<article class="image-card">${preview}<p>${escapeHtml(title)}<span class="quiet">${escapeHtml(size)}</span></p></article>`;
}

function renderDetail(row) {
  const detail = byId('detail');
  detail.hidden = !row;
  if (!row) {
    detail.replaceChildren();
    delete detail.dataset.requirement;
    return;
  }
  const accountingOpen = detail.dataset.requirement === row.code && detail.querySelector('.detail-accounting')?.open;
  detail.dataset.requirement = row.code;
  const blockers = row.blockers.map(message => `<li>${escapeHtml(message)}</li>`).join('');
  const {costs, estimated_hdk: estimate, verified_generations: generations, tokens, shared_session: sharedSession} = accounting(row);
  const detailAccounting = fields([
    ['平台开工', displayTime(row.timing?.started_at)],
    ['平台结案', displayTime(row.timing?.closed_at)],
    ['开工至结案（含等待）', duration(row.timing?.elapsed_seconds)],
    ['已登记实耗', costText(costs) || '未记录'],
    ['生成费用估算', estimate == null ? '未记录' : `${estimate} HDK${generations ? ` · ${generations} 次生成` : ''}`],
    ['Codex 会话 Token', sharedSession ? '多需求共用会话' : formatTokens(tokens?.total)],
  ]);
  detail.innerHTML = `<div class="section-head"><h2 tabindex="-1">${escapeHtml(row.code)}</h2><button data-close-detail>关闭详情</button></div>
    ${fields([
      ['平台状态', labels.platform[row.platform_status] || row.platform_status || null],
      ['本地状态', labels.dispatch[row.state] || row.state || null],
      ['负责会话', sessionName(row)],
      ['交付成员', memberProgress(row)],
      ['语种', locales(row)],
      ['需求日期', row.request_date],
      ['平台记录时间', displayTime(row.platform_updated_at)],
    ])}
    <details class="detail-accounting fold-panel"${accountingOpen ? ' open' : ''}>
      <summary><span>需求时间与花费</span></summary>
      <div class="fold-body">${detailAccounting}</div>
    </details>
    ${row.operation_pending ? '<p class="notice">有操作回执待核对。</p>' : ''}
    ${blockers ? `<details><summary>接入记录中的问题</summary><ul>${blockers}</ul></details>` : ''}
    ${row.sources.length ? `<div class="image-grid">${row.sources.map(sourceCard).join('')}</div>` : ''}`;
}

function versionTitle(path) {
  const production = path.match(/^work\/fireplay-production\/([^/]+)\/(?:artwork\/)?source-(\d+)(?:-([^/]+))?(?:\/([^/]+))?/);
  if (production) {
    const [, code, source, variant, stage] = production;
    const label = [code, `原图 ${source}`];
    if (variant) label.push(variant);
    if (stage && /^render/.test(stage)) label.push(stage.replace(/^render(?:ed)?/, '成图').replace(/-/g, ' '));
    return label.join(' · ');
  }
  const workPackage = path.match(/^runs\/([^/]+)\/languages\/[^/]+$/);
  if (workPackage) return workPackage[1];
  return path.replace(/^(work|runs)\//, '').split('/').slice(-3).join(' / ');
}

function renderTasks() {
  const taskRows = state.tasks?.tasks || [];
  const groups = state.tasks?.groups || [];
  const sourceRows = state.snapshot.requirements.filter(row => !row.archived && row.sources.length);
  byId('overview').innerHTML = cards([
    ['已下载原图', sourceRows.reduce((total, row) => total + row.sources.filter(source => source.url).length, 0), '当前需求'],
    ['制作原图组', groups.length, '按同一原图归组'],
    ['已有译图', taskRows.filter(task => task.has_output).length, '可进入版本审阅'],
  ]);
  const requirements = sourceRows.length ? table('当前需求的原图', ['需求', '下载进度', '目标语种'], sourceRows.map(row => `
    <tr data-selected="${state.selected === row.code}"><td><button class="table-action" data-select="${escapeHtml(row.code)}" aria-controls="detail" aria-expanded="${state.selected === row.code}">${escapeHtml(row.code)}</button></td>
    <td>${row.sources.filter(source => source.url).length}/${row.sources.length}</td><td>${escapeHtml(locales(row))}</td></tr>`).join(''))
    : empty('当前需求还没有可索引的原图。');
  const versionRows = taskRows.map(task => {
    const path = String(task.title || task.id).split('::')[0];
    return `<tr><td title="${escapeHtml(path)}">${escapeHtml(versionTitle(path))}</td><td>${escapeHtml(task.language)}</td>
      <td>${badge(task.has_output ? '已有译图' : '待制作', task.has_output ? 'good' : '')}</td>
      <td><a href="${escapeHtml(taskReviewUrl(task.id))}">查看版本</a></td></tr>`;
  }).join('');
  const versions = versionRows
    ? table('制作版本明细', ['版本', '语种', '状态', '操作'], versionRows, 'fold-scroll version-table')
    : '<p class="empty">还没有本地工作包或译图版本。</p>';
  const previews = groups.filter(group => knownTaskImage(group.source_url)).slice(0, 12).map(group => {
    const latest = taskRows.find(task => task.group_id === group.id);
    const title = versionTitle(String(latest.title || latest.id).split('::')[0]);
    return `<article class="image-card"><a href="${escapeHtml(taskReviewUrl(latest.id))}">
      <img src="${escapeHtml(group.source_url)}" alt="${escapeHtml(title)}" loading="lazy"></a><p>${escapeHtml(title)}</p></article>`;
  });
  const previewPanel = previews.length
    ? fold('source-previews', '原图组预览', previews.length,
      `<div class="fold-scroll" role="region" aria-label="原图组预览明细" tabindex="0"><div class="image-grid">${previews.join('')}</div></div>`)
    : '';
  byId('content').innerHTML = `<div class="stack">${requirements}${fold('local-versions', '本地制作版本', taskRows.length, versions)}${previewPanel}</div>`;
  renderDetail(sourceRows.find(row => row.code === state.selected));
}

function renderEfficiency() {
  const requirements = state.snapshot.requirements;
  const samples = requirements.filter(row => row.delivery_verified && Number.isFinite(row.timing?.elapsed_seconds));
  const mean = samples.length ? samples.reduce((total, row) => total + row.timing.elapsed_seconds, 0) / samples.length : null;
  const costCount = requirements.filter(row => Object.keys(accounting(row).costs || {}).length).length;
  const estimateCount = requirements.filter(row => accounting(row).estimated_hdk != null).length;
  byId('overview').innerHTML = cards([
    ['结案耗时样本', samples.length, '交付已核验且有开工和结案回执'],
    ['平均结案耗时', duration(mean), '含制作、等待与上传核验'],
    ['有实耗记录的需求', costCount, `另有 ${estimateCount} 单有生成费用估算`],
  ]);
  const recordedRows = requirements.filter(row => row.timing?.started_at || row.timing?.closed_at
    || Object.keys(accounting(row).costs || {}).length || accounting(row).estimated_hdk != null
    || Number.isSafeInteger(accounting(row).tokens?.total));
  const requirementRows = recordedRows.map(row => `<tr>
    <td><a href="#ledger/${encodeURIComponent(row.code)}">${escapeHtml(row.code)}</a></td>
    <td>${duration(row.timing?.elapsed_seconds)}</td><td>${costCell(row)}</td>
    <td>${escapeHtml(displayTime(row.timing?.started_at))}</td><td>${escapeHtml(displayTime(row.timing?.closed_at))}</td>
    <td>${formatTokens(accounting(row).tokens?.total)}</td></tr>`).join('');
  const tasks = state.monitor?.tasks || [];
  const timed = tasks.filter(task => Number.isFinite(task.duration_seconds) && task.duration_seconds >= 0);
  const average = samples => samples.length
    ? `${(samples.reduce((sum, task) => sum + task.duration_seconds, 0) / samples.length).toFixed(1)} 秒` : '未记录';
  const languages = [...new Set(tasks.map(task => task.language))].sort();
  const rows = languages.map(language => {
    const versions = tasks.filter(task => task.language === language);
    const samples = timed.filter(task => task.language === language);
    return `<tr><td>${escapeHtml(language)}</td><td>${versions.length}</td><td>${samples.length}</td><td>${average(samples)}</td>
      <td>${versions.filter(task => task.technical_status === 'pass').length}</td>
      <td>${versions.filter(task => task.agent_status === 'pass').length}</td></tr>`;
  }).join('');
  const demandTable = requirementRows
    ? table('需求时间与花费明细', ['需求', '耗时（含等待）', '花费', '平台开工', '平台结案', 'Codex 会话 Token'],
      requirementRows, 'fold-scroll requirement-table')
    : '<p class="empty">暂无需求时间或费用回执。</p>';
  const languageTable = rows
    ? table('语种与耗时样本', ['语言', '版本', '耗时样本', '平均渲染耗时', '技术通过', '视觉通过'], rows, 'ledger-table fold-scroll')
    : '<p class="empty">暂无可绑定的语种渲染记录。</p>';
  byId('content').innerHTML = `<div class="stack">
    ${fold('requirement-accounting', '需求时间与花费', recordedRows.length, demandTable
      + '<p class="quiet usage-note">时间使用洛杉矶时区。花费按回执原单位列示。— 表示缺少记录。</p>', true)}
    ${fold('language-timing', '语种渲染与审核', languages.length, languageTable)}
    </div>`;
}

function renderSessions() {
  const rows = state.snapshot.requirements.filter(row => row.thread_id);
  byId('overview').innerHTML = cards([
    ['当前负责会话', new Set(rows.filter(row => !row.archived).map(row => row.thread_id)).size, '对应当前跟踪需求'],
    ['已登记需求', rows.length, '含历史关联'],
    ['回执待核对', rows.filter(row => row.operation_pending).length, '本地登记'],
  ]);
  byId('content').innerHTML = rows.length ? table('负责会话', ['会话名称', '需求', '派发状态', '跟踪范围'], rows.map(row =>
    `<tr><td>${escapeHtml(sessionName(row))}</td><td><a href="#ledger/${encodeURIComponent(row.code)}">${escapeHtml(row.code)}</a></td>
      <td>${status('dispatch', row.state)}</td><td>${row.archived ? '历史' : '当前'}</td></tr>`).join('')) : empty('尚无已登记的负责会话。');
}

function renderAutomations() {
  const monitor = state.snapshot.monitor;
  byId('overview').innerHTML = cards([
    ['发现巡检', quantity(monitor.discovery_interval, ' 分钟'), '项目配置周期'],
    ['发现检查点', displayTime(monitor.last_new), '本地回执'],
    ['主控登记', monitor.controller_recorded ? '有记录' : '未记录', '当前项目'],
  ]);
  byId('content').innerHTML = table('巡检与核对', ['任务', '配置', '最近检查点'], [
    ['新需求发现', monitor.discovery_interval ? `每 ${monitor.discovery_interval} 分钟` : '未记录', monitor.last_new],
    ['活动需求核对', monitor.followup_mode === 'same_controller_when_work_exists' ? '有待办时由同一主控跟进' : '未记录', monitor.last_active],
    ['全量核对', '按项目巡检规则', monitor.last_full],
  ].map(([title, policy, last]) =>
    `<tr><td>${title}</td><td>${escapeHtml(policy)}</td><td>${escapeHtml(displayTime(last))}</td></tr>`).join(''));
}

function renderKnowledgeResults() {
  const entries = state.snapshot.knowledge;
  const matches = entries.filter(entry => knowledgeCategory(entry) === knowledgeState.category
    && [entry.title, entry.id, entry.path, entry.scope, ...(Array.isArray(entry.tags) ? entry.tags : [])]
      .join(' ').toLowerCase().includes(knowledgeState.query.toLowerCase().trim()));
  const pageSize = 8;
  const pageCount = Math.max(1, Math.ceil(matches.length / pageSize));
  knowledgeState.page = Math.min(pageCount, knowledgeState.page);
  const results = matches.slice((knowledgeState.page - 1) * pageSize, knowledgeState.page * pageSize);
  byId('knowledge-results').innerHTML = results.map(entry => `<article class="knowledge-item">
    <div><h3>${escapeHtml(entry.title)}</h3><p class="quiet">${escapeHtml(entry.scope)}</p>
      <p class="quiet mono">${escapeHtml(entry.path)}</p></div>
    <button class="table-action" data-knowledge="${escapeHtml(entry.id)}" aria-label="${escapeHtml(`查看正文 ${entry.title}`)}">查看正文 →</button>
    </article>`).join('') || '<p class="empty">没有匹配的索引条目。</p>';
  byId('knowledge-pager').innerHTML = `<span class="quiet" aria-live="polite">第 ${knowledgeState.page} / ${pageCount} 页 · ${matches.length} 项</span>
    <div><button data-knowledge-page="${knowledgeState.page - 1}" ${knowledgeState.page <= 1 ? 'disabled' : ''}>上一页</button>
    <button data-knowledge-page="${knowledgeState.page + 1}" ${knowledgeState.page >= pageCount ? 'disabled' : ''}>下一页</button></div>`;
}

const knowledgeCategory = entry => {
  if (entry.kind === 'method' && entry.status === 'active') return 'method';
  if (entry.kind === 'method' && entry.status === 'provisional') return 'candidate';
  if (entry.kind === 'case') return 'case';
  return 'history';
};

function renderKnowledge() {
  const entries = state.snapshot.knowledge;
  const benchmarks = state.monitor?.benchmarks || [];
  const categories = {method: '现行方法', case: '图片案例', candidate: '暂定方法', history: '其他存档'};
  const options = Object.entries(categories).map(([category, label]) =>
    `<option value="${category}" ${knowledgeState.category === category ? 'selected' : ''}>${label} · ${entries.filter(entry => knowledgeCategory(entry) === category).length}</option>`).join('');
  const benchmarkRows = benchmarks.map(item => `<tr><td>${escapeHtml(item.id)}</td><td>${escapeHtml(item.schema)}</td></tr>`).join('');
  byId('overview').replaceChildren();
  byId('content').innerHTML = `<div class="stack"><section class="panel knowledge-catalog" aria-label="项目知识目录">
    <div class="toolbar"><select id="knowledge-category" aria-label="知识类别">${options}</select>
      <input id="knowledge-search" class="search-label" type="search" aria-label="搜索知识"
        placeholder="搜索标题、标签或路径" autocomplete="off" value="${escapeHtml(knowledgeState.query)}"></div>
    <div id="knowledge-results"></div><div id="knowledge-pager" class="knowledge-pager"></div>
    </section>
    ${fold('benchmarks', '基准与对比记录', benchmarks.length, benchmarkRows
      ? table('基准与对比记录', ['记录', '类型'], benchmarkRows, 'fold-scroll') : '<p class="empty">暂无基准与对比记录。</p>')}
    </div>`;
  renderKnowledgeResults();
}

function renderUsage() {
  if (!state.usage) return '';
  const sessions = state.usage.sessions;
  const rows = sessions.map(session => {
    const {total, input, output, cached_input: cached} = session.tokens;
    const cacheRate = Number.isSafeInteger(cached) && input > 0 ? ` (${(100 * cached / input).toFixed(1)}%)` : '';
    const otherCosts = Object.entries(session.costs)
      .filter(([unit]) => unit !== 'KIE Credits')
      .map(([unit, amount]) => `${amount} ${unit}`).join(' · ');
    return `<tr><td>${escapeHtml(session.title)}<span class="quiet">${session.completed ? '交付已核验' : '交付待核验'}</span></td>
      <td>${formatTokens(total)}</td><td>${formatTokens(input)}</td><td>${formatTokens(output)}</td>
      <td>${formatTokens(cached)}${cacheRate}</td>
      <td>${escapeHtml(session.costs['KIE Credits'] ?? '—')}</td><td>${escapeHtml(otherCosts || '—')}</td></tr>`;
  }).join('');
  const body = rows ? table('图片翻译会话用量', ['会话', 'Codex Token 总计', '输入', '输出', '缓存命中', 'KIE Credits', '其他已登记费用'],
    rows, 'fold-scroll usage-table') : '<p class="empty">暂无已登记的本地负责会话。</p>';
  const note = '<p class="quiet usage-note">Token 为会话累计用量，缓存命中计入输入。费用取自需求阶段回执。— 表示缺少记录。</p>';
  return fold('session-usage', '会话用量与花费', sessions.length, body + note, sessions.length <= 8);
}

function renderSystem() {
  const summary = state.monitor?.summary;
  const totals = (state.usage?.sessions || []).map(session => session.tokens.total)
    .filter(total => Number.isSafeInteger(total) && total >= 0).sort((left, right) => left - right);
  const sampleCount = totals.length;
  const middle = Math.floor(sampleCount / 2);
  const average = sampleCount ? totals.reduce((sum, total) => sum + total, 0) / sampleCount : null;
  const median = sampleCount ? (totals[Math.floor((sampleCount - 1) / 2)] + totals[middle]) / 2 : null;
  const formatStatistic = total => total == null ? '—' : total.toLocaleString('zh-CN', {maximumFractionDigits: 1});
  byId('overview').innerHTML = cards([
    ['本地制作版本', summary?.tasks_total ?? '—', '由制作记录索引'],
    ['已有译图', summary?.production?.with_output ?? '—', '来自当前文件'],
    ['平均 Codex Token 消耗', formatStatistic(average), `中位数 ${formatStatistic(median)} · ${sampleCount} 个会话`],
  ]);
  const breakdown = [['技术检查', summary?.technical], ['Agent 视觉审核', summary?.agent]];
  const rows = breakdown.map(([title, values]) =>
    `<tr><td>${title}</td>${['pass', 'fail', 'pending', 'unknown'].map(key => `<td>${values?.[key] ?? '—'}</td>`).join('')}</tr>`).join('');
  byId('content').innerHTML = `<div class="stack">${renderUsage()}${capacityPanel()}${table('门禁状态', ['项目', '已通过', '未通过', '待检查', '待核对'], rows)}</div>`;
}

const renderers = {
  ledger: renderLedger, tasks: renderTasks, efficiency: renderEfficiency, sessions: renderSessions,
  automations: renderAutomations, knowledge: renderKnowledge, system: renderSystem,
};

function render() {
  if (!state.snapshot) return;
  const content = byId('content');
  const active = document.activeElement;
  const folds = [...content.querySelectorAll('.fold-panel[id]')].map(panel => {
    const body = panel.querySelector('.fold-scroll');
    return {id: panel.id, open: panel.open, top: body?.scrollTop, left: body?.scrollLeft,
      summaryFocused: active === panel.querySelector('summary'), bodyFocused: active === body};
  });
  const focused = document.activeElement?.dataset?.select;
  const region = content.querySelector('.table-wrap:not(.fold-scroll)');
  const scroll = region?.scrollLeft || 0;
  const tableFocused = document.activeElement === region;
  byId('toolbar').hidden = state.page !== 'ledger';
  if (!['ledger', 'tasks'].includes(state.page)) renderDetail(null);
  renderers[state.page]();
  const updatedRegion = content.querySelector('.table-wrap:not(.fold-scroll)');
  if (updatedRegion) {
    updatedRegion.scrollLeft = scroll;
    if (tableFocused) updatedRegion.focus({preventScroll: true});
  }
  if (focused) {
    [...document.querySelectorAll('[data-select]')].find(button => button.dataset.select === focused)?.focus({preventScroll: true});
  }
  if (active?.id === 'knowledge-search' || active?.id === 'knowledge-category') {
    const replacement = byId(active.id);
    replacement?.focus({preventScroll: true});
  }
  for (const previous of folds) {
    const panel = byId(previous.id);
    if (!panel) continue;
    panel.open = previous.open;
    if (previous.summaryFocused) panel.querySelector('summary').focus({preventScroll: true});
    const body = panel.querySelector('.fold-scroll');
    if (!body) continue;
    body.scrollTop = previous.top ?? 0;
    body.scrollLeft = previous.left ?? 0;
    if (previous.bodyFocused) body.focus({preventScroll: true});
  }
}

async function request(url) {
  if (!state.token) {
    const bootstrap = await fetch('/api/bootstrap');
    if (!bootstrap.ok) throw Error('本地服务连接失败');
    state.token = (await bootstrap.json()).token;
  }
  const response = await fetch(url, {headers: {'X-Review-Token': state.token}, cache: 'no-store'});
  if (response.status === 403) state.token = '';
  if (!response.ok) throw Error(`读取失败（${response.status}）`);
  return response.json();
}

function showNotice(message) {
  byId('notice').textContent = message;
  byId('notice').hidden = !message;
}

function showRecordNotes(warnings) {
  const panel = byId('record-notes');
  panel.hidden = !warnings.length;
  if (panel.hidden) panel.open = false;
  byId('record-note-count').textContent = `${warnings.length} 条`;
  const list = byId('record-note-list');
  const items = warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('');
  if (list.innerHTML !== items) list.innerHTML = items;
}

async function refresh() {
  if (state.loading) {
    state.refreshQueued = true;
    return;
  }
  state.loading = true;
  const page = state.page;
  try {
    const [snapshot, secondary, usage] = await Promise.all([
      request('/api/workbench'),
      page === 'tasks' ? request('/api/tasks')
        : ['efficiency', 'knowledge', 'system'].includes(page) ? request('/api/monitor') : null,
      ['ledger', 'efficiency', 'system'].includes(page) ? request('/api/workbench/usage') : null,
    ]);
    if (page !== state.page) return;
    const firstSnapshot = !state.snapshot;
    state.snapshot = snapshot;
    if (firstSnapshot) exposeArchivedSelection();
    if (page === 'tasks') state.tasks = secondary;
    if (['efficiency', 'knowledge', 'system'].includes(page)) state.monitor = secondary;
    if (usage) state.usage = usage;
    const {read_at: readAt, ...workbench} = snapshot;
    const {generated_at: _generatedAt, ...monitor} = secondary || {};
    const signature = JSON.stringify([page, workbench, monitor, usage]);
    if (signature !== state.signature) {
      render();
      state.signature = signature;
    }
    byId('read-time').textContent = `读取于 ${displayTime(readAt)}`;
    byId('connection').textContent = byId('auto').checked ? '已连接' : '已暂停自动更新';
    const warnings = [...new Set([...snapshot.warnings, ...(secondary?.warnings || [])])];
    showNotice('');
    showRecordNotes(warnings);
    if (state.tasks) {
      byId('nav-count').textContent = state.tasks.tasks.length;
      byId('nav-count').hidden = false;
    }
  } catch (error) {
    byId('connection').textContent = '读取失败';
    showNotice(error.message);
  } finally {
    state.loading = false;
    if (state.refreshQueued) {
      state.refreshQueued = false;
      refresh();
    }
  }
}

function route() {
  const [page, rawSelection] = location.hash.slice(1).split('/');
  if (page === 'main') return;
  const nextPage = Object.hasOwn(pages, page) ? page : 'ledger';
  if (state.page !== nextPage) showRecordNotes([]);
  state.page = nextPage;
  try {
    state.selected = rawSelection ? decodeURIComponent(rawSelection) : '';
  } catch {
    state.selected = '';
  }
  const [title, description] = pages[state.page];
  byId('page-title').textContent = title;
  byId('page-label').textContent = title;
  byId('page-description').textContent = description;
  document.querySelectorAll('[data-nav]').forEach(link => {
    if (link.dataset.nav === state.page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  exposeArchivedSelection();
  state.signature = '';
  render();
  refresh();
}

byId('refresh').addEventListener('click', refresh);
byId('auto').addEventListener('change', () => {
  if (byId('auto').checked) refresh();
  else byId('connection').textContent = '已暂停自动更新';
});
byId('search').addEventListener('input', render);
byId('scope').addEventListener('change', render);
byId('progress').addEventListener('change', render);
byId('content').addEventListener('input', event => {
  if (event.target.id !== 'knowledge-search') return;
  knowledgeState.query = event.target.value;
  knowledgeState.page = 1;
  renderKnowledgeResults();
});
byId('content').addEventListener('change', event => {
  if (event.target.id !== 'knowledge-category') return;
  knowledgeState.category = event.target.value;
  knowledgeState.page = 1;
  renderKnowledgeResults();
});
window.addEventListener('hashchange', route);
document.addEventListener('click', async event => {
  const knowledgePage = event.target.closest('[data-knowledge-page]');
  if (knowledgePage) {
    knowledgeState.page = Number(knowledgePage.dataset.knowledgePage);
    renderKnowledgeResults();
    byId('knowledge-pager').querySelector('button:not([disabled])')?.focus({preventScroll: true});
    return;
  }
  const selector = event.target.closest('[data-select]');
  if (selector) {
    state.selected = selector.dataset.select;
    render();
    byId('detail').querySelector('h2')?.focus({preventScroll: true});
    byId('detail').scrollIntoView({block: 'start'});
    return;
  }
  if (event.target.closest('[data-close-detail]')) {
    const selected = state.selected;
    state.selected = '';
    if (location.hash.includes('/')) history.replaceState(null, '', `#${state.page}`);
    render();
    [...document.querySelectorAll('[data-select]')].find(button => button.dataset.select === selected)?.focus();
    return;
  }
  const entry = event.target.closest('[data-knowledge]');
  if (!entry) return;
  try {
    const documentEntry = await request(`/api/workbench/knowledge?id=${encodeURIComponent(entry.dataset.knowledge)}`);
    byId('document-title').textContent = documentEntry.title || documentEntry.id;
    byId('document-text').textContent = documentEntry.content;
    if (!byId('document').open) byId('document').showModal();
  } catch (error) {
    showNotice(error.message);
  }
});
setInterval(() => {
  if (byId('auto').checked && !state.loading && !document.hidden) refresh();
}, 8000);
route();
