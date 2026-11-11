/* Hallmark · macrostructure: Workflow desk · tone: utilitarian · theme: host tokens
 * pre-emit critique: P5 H5 E4 S5 R5 V4 */
window.__ModuleLoader__.load({
  id: '@local/image-review-ui',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const BASE = '/local-image-review';
    const PANEL = 'local-image-review-ui';
    const PROJECT = 'C:/Users/Echo/Desktop/单图片翻译';
    const normalizePath = (value) => typeof value === 'string' ? value.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase() : '';
    const canTarget = (row) => !!row && row.origin !== 'subagent' && normalizePath(row.cwd) === normalizePath(PROJECT);
    const statusNames = { planned: '计划中', running: '执行中', artifact_ready: '有结果', review_pending: '待确认', completed: '已完成', failed: '失败', unknown: '证据未知', skipped: '已跳过' };
    const statusName = (value) => ({ idle: '空闲', queued: '排队中' })[value] || statusNames[value] || value || '证据未知';
    const text = (value) => value == null ? '—' : ['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '';
    const shortId = (value) => String(value || '').replace(/^session-/, '').slice(0, 8);
    const artifactName = (item) => {
      const name = item.name || item.path?.split(/[/\\]/).pop() || item.id || '未命名产物';
      if (!/^[a-f0-9]{64}\.(png|jpe?g|webp|gif)$/i.test(name)) return name;
      const source = item.source_path?.split(/[/\\]/).pop();
      return source && !/^[a-f0-9]{64}\./i.test(source) ? source : '历史图片';
    };
    function galleryArtifacts(artifacts = []) {
      const groups = new Map(), history = [];
      const rank = (item) => (item.capture === 'current' ? 4 : 0) + (/^[a-f0-9]{64}\./i.test(item.name || '') ? 0 : 1);
      for (const item of artifacts) {
        const key = item.sha256 ? `${item.kind}:${item.sha256}` : item;
        const previous = groups.get(key);
        if (!previous) groups.set(key, item);
        else if (rank(item) > rank(previous)) { history.push(previous); groups.set(key, item); }
        else history.push(item);
      }
      return { primary: [...groups.values()], history };
    }
    const nodeType = (node) => node.type || node.node_type || node.id;
    const auxiliary = (node) => node.status === 'skipped' || node.auxiliary === true || node.stage === 'aux' || /^(capability\.|task\.initialize|job\.|analysis\.packet|review\.packet|semantic\.(validate|resolve)|style\.plan|route\.select|font\.|quality\.|evidence\.|revision\.)/.test(nodeType(node) || '');
    function assetUrl(value) {
      if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '';
      if (value.startsWith('/api/assets?')) return `${BASE}/image?${value.split('?')[1]}`;
      return value.startsWith(`${BASE}/`) ? value : '';
    }
    function dateLabel(value) {
      if (!value) return '暂无时间记录';
      const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
    }
    function catalogRows(catalog) {
      if (Array.isArray(catalog)) return catalog;
      if (Array.isArray(catalog?.nodes)) return catalog.nodes;
      return catalog && typeof catalog === 'object' ? Object.entries(catalog).flatMap(([key, value]) => Array.isArray(value) ? value.map((node) => ({ ...node, routes: node.routes || [key] })) : value && typeof value === 'object' ? [{ ...value, id: value.id || key }] : []) : [];
    }
    function sessionTitle(row, byId) {
      const metadata = byId?.[row?.session_id];
      for (const value of [metadata?.displayTitle, metadata?.title, row?.title]) {
        if (typeof value === 'string' && value.trim() && value !== row?.session_id && !/^(session-)?[a-f0-9-]{32,36}$/i.test(value.trim())) return value.trim();
      }
      return `会话 ${shortId(row?.session_id)}`;
    }
    function mergeUnassignedRun(metadata, detail) {
      if (!metadata || !detail || !metadata.id || metadata.id !== detail.id || metadata.root !== detail.root || !Array.isArray(detail.nodes)) return metadata;
      return { ...detail, ...metadata, nodes: Array.isArray(metadata.nodes) && metadata.nodes.length ? metadata.nodes : detail.nodes };
    }
    function mergeWorkflowSnapshot(next, previous) {
      return { ...next, sessions: (next.sessions || []).map(metadata => {
        const detail = previous?.sessions?.find(row => row.session_id === metadata.session_id);
        if (metadata.detail_loaded !== false || detail?.detail_loaded !== true) return metadata;
        return { ...metadata, detail_loaded: true, runs: (metadata.runs || []).map(run => detail.runs?.find(old => old.id === run.id && old.root === run.root) || run) };
      }), unassigned: (next.unassigned || []).map((metadata) => mergeUnassignedRun(metadata, previous?.unassigned?.find((detail) => detail.id === metadata.id && detail.root === metadata.root))) };
    }
    function mergeSessionDetail(previous, payload, expectedId) {
      if (!previous || payload?.session?.session_id !== expectedId) return previous;
      if (!previous.sessions?.some(row => row.session_id === expectedId)) return previous;
      return { ...previous, node_catalog: payload.node_catalog || previous.node_catalog, sessions: previous.sessions.map(row => row.session_id === expectedId ? { ...payload.session, ...row, runs: payload.session.runs || [], detail_loaded: true, detail_warnings: payload.warnings || [] } : row) };
    }
    function routeLabel(route) { return ({ basic: '标准制作', layered: '分层制作', native: '分阶段制作' })[route] || route || '路线未标注'; }
    function workflowNodes(run, catalog) {
      if (!run) return [];
      const definitions = catalogRows(catalog);
      return (Array.isArray(run.nodes) ? run.nodes : []).map((node) => {
        const entry = definitions.find((item) => nodeType(item) === nodeType(node) || item.id === node.id);
        return { ...entry, ...node, artifacts: Array.isArray(node.artifacts) ? node.artifacts : [] };
      });
    }
    // Only known human-readable fields become content; unknown objects stay in debug.
    const words = (value) => typeof value === 'string' && value.trim() ? value.trim() : '';
    function semanticContent(value) {
      const paragraphs = [], regions = [], checks = [];
      const add = (entry) => { const line = words(entry); if (line && !paragraphs.includes(line)) paragraphs.push(line); };
      const row = (entry) => { if (!entry || typeof entry !== 'object') return; const source = words(entry.source_text), target = words(entry.final_text) || words(entry.final_target_text) || words(entry.target_text); if (source || target) regions.push({ ...entry, id: entry.id || entry.region_id, source_text: source, target_text: target }); };
      if (typeof value === 'string') add(value);
      else if (Array.isArray(value)) value.forEach((entry) => typeof entry === 'string' ? add(entry) : row(entry));
      else if (value && typeof value === 'object') {
        for (const key of ['text', 'summary', 'conclusion', 'message']) add(value[key]);
        for (const list of [value.paragraphs, value.observations, value.analysis?.observations]) if (Array.isArray(list)) list.forEach(add);
        for (const list of [value.regions, value.translations]) if (Array.isArray(list)) list.forEach(row);
        if (Array.isArray(value.rows)) value.rows.forEach((entry) => { if (words(entry?.source_text) || words(entry?.target_text)) row(entry); else add(entry?.text); });
        if (value.stage_notes && typeof value.stage_notes === 'object') Object.values(value.stage_notes).forEach(add);
        if (Array.isArray(value.issues)) value.issues.forEach((entry) => { const note = words(entry?.note) || words(entry?.message); if (note) checks.push({ text: note, severity: words(entry.severity) }); });
        if (Array.isArray(value.style_comparisons)) value.style_comparisons.forEach((entry) => { const observation = words(entry?.target_observation); if (observation) checks.push({ text: observation, verdict: words(entry.verdict) }); });
        if (Array.isArray(value.errors)) value.errors.forEach((entry) => { const note = words(entry) || words(entry?.message); if (note) checks.push({ text: note, severity: 'error' }); });
      }
      return { paragraphs, regions, checks };
    }
    function artifactContent(artifact) {
      let value = artifact.readable ?? artifact.preview ?? artifact.data ?? (['text', 'markdown'].includes(artifact.kind) ? artifact.text : null);
      if (typeof value === 'string' && (artifact.kind === 'json' || /\.json$/i.test(artifact.name || artifact.path || '')) && /^\s*[\[{]/.test(value)) { try { value = JSON.parse(value); } catch { return semanticContent(null); } }
      return semanticContent(value);
    }
    function nodeContent(node) {
      const paragraphs = [], regions = [], checks = [], seenRegions = new Set(), seenChecks = new Set();
      const merge = (content) => {
        for (const line of content.paragraphs) if (!paragraphs.includes(line)) paragraphs.push(line);
        for (const region of content.regions) { const key = JSON.stringify([region.language || region.target_language, region.id, region.source_text, region.target_text]); if (!seenRegions.has(key)) { seenRegions.add(key); regions.push(region); } }
        for (const check of content.checks) { const key = check.severity + ':' + check.text; if (!seenChecks.has(key)) { seenChecks.add(key); checks.push(check); } }
      };
      const primary = semanticContent(node.content);
      if (usefulContent(primary)) merge(primary);
      else { merge(semanticContent({ regions: node.regions })); for (const artifact of nodeGallery(node).primary) merge(artifactContent(artifact)); }
      for (const highlight of (Array.isArray(node.highlights) ? node.highlights : [])) { const line = words(highlight?.text); if (line && !paragraphs.includes(line)) paragraphs.push(line); }
      for (const output of (Array.isArray(node.outputs) ? node.outputs : [])) if (output?.kind === 'text') { const line = words(output.text); if (line && !paragraphs.includes(line)) paragraphs.push(line); }
      if (node.status === 'failed') {
        const reason = words(node.reason).split('\n')[0].slice(0, 300), code = Number.isInteger(node.exit_code) ? `退出码 ${node.exit_code}` : '';
        const failure = [reason, code].filter(Boolean).join(' · ');
        if (failure && !paragraphs.includes(failure)) paragraphs.unshift(failure);
      }
      if (/^(quality\.|semantic\.validate)/.test(nodeType(node)) && ['pass', 'fail'].includes(node.verdict) && !paragraphs.length && !checks.length) paragraphs.push(node.verdict === 'pass' ? '技术检查通过' : '技术检查未通过');
      return { paragraphs, regions, checks };
    }
    const usefulContent = (content) => !!(content.paragraphs.length || content.regions.length || content.checks.length);
    const primaryComparisonName = (item) => item.kind === 'image' && /(?:^|[/\\])(?:comparison|compare|board)(?:[-_.]|$)/i.test(item.name || item.path || '');
    const comparisonDetailArtifact = (item) => item.kind === 'image' && [item.source_path, item.path].some((path) => typeof path === 'string' && path.split(String.fromCharCode(92)).join('/').toLowerCase().split('/').some((part) => part === 'regions' || part === 'differences'));
    const comparisonArtifact = (item) => item.kind === 'image' && !comparisonDetailArtifact(item) && [item.name, item.source_path, item.path].some((name) => typeof name === 'string' && (primaryComparisonName({ ...item, name }) || /[-_.]comparison\.(?:png|jpe?g|webp|gif)$/i.test(name)));
    function comparisonSections(node, images) {
      const comparisonNode = node.stage === 'comparison' || nodeType(node) === 'evidence.compare';
      const comparisons = images.filter(comparisonArtifact), remaining = images.filter((item) => !comparisonArtifact(item));
      return { comparisons, ordinary: comparisonNode ? [] : remaining, details: comparisonNode ? remaining : [] };
    }
    function comparisonPairs(node, nodes) {
      const artifacts = nodes.flatMap((entry) => entry.artifacts || []);
      return (Array.isArray(node.comparison_inputs) ? node.comparison_inputs : []).flatMap((pair) => {
        const source = artifacts.find((item) => item.id === pair.source_artifact_id && item.kind === 'image' && assetUrl(item.url));
        const result = artifacts.find((item) => item.id === pair.result_artifact_id && item.kind === 'image' && assetUrl(item.url));
        return source && result ? [{ source, result, language: words(pair.language) }] : [];
      });
    }
    function nodeGallery(node) {
      const artifacts = Array.isArray(node.artifacts) ? node.artifacts : [];
      if (nodeType(node) !== 'result' || !Array.isArray(node.current_artifact_ids)) return galleryArtifacts(artifacts);
      const current = new Set(node.current_artifact_ids);
      const visible = artifacts.filter((item) => current.has(item.id));
      const { primary, history } = galleryArtifacts(visible);
      return { primary, history: [...history, ...artifacts.filter((item) => !current.has(item.id))] };
    }
    function usefulNode(node, nodes = []) { return node.status === 'running' || node.status === 'failed' || nodeGallery(node).primary.some((item) => item.kind === 'image' && assetUrl(item.url)) || usefulContent(nodeContent(node)) || comparisonPairs(node, nodes).length > 0; }
    function visibleNodes(nodes) { return nodes.filter((node) => usefulNode(node, nodes)); }
    function selectedNode(nodes, id) { return nodes.find((row) => row.id === id) || nodes.find((row) => row.status === 'running') || nodes.find((row) => nodeType(row) === 'result') || nodes.find((row) => row.stage === 'comparison' || nodeType(row) === 'evidence.compare') || nodes.find((row) => row.status === 'review_pending') || nodes[0]; }
    const STAGE_DEFS = [
      ['input', '输入', /^(task\.initialize|job\.(initialize|freeze)|source\.|analysis\.packet|capability\.|style\.|route\.)/],
      ['recognition', '识别', /^semantic\.(analyze|resolve)/], ['translation', '翻译', /^semantic\.translate/],
      ['background', '背景', /^background\./], ['lettering', '文字层', /^(lettering\.|assets\.|font\.)/],
      ['composition', '合成', /^(basic\.render|composite\.|job\.render)/],
      ['checks', '检查', /^(quality\.|semantic\.validate|review\.)/], ['delivery', '交付', /^(result$|evidence\.|feedback\.|revision\.)/],
    ];
    function groupStatus(members) {
      for (const status of ['running', 'failed', 'review_pending']) if (members.some(item => item.status === status)) return status;
      if (members.length && members.every(item => item.status === 'completed' || item.status === 'skipped')) return 'completed';
      return members.some(item => usefulNode(item, members)) ? 'artifact_ready' : 'planned';
    }
    function historicalArtifact(artifact) {
      return (typeof artifact.capture === 'string' && artifact.capture !== 'current') || [artifact.path, artifact.url].some(value => typeof value === 'string' && value.split(String.fromCharCode(92)).join('/').includes('/workflows/content/'));
    }
    function stageGallery(members) {
      const candidates = [], historical = [], sources = new Map();
      for (const member of members) {
        const gallery = nodeGallery(member);
        for (const artifact of [...gallery.primary, ...gallery.history]) {
          if (artifact.kind !== 'image' || !assetUrl(artifact.url)) continue;
          const key = artifact.sha256 || artifact.id;
          if (!sources.has(key)) sources.set(key, []);
          sources.get(key).push({ node_id: member.id, title: member.title || member.id, artifact_id: artifact.id, sha256: artifact.sha256 });
        }
        candidates.push(...gallery.primary.filter(item => item.kind === 'image' && assetUrl(item.url)));
        historical.push(...gallery.history);
      }
      const live = candidates.filter(item => !historicalArtifact(item));
      let primary;
      if (live.length) primary = galleryArtifacts(live).primary;
      else primary = [...candidates].sort((a, b) => (Date.parse(b.captured_at || '') || 0) - (Date.parse(a.captured_at || '') || 0)).slice(0, 1);
      const keys = new Set(primary.map(item => item.sha256 || item.id));
      const history = galleryArtifacts([...candidates, ...historical]).primary.filter(item => item.kind === 'image' && assetUrl(item.url) && !keys.has(item.sha256 || item.id));
      return { primary, history, sources };
    }
    function businessStages(nodes) {
      return STAGE_DEFS.flatMap(([id, title, pattern]) => {
        const members = nodes.filter(item => pattern.test(nodeType(item)) && usefulNode(item, nodes));
        if (!members.length) return [];
        let { primary: images, history, sources } = stageGallery(members);
        if (id === 'input' && images.length > 1) {
          const normalized = images.filter(item => (item.source_path || item.path || item.name || '').split(/[/\\\\]/).at(-1) === 'source.png');
          if (normalized.length === 1) { history = [...history, ...images.filter(item => item !== normalized[0])]; images = normalized; }
        }
        const content = { paragraphs: [], regions: [], checks: [] }, regionKeys = new Set(), checkKeys = new Set();
        for (const member of members) {
          const next = nodeContent(member);
          next.paragraphs.forEach(line => { if (!content.paragraphs.includes(line)) content.paragraphs.push(line); });
          next.regions.forEach(row => { const key = JSON.stringify([row.id, row.language, row.source_text, row.target_text]); if (!regionKeys.has(key)) { regionKeys.add(key); content.regions.push(row); } });
          next.checks.forEach(row => { const key = row.text + ':' + row.verdict + ':' + row.severity; if (!checkKeys.has(key)) { checkKeys.add(key); content.checks.push(row); } });
        }
        return [{ id, title, members, artifacts: images, historyArtifacts: history, sources, content, status: groupStatus(members) }];
      });
    }
    function selectedStage(stages, id) {
      return stages.find(item => item.id === id || item.members.some(member => member.id === id)) || (!id ? stages.find(item => item.status === 'running') || stages.find(item => item.status === 'failed') || stages.at(-1) : undefined);
    }
    const statusOnlyText = value => /^(?:会话)?(?:已)?(?:空闲|执行中|运行中|排队中|停止|已停止|已完成|进行中|idle|running|queued|completed|stopped)$/i.test(words(value));
    function sessionActivity(row) {
      const activity = row?.active_stage || (Array.isArray(row?.activities) ? row.activities.at(-1) : null) || row?.activity;
      if (typeof activity === 'string') return statusOnlyText(activity) ? '' : activity.trim();
      const latest = Array.isArray(row?.activities) ? [...row.activities].reverse().find(item => !['plan', 'status'].includes(item?.type) && words(item?.text) && !statusOnlyText(item.text)) : null;
      return words(latest?.text) || (activity?.type !== 'status' && !statusOnlyText(activity?.text) ? words(activity?.text) || words(activity?.summary) || words(activity?.message) : '');
    }
    const canStopSession = row => row?.status === 'running' && row?.can_control?.cancel === true;
    function sessionProgress(row) {
      const running = (row.runs || []).flatMap(run => businessStages(workflowNodes(run))).find(stage => stage.status === 'running');
      const steps = (row.runs || []).flatMap(run => businessStages(workflowNodes(run)));
      return { label: running?.title || words(row.active_stage?.stage) || (!statusOnlyText(row.active_stage?.text) ? words(row.active_stage?.text).slice(0, 42) : '') || (steps.length ? steps.at(-1).title : '等待步骤事件'), completed: steps.filter(stage => stage.status === 'completed').length, total: steps.length, text: sessionActivity(row) };
    }
    function explicitPlan(row) {
      const plan = Array.isArray(row?.plan) ? row.plan : Array.isArray(row?.plan?.items) ? row.plan.items : [];
      return plan.filter(item => item && words(item.title || item.content)).map(item => ({ id: item.id, title: words(item.title || item.content), status: ['pending', 'in_progress', 'completed'].includes(item.status) ? item.status : 'pending' }));
    }
    function controlIntentFor(previous, input, requestId) {
      const key = JSON.stringify(input);
      if (previous?.key === key) return previous;
      return { key, input: { ...input, request_id: requestId }, prepared: null };
    }
    function validControlIntent(prepared, input) {
      if (prepared?.prepared !== true || prepared.session_id !== input.session_id || prepared.request_id !== input.request_id || prepared.action !== input.action || prepared.request?.sessionId !== input.session_id) return false;
      for (const field of ['run_id', 'node_id', 'plan_event_id', 'plan_index']) if ((prepared.scope?.[field] ?? undefined) !== input[field]) return false;
      return input.action === 'cancel' || (prepared.request.requestId === input.request_id && prepared.request.mode === (input.mode || 'queue') && Array.isArray(prepared.request.content));
    }
    async function dispatchControlIntent(intent, prepare, remote) {
      const prepared = intent.prepared || await prepare(intent.input);
      if (!validControlIntent(prepared, intent.input)) throw new Error('控制目标不匹配，请刷新后重试。');
      intent.prepared = prepared;
      const reply = await (intent.input.action === 'cancel' ? remote.cancel(prepared.request) : remote.prompt(prepared.request));
      if (reply?.ok !== true || reply.value?.accepted !== true) throw new Error('会话未确认接收；可使用同一指令重试。');
      return reply;
    }
    function normalizedBox(box, width, height) {
      if (!Array.isArray(box) || box.length !== 4 || box.some((n) => !Number.isFinite(n)) || !width || !height || box[2] <= 0 || box[3] <= 0) return null;
      const left = Math.max(0, Math.min(1, box[0] / width)), top = Math.max(0, Math.min(1, box[1] / height));
      const right = Math.max(left, Math.min(1, (box[0] + box[2]) / width)), bottom = Math.max(top, Math.min(1, (box[1] + box[3]) / height));
      return right > left && bottom > top ? [left, top, right - left, bottom - top] : null;
    }
    function regionBox(region, width, height) {
      if (region?.coordinate_space === 'source_normalized') {
        const box = region.bbox;
        return Array.isArray(box) && box.length === 4 && box.every(Number.isFinite) && box[0] >= 0 && box[1] >= 0 && box[2] > 0 && box[3] > 0 && box[0] + box[2] <= 1.000001 && box[1] + box[3] <= 1.000001 ? box : null;
      }
      return normalizedBox(region?.bbox_pixels || region?.bbox, width, height);
    }
    function selectionBox(origin, current, dimensions) {
      if (![origin, current].every((point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite))) return null;
      const start = origin.map((value) => Math.max(0, Math.min(1, value))), end = current.map((value) => Math.max(0, Math.min(1, value)));
      const isPoint = Math.hypot(end[0] - start[0], end[1] - start[1]) < .008;
      const minimum = [0, 1].map((index) => 1 / Math.max(1, Number(dimensions?.[index]) || 1000));
      const width = isPoint ? minimum[0] : Math.max(minimum[0], Math.abs(end[0] - start[0]));
      const height = isPoint ? minimum[1] : Math.max(minimum[1], Math.abs(end[1] - start[1]));
      return { bbox: [Math.min(1 - width, Math.min(end[0], start[0])), Math.min(1 - height, Math.min(end[1], start[1])), width, height], isPoint };
    }
    function resultCandidates(run) {
      const declared = Array.isArray(run?.results) ? run.results : run?.result?.id ? [run.result] : [];
      return [...new Map(declared.filter((item) => item.kind === 'image' && item.id).map((item) => [item.id, item])).values()];
    }
    function selectedResult(run, id) {
      const candidates = resultCandidates(run);
      if (id) return candidates.find((item) => item.id === id) || null;
      const declared = run?.result;
      if (declared?.id) return candidates.find((item) => item.id === declared.id && item.sha256 === declared.sha256) || null;
      return candidates.length === 1 ? candidates[0] : null;
    }
    function commentPayload(draft) {
      return { session_id: draft.session_id, run_id: draft.run.id, node_id: draft.node.id, note: draft.note.trim(), request_id: draft.request_id,
        ...(draft.artifact?.id ? { artifact_id: draft.artifact.id } : {}), ...(draft.artifact?.sha256 ? { artifact_sha256: draft.artifact.sha256 } : {}),
        ...(draft.region_id ? { region_id: draft.region_id } : {}), ...(draft.bbox ? { bbox: [...draft.bbox] } : {}) };
    }
    function validComment(result, expected) {
      const record = result?.comment;
      return !!record && typeof record.id === 'string' && !!record.id && result.session_id === expected.session_id &&
        ['session_id', 'run_id', 'node_id', 'request_id', 'note'].every((key) => record[key] === expected[key]) &&
        ['artifact_id', 'artifact_sha256', 'region_id'].every((key) => (record[key] ?? null) === (expected[key] ?? null)) &&
        JSON.stringify(record.bbox ?? null) === JSON.stringify(expected.bbox ?? null);
    }
    async function api(path, options = {}) {
      const controller = new AbortController();
      const cancel = () => controller.abort();
      options.signal?.addEventListener('abort', cancel, { once: true });
      if (options.signal?.aborted) controller.abort();
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
      try {
        const response = await fetch(`${BASE}/${path}`, { credentials: 'same-origin', cache: 'no-store', ...options, signal: controller.signal, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
        let data;
        try { data = await response.json(); } catch (error) { if (error.name === 'AbortError') throw error; throw new Error(`响应无法读取（${response.status}）`); }
        if (!response.ok) { const failure = new Error(typeof data.error === 'string' ? data.error : data.error?.message || `请求失败（${response.status}）`); failure.status = response.status; throw failure; }
        return data;
      } catch (failure) { if (timedOut) throw new Error('服务响应超时；草稿和请求编号已保留，请核对后重试。'); throw failure; }
      finally { clearTimeout(timer); options.signal?.removeEventListener('abort', cancel); }
    }
    const DRAFT_KEY = `${PANEL}:workflow-draft`;
    function restoreDraft() {
      try {
        const value = JSON.parse(window.sessionStorage.getItem(DRAFT_KEY) || 'null');
        return value && typeof value.note === 'string' && value.note.length <= 10000 && typeof value.request_id === 'string' && value.node?.id && value.run?.id ? value : null;
      } catch { return null; }
    }
    const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
    const css = `
.wfd{--bg:var(--dsw-alias-bg-base);--layer:var(--dsw-alias-bg-layer-1);--raised:var(--dsw-alias-bg-layer-2);--ink:var(--dsw-alias-label-primary);--muted:var(--dsw-alias-label-secondary);--line:var(--dsw-alias-border-l1);--accent:var(--dsw-alias-brand-primary);--good:var(--dsw-alias-state-success-primary);--bad:var(--dsw-alias-state-error-primary);--overlay:var(--dsw-alias-bg-overlay);height:100%;min-height:0;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:inherit;font-size:13px;line-height:1.5;position:relative;isolation:isolate;overflow-x:clip}
.wfd *{box-sizing:border-box}.wfd button,.wfd input,.wfd select,.wfd textarea{font:inherit;color:inherit;min-width:0;background:var(--layer);border:1px solid var(--line);border-radius:6px;padding:7px 10px}.wfd button{cursor:pointer;white-space:nowrap}.wfd button:hover:not(:disabled){background:var(--raised);border-color:var(--accent)}.wfd button:active:not(:disabled){transform:translateY(1px)}.wfd button:disabled{opacity:.45;cursor:default}.wfd :focus-visible{outline:2px solid var(--accent);outline-offset:3px}.wfd .primary{background:var(--accent);color:var(--bg);border-color:var(--accent)}.wfd a{color:var(--accent)}.wfd small,.wfd .muted{color:var(--muted)}.wfd small{font-size:11px}.wfd h2,.wfd h3,.wfd p{margin:0}.wfd h2{font-size:19px;font-weight:650;letter-spacing:-.4px;overflow-wrap:anywhere}.wfd h3{font-size:14px;font-weight:600}.wfd .grow{flex:1;min-width:0}.wfd .top{display:flex;gap:16px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap;min-height:66px}.wfd .top-title{display:flex;align-items:center;gap:10px}.wfd .context{font-size:12px;min-width:0;max-width:45%;overflow-wrap:anywhere}.wfd .connection{font-size:11px;display:flex;align-items:center;gap:6px;white-space:nowrap;color:var(--muted)}.wfd .dot{width:6px;height:6px;background:var(--good);border-radius:50%;display:inline-block;flex-shrink:0}.wfd .dot.off{background:var(--bad)}.wfd .layout{display:grid;grid-template-columns:248px minmax(0,1fr);flex:1;min-height:0}.wfd .sidebar{overflow:auto;border-right:1px solid var(--line);padding:16px 12px}.wfd .sidebar input{width:100%;margin-bottom:16px}.wfd .sidebar-label{padding:0 8px 8px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}.wfd .session{margin-bottom:8px;border-radius:8px;border:1px solid transparent}.wfd .session.active{background:var(--layer);border-color:var(--line)}.wfd .session-head{background:transparent;border:0;width:100%;display:flex;text-align:left;align-items:flex-start;gap:8px;padding:10px 8px}.wfd .session-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;max-width:185px;font-weight:600}.wfd .session-meta{display:flex;gap:6px;align-items:center;margin-top:4px;font-size:10px;color:var(--muted)}.wfd .runs{border-left:1px solid var(--line);margin:0 8px 10px 17px;padding-left:8px}.wfd .run{display:block;text-align:left;width:100%;border:0;background:transparent;padding:7px 8px;overflow:hidden;text-overflow:ellipsis;font-size:12px}.wfd .run[aria-pressed=true]{background:var(--raised);color:var(--accent)}.wfd .history{margin-top:20px;border-top:1px solid var(--line);padding-top:12px}.wfd summary{cursor:pointer;color:var(--muted)}.wfd .main{overflow:auto;min-width:0;padding:20px 24px 32px}.wfd .run-head{display:flex;align-items:flex-start;gap:12px;margin-bottom:20px}.wfd .run-subtitle{font-size:12px;color:var(--muted);margin-top:5px}.wfd .badge{display:inline-flex;align-items:center;gap:5px;font-size:10px;line-height:1.6;border:1px solid var(--line);border-radius:4px;padding:1px 5px;white-space:nowrap;color:var(--muted)}.wfd .badge[data-status=running],.wfd .badge[data-status=review_pending],.wfd .badge[data-status=artifact_ready]{color:var(--accent);border-color:var(--accent)}.wfd .badge[data-status=failed]{color:var(--bad);border-color:var(--bad)}.wfd .badge[data-status=completed]{color:var(--good)}.wfd .flow{border:1px solid var(--line);border-radius:10px;padding:20px;background:var(--layer);margin-bottom:16px}.wfd .flow-head{display:flex;align-items:center;gap:12px;margin-bottom:18px}.wfd .flow-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(156px,1fr));gap:20px 24px;align-items:stretch}.wfd .flow-cell{min-width:0;position:relative}.wfd .flow-cell:not(:last-child):after{content:'→';position:absolute;right:-19px;top:32px;color:var(--muted);font-size:17px}.wfd .node{width:100%;height:100%;min-height:116px;display:flex;flex-direction:column;align-items:stretch;gap:8px;text-align:left;background:var(--bg);border-radius:7px;padding:12px;white-space:normal}.wfd .node[aria-pressed=true]{border:2px solid var(--accent);padding:11px}.wfd .node[data-status=running]{box-shadow:inset 3px 0 var(--accent)}.wfd .node[data-status=failed]{box-shadow:inset 3px 0 var(--bad)}.wfd .node-title{font-weight:600;overflow-wrap:anywhere;line-height:1.4}.wfd .node-meta{display:flex;align-items:center;gap:7px;margin-top:auto;font-size:11px;color:var(--muted)}.wfd .node-meta img{width:32px;height:28px;object-fit:contain;background:var(--raised);border-radius:3px}.wfd .branch{font-size:10px;color:var(--muted);padding:0 0 4px;overflow-wrap:anywhere}.wfd .support{border-top:1px solid var(--line);padding-top:12px;margin-top:18px}.wfd .support-items{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.wfd .support-items button{font-size:11px;display:flex;gap:7px;align-items:center}.wfd .detail{border:1px solid var(--line);border-radius:10px;overflow:hidden}.wfd .detail-head{display:flex;align-items:center;gap:12px;padding:16px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap}.wfd .detail-body{padding:18px;min-width:0}.wfd .reason{font-size:12px;margin-bottom:16px;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}.wfd .gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr));gap:14px;align-items:start}.wfd figure{margin:0;min-width:0;border:1px solid var(--line);border-radius:7px;overflow:hidden;background:var(--layer)}.wfd figure .art-open{display:block;padding:0;width:100%;border:0;border-radius:0;background:var(--raised)}.wfd figure img{display:block;width:100%;height:210px;object-fit:contain}.wfd figcaption{padding:10px 12px;font-size:12px;overflow-wrap:anywhere}.wfd .artifact-actions{display:flex;align-items:center;gap:8px;margin-top:8px}.wfd .artifact-actions button{font-size:11px;padding:4px 7px}.wfd .artifact-preview{padding:14px;max-height:290px;overflow:auto}.wfd pre{font:inherit;font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0;padding:12px;background:var(--raised);border-radius:5px;max-height:280px;overflow:auto}.wfd table{width:100%;border-collapse:collapse;font-size:12px}.wfd th,.wfd td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}.wfd th{font-weight:500;color:var(--muted)}.wfd .table-wrap{overflow:auto;margin-top:12px}.wfd .regions-table{min-width:420px}.wfd .regions-table td:first-child{width:72px}.wfd .regions-table td:last-child{width:80px}.wfd .regions-table button{font-size:11px;padding:4px 6px}.wfd .technical{border-top:1px solid var(--line);padding-top:12px;margin-top:18px;font-size:11px}.wfd .technical dl{display:grid;grid-template-columns:92px minmax(0,1fr);gap:8px}.wfd dt{color:var(--muted)}.wfd dd{margin:0;overflow-wrap:anywhere}.wfd .empty{padding:48px 16px;text-align:center;color:var(--muted)}.wfd .empty p{margin-bottom:12px}.wfd .notice{border:1px solid var(--line);border-left:3px solid var(--accent);padding:10px 12px;margin-bottom:16px;border-radius:5px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}.wfd .notice.error{border-left-color:var(--bad);color:var(--bad)}.wfd .binding{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border:1px solid var(--line);padding:12px;margin-bottom:18px;border-radius:7px}.wfd .binding select{max-width:280px}.wfd .legend{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.wfd .legend small{font-size:10px}.wfd .warnings{margin-top:18px;font-size:12px}.wfd .backdrop{position:absolute;inset:0;z-index:30;background:var(--overlay);display:flex;align-items:center;justify-content:center;padding:20px}.wfd .dialog{background:var(--bg);border:1px solid var(--line);border-radius:12px;width:min(1060px,100%);max-height:100%;display:flex;flex-direction:column;overflow:hidden}.wfd .dialog-head{display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line);padding:16px 20px}.wfd .dialog-content{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,1fr);min-height:0;overflow:auto}.wfd .dialog-content.no-image{grid-template-columns:minmax(0,1fr)}.wfd .image-pane{padding:18px;background:var(--layer);min-width:0;overflow:auto}.wfd .image-stage{position:relative;width:fit-content;max-width:100%;line-height:0;margin:auto;touch-action:none;cursor:crosshair}.wfd .image-stage>img{display:block;max-width:100%;max-height:52vh;object-fit:contain;user-select:none}.wfd .region-overlay,.wfd .selection{position:absolute;border:2px solid var(--accent);border-radius:2px;background:transparent;min-width:5px;min-height:5px;padding:0}.wfd .region-overlay{border-width:1px;opacity:.65;cursor:pointer}.wfd .region-overlay:hover{opacity:1}.wfd .region-overlay span{position:absolute;left:0;top:0;background:var(--accent);color:var(--bg);font-size:10px;line-height:16px;padding:0 3px}.wfd .selection{pointer-events:none;outline:1px solid var(--bg)}.wfd .image-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;font-size:11px;color:var(--muted)}.wfd .comment-pane{padding:20px;display:flex;flex-direction:column;gap:14px;min-width:0}.wfd .owner{padding:10px 12px;background:var(--layer);border-left:2px solid var(--accent);border-radius:4px;font-size:12px;overflow-wrap:anywhere}.wfd .owner small{display:block;margin-top:3px}.wfd .comment-pane textarea{width:100%;min-height:140px;resize:vertical}.wfd .field{display:flex;flex-direction:column;gap:6px}.wfd .coordinates{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.wfd .crop{width:100%;height:94px;display:block;background:var(--raised);border:1px solid var(--line);border-radius:5px}.wfd .dialog-footer{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:14px 20px;border-top:1px solid var(--line)}.wfd .confirm{width:min(420px,100%);padding:24px;gap:16px}.wfd .actions{display:flex;gap:8px;flex-wrap:wrap}.wfd .saved-link{font-size:12px;overflow-wrap:anywhere}.wfd .session-code{font-size:10px;font-variant-numeric:tabular-nums}.wfd .preview-fields td:first-child{width:35%;color:var(--muted)}
@media(max-width:1100px){.wfd .layout{grid-template-columns:210px minmax(0,1fr)}.wfd .main{padding:18px 16px}.wfd .session-title{max-width:155px}.wfd .flow{padding:16px}.wfd .context{max-width:35%}.wfd .dialog-content{grid-template-columns:minmax(0,1fr) minmax(260px,1fr)}}
@media(max-width:768px){.wfd .layout{grid-template-columns:168px minmax(0,1fr)}.wfd .sidebar{padding:12px 8px}.wfd .session-title{max-width:120px}.wfd .main{padding:14px 12px}.wfd .top{padding:10px 12px;gap:10px}.wfd .context{display:none}.wfd .flow-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wfd .flow-cell:not(:last-child):after{right:-13px;font-size:12px}.wfd .node{padding:10px;min-height:110px}.wfd .node[aria-pressed=true]{padding:9px}.wfd .dialog-content{grid-template-columns:minmax(0,1fr)}.wfd .image-stage>img{max-height:32vh}.wfd .dialog{max-height:100%}.wfd .backdrop{padding:10px}.wfd .comment-pane{padding:14px}.wfd .run-head{flex-wrap:wrap}}
@media(max-width:480px){.wfd .layout{display:flex;flex-direction:column}.wfd .sidebar{max-height:160px;border-right:0;border-bottom:1px solid var(--line);flex-shrink:0}.wfd .sidebar input{margin-bottom:8px}.wfd .session-title{max-width:100%}.wfd .sidebar-label{padding-bottom:4px}.wfd .session{margin-bottom:4px}.wfd .main{flex:1}.wfd .history{margin-top:8px}.wfd .run{padding:4px 8px}.wfd .top-title strong{font-size:13px}.wfd .connection{font-size:10px}.wfd .flow{padding:12px}.wfd .flow-grid{gap:14px}.wfd .node{padding:8px;min-height:116px}.wfd .node[aria-pressed=true]{padding:7px}.wfd .node-title{font-size:12px}.wfd .detail-head,.wfd .detail-body{padding:12px}.wfd .dialog-footer{padding:12px}.wfd .dialog-footer button{font-size:12px}.wfd .dialog-head{padding:12px}.wfd .image-pane{padding:12px}}
@media(prefers-reduced-motion:reduce){.wfd *{animation:none!important;transition:none!important}.wfd button:active:not(:disabled){transform:none}}
`;
    const presentationCss = `.wfd .session-progress{display:block;width:100%;height:3px;margin-top:8px;accent-color:var(--accent)}.wfd .session-activity{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:185px;margin-top:5px}.wfd .output-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}.wfd .output-tabs [aria-selected=true]{border-color:var(--accent);color:var(--accent)}.wfd .more-actions button{display:block;margin-top:8px}.wfd .step-command{padding:16px 18px;border-top:1px solid var(--line)}.wfd .step-command textarea{width:100%;min-height:80px;resize:vertical}.wfd .step-command .actions{margin-top:10px}.wfd .artifact-provenance{display:block;margin:5px 0 10px}.wfd .explicit-plan{line-height:2.2;padding-left:22px}.wfd .node{min-height:98px;white-space:normal}.wfd .flow-grid{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}.wfd .gallery{grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))}.wfd .gallery .art-open img{max-height:60vh}.wfd .top{gap:10px}@media(max-width:768px){.wfd .flow-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.wfd .top button{font-size:11px;padding:6px 8px}}.wfd .reading{font-size:14px;line-height:1.8;margin-bottom:20px}.wfd .reading-paragraph{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 12px}.wfd .translation-list{border-top:1px solid var(--line)}.wfd .translation-row{display:grid;grid-template-columns:minmax(0,1fr) 24px minmax(0,1fr);gap:8px 12px;align-items:start;padding:13px 0;border-bottom:1px solid var(--line)}.wfd .source-text,.wfd .target-text{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}.wfd .source-text{color:var(--muted)}.wfd .target-text{font-weight:600}.wfd .translation-arrow{text-align:center;color:var(--muted)}.wfd .recognized-row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}.wfd .recognized-row .source-text{color:var(--ink);flex:1}.wfd .language-tag,.wfd .region-concern{grid-column:1/-1}.wfd .region-concern{color:var(--dsw-alias-state-warning-primary,var(--muted))}.wfd .review-findings{padding-left:20px;margin:12px 0;line-height:1.8}.wfd .review-findings li{padding:5px 0}.wfd .review-findings li[data-verdict=fail],.wfd .review-findings li[data-verdict=error]{color:var(--bad)}.wfd .final-results:empty{display:none}.wfd .final-results,.wfd .result-comparisons{margin-bottom:24px}.wfd .final-results h3,.wfd .comparisons h3{margin-bottom:12px}.wfd .gallery.stage-gallery{grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))}.wfd .gallery .art-open img{height:auto;max-height:480px;object-fit:contain}.wfd .result-gallery,.wfd .comparison-gallery{grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr))}.wfd .result-gallery .art-open img,.wfd .comparison-gallery .art-open img{max-height:65vh;height:auto;object-fit:contain}.wfd .comparison-pair{margin-bottom:18px}.wfd .comparison-pair h4{margin:0 0 8px}.wfd .debug{margin-top:24px;border-top:1px solid var(--line);padding-top:12px}.wfd .flow-grid:empty{display:none}`;
    const button = (label, onClick, disabled = false, props = {}) => h('button', { type: 'button', onClick, disabled, ...props }, label);
    const badge = (status) => h('span', { className: 'badge', 'data-status': status || 'unknown' }, statusName(status));
    function Icon({ size = 18 }) {
      return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, 'aria-hidden': true }, h('rect', { x: 3, y: 3, width: 7, height: 7, rx: 2 }), h('rect', { x: 14, y: 14, width: 7, height: 7, rx: 2 }), h('path', { d: 'M10 6h7v8M6 10v7h8' }));
    }
    function Reading({ content, recognition = false, locate, canLocate = false }) {
      return h('div', { className: 'reading', 'data-testid': 'stage-content' }, content.paragraphs.map((line, index) => h('p', { key: `p-${index}`, className: 'reading-paragraph' }, line)),
        content.regions.length > 0 && h('div', { className: 'translation-list', 'data-testid': recognition ? 'recognized-text' : 'translation-text' }, content.regions.map((region, index) => {
          const issue = Array.isArray(region.issues) ? region.issues.map((item) => words(item) || words(item?.note) || words(item?.message)).filter(Boolean).join('；') : '';
          const uncertain = region.recognition_verified === false || region.unresolved === true || region.status === 'unresolved' || (Array.isArray(region.unresolved) && region.unresolved.length > 0) || !!issue;
          return h('div', { className: recognition ? 'recognized-row' : 'translation-row', 'data-testid': recognition ? 'recognition-row' : 'translation-row', key: `${region.language || ''}-${region.id || index}-${index}` },
            h('span', { className: 'source-text', 'data-testid': 'translation-source' }, region.source_text || '—'), !recognition && h('span', { className: 'translation-arrow', 'aria-label': '翻译为' }, '→'), !recognition && h('strong', { className: 'target-text', 'data-testid': 'translation-target' }, region.target_text || (region.action === 'preserve' ? '保留原文' : '待定稿')),
            words(region.language) && h('small', { className: 'language-tag' }, region.language), uncertain && h('small', { className: 'region-concern' }, `待确认${issue || words(region.notes) ? ' · ' + (issue || words(region.notes)) : ''}`), canLocate && locate && button('定位', () => locate(region)));
        })),
        content.checks.length > 0 && h('ul', { className: 'review-findings', 'data-testid': 'review-conclusions' }, content.checks.map((check, index) => h('li', { key: index, 'data-verdict': check.verdict || check.severity || '' }, check.text))));
    }
    function NodeButton({ node, selected, onSelect, compact, disabled }) {
      const { primary } = nodeGallery(node);
      const images = primary.filter((item) => item.kind === 'image' && assetUrl(item.url));
      const image = images[0], content = nodeContent(node);
      const summary = images.length ? `${images.length} 张图` : content.regions.length ? `${content.regions.length} 条文字` : usefulContent(content) ? '文字记录' : null;
      if (compact) return button(h(React.Fragment, null, node.title || node.id, badge(node.status)), () => onSelect(node.id), !!disabled, { 'aria-pressed': selected === node.id, 'data-testid': 'workflow-node', 'data-node-id': node.id });
      return h('div', { className: 'flow-cell' }, (node.branch || node.language || node.scope) && h('div', { className: 'branch' }, text(node.branch || node.language || node.scope)), button(h(React.Fragment, null, h('span', { className: 'node-title' }, node.title || node.id), h('span', null, badge(node.status)), h('span', { className: 'node-meta' }, image && h('img', { src: assetUrl(image.url), alt: '', loading: 'lazy' }), summary)), () => onSelect(node.id), !!disabled, { className: 'node', 'aria-pressed': selected === node.id, 'data-status': node.status || 'unknown', 'data-testid': 'workflow-node', 'data-node-id': node.id }));
    }
    function CropPreview({ artifact, box, dimensions }) {
      const src = assetUrl(artifact?.url);
      if (!src || !box || !dimensions) return null;
      const [width, height] = dimensions, cropWidth = Math.max(box[2] * width, 24), cropHeight = Math.max(box[3] * height, 24);
      const x = Math.max(0, Math.min(width - cropWidth, box[0] * width - (box[2] ? 0 : 12))), y = Math.max(0, Math.min(height - cropHeight, box[1] * height - (box[3] ? 0 : 12)));
      return h('svg', { className: 'crop', viewBox: `${x} ${y} ${cropWidth} ${cropHeight}`, 'aria-label': '所选区域预览', role: 'img' }, h('image', { href: src, width, height }));
    }
    function CommentDialog({ draft, busy, onChange, onClose, onSave }) {
      const root = React.useRef(null), initialFocus = React.useRef(null), drag = React.useRef(null);
      const [dimensions, setDimensions] = React.useState(draft.artifact?.width && draft.artifact?.height ? [draft.artifact.width, draft.artifact.height] : null);
      const [showRegions, setShowRegions] = React.useState(true), [imageError, setImageError] = React.useState(false), [liveBox, setLiveBox] = React.useState(null);
      const src = assetUrl(draft.artifact?.url);
      React.useEffect(() => { const previous = document.activeElement; initialFocus.current?.focus(); return () => { if (previous?.isConnected) previous.focus(); }; }, []);
      function keyboard(event) {
        if (event.key === 'Escape') { event.preventDefault(); if (!busy) onClose(); }
        if (event.key !== 'Tab') return;
        const focusable = [...root.current.querySelectorAll('button:not(:disabled),textarea:not(:disabled),input:not(:disabled),a[href],select:not(:disabled)')];
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
      function point(event) { const rect = event.currentTarget.getBoundingClientRect(); return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))]; }
      function start(event) {
        if (busy || !draft.session_id || draft.saved || draft.submitted || event.button !== 0 || event.target.closest('button')) return;
        const position = point(event); drag.current = { position, pointerId: event.pointerId };
        event.currentTarget.setPointerCapture(event.pointerId); setLiveBox([...position, 0, 0]);
      }
      function move(event) { if (!drag.current) return; const current = point(event), origin = drag.current.position; setLiveBox([Math.min(current[0], origin[0]), Math.min(current[1], origin[1]), Math.abs(current[0] - origin[0]), Math.abs(current[1] - origin[1])]); }
      function finish(event) {
        if (!drag.current) return;
        const current = point(event), origin = drag.current.position;
        const selected = selectionBox(origin, current, dimensions);
        drag.current = null; setLiveBox(null); if (selected) onChange({ ...selected, region_id: undefined, selectedRegion: undefined });
      }
      const boxStyle = (box) => ({ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${box[2] * 100}%`, height: `${box[3] * 100}%` });
      const selectedBox = liveBox || draft.bbox, locked = !!busy || !draft.session_id || !!draft.saved || !!draft.submitted;
      return h('div', { className: 'backdrop' }, h('section', { ref: root, className: 'dialog', role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'wfd-comment-heading', 'data-testid': 'node-comment-dialog', onKeyDown: keyboard },
        h('header', { className: 'dialog-head' }, h('div', { className: 'grow' }, h('h3', { id: 'wfd-comment-heading' }, `${draft.node.title || draft.node.id} · 点评 / 调整`), h('small', null, draft.run.title || draft.run.id)), button('关闭', onClose, !!busy, { 'aria-label': '关闭点评' })),
        h('div', { className: `dialog-content${src ? '' : ' no-image'}` }, src && h('div', { className: 'image-pane' }, h('div', { className: 'image-tools' }, h('span', { className: 'grow' }, '点击标记 · 拖动框选'), button(showRegions ? '隐藏区域' : '显示区域', () => setShowRegions(!showRegions), !!busy), selectedBox && button('清除选区', () => onChange({ bbox: undefined, region_id: undefined }), locked)),
          imageError ? h('p', { className: 'muted' }, '图片读取失败，可继续记录节点意见。') : h('div', { className: 'image-stage', onPointerDown: start, onPointerMove: move, onPointerUp: finish, onPointerCancel: () => { drag.current = null; setLiveBox(null); } },
            h('img', { src, alt: artifactName(draft.artifact), draggable: false, onError: () => setImageError(true), onLoad: (event) => { const dims = [event.target.naturalWidth, event.target.naturalHeight]; setDimensions(dims); if (draft.pendingRegion) onChange({ bbox: regionBox(draft.pendingRegion, ...dims) || undefined, pendingRegion: undefined }); } }),
            dimensions && showRegions && (draft.artifact?.regions || []).map((region, index) => { const box = regionBox(region, ...dimensions); return box && h('button', { key: region.id || index, className: 'region-overlay', style: boxStyle(box), type: 'button', disabled: locked, 'aria-label': `点评区域 ${region.id || index + 1}`, title: `${region.source_text || ''} → ${region.target_text || ''}`, onClick: () => onChange({ region_id: region.id, bbox: box, selectedRegion: region, isPoint: false }) }, h('span', null, region.id || index + 1)); }), selectedBox && h('div', { className: 'selection', style: boxStyle(selectedBox) }))),
          h('div', { className: 'comment-pane' }, h('div', { className: 'owner', title: draft.session_id || '' }, '原会话 · ', draft.sessionTitle || '历史未关联', draft.session_id && h('small', null, shortId(draft.session_id))), draft.artifact && h('strong', null, artifactName(draft.artifact)),
            draft.bbox && h(CropPreview, { artifact: draft.artifact, box: draft.bbox, dimensions }),
            h('details', null, h('summary', null, '定位详情'), h('pre', null, JSON.stringify({ session_id: draft.session_id, run_id: draft.run.id, node_id: draft.node.id, artifact_id: draft.artifact?.id, sha256: draft.artifact?.sha256, region_id: draft.region_id, bbox: draft.bbox }, null, 2))),
            draft.selectedRegion && h('div', { className: 'coordinates' }, h('div', null, `原文：${draft.selectedRegion.source_text || '—'}`), h('div', null, `译文：${draft.selectedRegion.target_text || '—'}`)),
            h('label', { className: 'field' }, h('span', null, '具体意见'), h('textarea', { ref: initialFocus, value: draft.note, disabled: locked, maxLength: 10000, placeholder: '描述这里需要调整的文字、背景或构图…', onChange: (event) => onChange({ note: event.target.value }) })),
            !draft.session_id && h('small', null, '先关联原会话'), draft.message && h('div', { className: `notice${draft.error ? ' error' : ''}`, role: draft.error ? 'alert' : 'status' }, draft.message), draft.saved?.prompt && h('details', null, h('summary', null, '查看已保存的发送内容'), h('pre', null, draft.saved.prompt)))),
        h('footer', { className: 'dialog-footer' }, h('small', { className: 'grow' }, busy ? '处理中…' : draft.deliveryStatus === 'queued' ? '已接收 · 等待处理' : ''), button(busy === 'save' ? '保存中…' : '只保存点评', () => onSave(false), !!busy || !draft.session_id || !draft.note.trim() || !!draft.saved || !!draft.persisted), button(busy === 'send' ? '发送中…' : draft.deliveryStatus === 'queued' ? '已接收' : '发送到原会话', () => onSave(true), !!busy || !draft.session_id || !draft.canSend || !draft.note.trim() || draft.deliveryStatus === 'queued', { className: 'primary' }))));
    }
    function apply(ctx) {
      ctx.effect(() => ctx.locale.register('image-review-desk', { zh: { title: '流程台' }, en: { title: 'Workflow desk' } }), 'image workflow dictionaries');
      const t = ctx.locale.bind('image-review-desk');
      function Panel({ useSessions, useWorkspaces }) {
        const list = useSessions((state) => state), archivedIds = useWorkspaces((state) => state.archivedSessionIds);
        const [data, setData] = React.useState(null), [selection, setSelection] = React.useState({ session: '', run: '' }), [nodeId, setNodeId] = React.useState('');
        const [search, setSearch] = React.useState(''), [connected, setConnected] = React.useState(false), [loading, setLoading] = React.useState(false), [notice, setNotice] = React.useState(null);
        const [draft, setDraft] = React.useState(restoreDraft), [busy, setBusy] = React.useState(''), [pending, setPending] = React.useState(null);
        const [bindTarget, setBindTarget] = React.useState(''), [resultId, setResultId] = React.useState(''), [savedResult, setSavedResult] = React.useState(null);
        const [deliveryTab, setDeliveryTab] = React.useState('result'), [instruction, setInstruction] = React.useState(''), [selectedPlan, setSelectedPlan] = React.useState(null);
        const [historyLoading, setHistoryLoading] = React.useState(false), historySequence = React.useRef(0);
        const [detailLoading, setDetailLoading] = React.useState(false);
        const request = React.useRef(null), alive = React.useRef(false), state = React.useRef({});
        const dirty = (!!draft && !!draft.note.trim() && !draft.saved && !draft.persisted) || !!instruction.trim();
        state.current = { draft, busy, dirty, selection, data };
        function guard(action) { if (state.current.busy) return; if (state.current.dirty) { setPending(() => action); return; } setDraft(null); setInstruction(''); action(); }
        async function refresh() {
          if (request.current) return;
          const controller = new AbortController(); request.current = controller; setLoading(true);
          try {
            const next = await api('workflow', { signal: controller.signal });
            if (next.schema_version !== 1 || !Array.isArray(next.sessions) || !Array.isArray(next.unassigned)) throw new Error('流程数据格式不完整，请刷新重试。');
            if (alive.current) { setData((previous) => mergeWorkflowSnapshot(next, previous)); setConnected(true); setNotice((old) => old?.source === 'connection' ? null : old); }
          } catch (failure) { if (alive.current && failure.name !== 'AbortError') { setConnected(false); setNotice({ error: true, message: failure.message }); } }
          finally { if (request.current === controller) { request.current = null; if (alive.current) setLoading(false); } }
        }
        React.useEffect(() => { alive.current = true; const restored = state.current.draft; if (restored) setSelection({ session: restored.session_id || '__unassigned', run: restored.run.id }); refresh(); const timer = setInterval(refresh, 2000); return () => { alive.current = false; clearInterval(timer); request.current?.abort(); }; }, []);
        React.useEffect(() => {
          try { if (draft && (draft.note.trim() || draft.saved)) window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); else window.sessionStorage.removeItem(DRAFT_KEY); } catch { /* In-memory draft remains usable when storage is unavailable. */ }
        }, [draft]);
        React.useEffect(() => { const unload = (event) => { if (state.current.dirty || state.current.busy) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', unload); return () => window.removeEventListener('beforeunload', unload); }, []);
        const sessions = data?.sessions || [], session = sessions.find((row) => row.session_id === selection.session), unassigned = selection.session === '__unassigned';
        const knownSessionIds = new Set(sessions.map((item) => item.session_id));
        const candidates = Object.values(list.byId || {}).filter((row) => (knownSessionIds.has(row.id) || canTarget(row)) && row.origin !== 'subagent' && !(archivedIds || []).includes(row.id));
        const runs = unassigned ? data?.unassigned || [] : session?.runs || [], run = runs.find((row) => row.id === selection.run || row.root === selection.run) || (!unassigned && !selection.run ? runs.find(item => businessStages(workflowNodes(item)).some(stage => stage.status === 'running')) || runs.at(-1) : undefined);
        const ownerId = !unassigned && run && run.session_id === session?.session_id ? run.session_id : '';
        React.useEffect(() => {
          const sid = session?.session_id;
          if (!sid || session.detail_loaded === undefined) return;
          let disposed = false, inFlight = false, controller;
          const readDetail = async () => {
            if (disposed || inFlight) return;
            inFlight = true; controller = new AbortController(); setDetailLoading(true);
            try {
              const detail = await api(`workflow/session?session_id=${encodeURIComponent(sid)}`, { signal: controller.signal });
              if (detail.session?.session_id !== sid || !Array.isArray(detail.session.runs)) throw new Error('会话步骤结果的身份不匹配，请刷新重试。');
              if (!disposed && alive.current) { setData(old => mergeSessionDetail(old, detail, sid)); setNotice(old => old?.source === 'detail' ? null : old); }
            } catch (failure) {
              if (!disposed && alive.current && failure.name !== 'AbortError') setNotice({ error: true, source: 'detail', message: failure.message });
            } finally { inFlight = false; if (!disposed && alive.current) setDetailLoading(false); }
          };
          readDetail(); const timer = setInterval(readDetail, 4000);
          return () => { disposed = true; clearInterval(timer); controller?.abort(); };
        }, [session?.session_id, session?.detail_loaded !== undefined]);
        React.useEffect(() => {
          const sequence = ++historySequence.current;
          if (!unassigned || !run?.root || !run?.id) { setHistoryLoading(false); return; }
          const controller = new AbortController(), expected = { id: run.id, root: run.root };
          setHistoryLoading(true);
          const current = () => alive.current && historySequence.current === sequence && state.current.selection.session === '__unassigned' && [expected.id, expected.root].includes(state.current.selection.run);
          api(`workflow/unassigned?run_root=${encodeURIComponent(expected.root)}`, { signal: controller.signal }).then((result) => {
            if (!current()) return;
            if (result.run?.id !== expected.id || result.run?.root !== expected.root || !Array.isArray(result.run.nodes)) throw new Error('历史流程不匹配，请重试。');
            setData((previous) => previous && ({ ...previous, unassigned: previous.unassigned.map((metadata) => metadata.id === expected.id && metadata.root === expected.root ? { ...metadata, ...result.run } : metadata) }));
          }).catch((failure) => { if (current() && failure.name !== 'AbortError') setNotice({ error: true, message: failure.message }); })
            .finally(() => { if (current()) setHistoryLoading(false); });
          return () => { controller.abort(); if (historySequence.current === sequence) historySequence.current++; };
        }, [unassigned, run?.id, run?.root]);
        const allNodes = workflowNodes(run, data?.node_catalog);
        const nodes = visibleNodes(allNodes), stages = businessStages(allNodes);
        const stage = selectedStage(stages, nodeId);
        const node = stage?.members.find(item => item.id === nodeId) || stage?.members.find(item => item.status === 'running') || stage?.members.find(item => item.status === 'failed') || stage?.members[0];
        const currentStage = stages.find(row => row.status === 'running');
        const outputs = resultCandidates(run), resultArtifact = selectedResult(run, resultId);
        const controlSessionId = ownerId || (!unassigned ? session?.session_id : '');
                const controlIntent = React.useRef(null);
        async function control(action, extra = {}) {
          if (!controlSessionId || busy) return;
          if (action === 'open') { try { await ctx.uiWorkspace.openSession(controlSessionId); ctx.layout.selectPanel(null); } catch (failure) { setNotice({ error: true, message: failure.message || '打开会话失败。' }); } return; }
          if (action !== 'stop' && run && !node) { setNotice({ error: true, message: '请选择仍可用的步骤，再发送指令。' }); return; }
          const instructionText = action === 'continue' ? '请继续执行' + ((node?.title || selectedPlan?.content) ? '“' + (node?.title || selectedPlan.content) + '”这一步，完成后报告重要结果。' : '当前计划中的下一步，完成后报告重要结果。') : extra.text;
          const input = { action: action === 'stop' ? 'cancel' : 'prompt', session_id: controlSessionId, ...(run ? { run_id: run.id } : {}), ...(node ? { node_id: node.id } : {}), ...(action === 'stop' ? {} : { instruction: instructionText, mode: 'queue', ...(!run && selectedPlan ? { plan_event_id: selectedPlan.event_id, plan_index: selectedPlan.index } : {}) }) };
          controlIntent.current = controlIntentFor(controlIntent.current, input, crypto.randomUUID());
          const intent = controlIntent.current;
          setBusy(action); state.current.busy = action;
          try {
            await dispatchControlIntent(intent, input => post('workflow/control', input), ctx.remote.session);
            if (input.action === 'prompt') setInstruction('');
            controlIntent.current = null;
            setNotice({ message: input.action === 'cancel' ? '停止本轮的请求已接收。' : '指令已接收。' });
          } catch (failure) { setNotice({ error: true, message: failure.message || '会话操作未确认，请重试。' }); }
          finally { state.current.busy = ''; if (alive.current) setBusy(''); }
        }
        function chooseRun(sessionId, row) { guard(() => { setSelection({ session: sessionId, run: row.id || row.root }); setNodeId(''); setNotice(null); setBindTarget(''); setResultId(''); setSavedResult(null); setDeliveryTab('result'); setInstruction(''); setSelectedPlan(null); }); }
        function chooseSession(row) { guard(() => { setSelection({ session: row.session_id, run: row.runs?.find(item => businessStages(workflowNodes(item)).some(stage => stage.status === 'running'))?.id || row.runs?.at(-1)?.id || row.runs?.at(-1)?.root || '' }); setNodeId(''); setNotice(null); setBindTarget(''); setSavedResult(null); setResultId(''); setDeliveryTab('result'); setInstruction(''); setSelectedPlan(null); }); }
        function openComment(artifact, region, owningNode = node) {
          guard(() => { const dims = artifact?.width && artifact?.height ? [artifact.width, artifact.height] : null;
            setDraft({ session_id: ownerId, sessionTitle: session ? sessionTitle(session, list.byId) : '历史未关联', canSend: !!ownerId && session?.can_send !== false, run: { id: run.id, root: run.root, title: run.title }, node: { ...owningNode }, artifact: artifact ? { ...artifact } : null, selectedRegion: region, region_id: region?.id, bbox: dims && region ? regionBox(region, ...dims) : undefined, pendingRegion: artifact && !dims && region ? region : undefined, note: '', request_id: crypto.randomUUID(), saved: null, message: '', error: false }); });
        }
        function regionImage(region) {
          for (const candidate of allNodes) {
            for (const artifact of candidate.artifacts) {
              const mapped = artifact.kind === 'image' && artifact.regions?.find((item) => item.id === region.id && item.source_text === region.source_text && item.target_text === region.target_text);
              if (mapped) return { artifact, region: mapped, node: candidate };
            }
          }
          return null;
        }
        function locateRegion(region) {
          const mapped = regionImage(region);
          if (mapped) openComment(mapped.artifact, mapped.region, mapped.node); else openComment(null, region);
        }
        function reopenComment(record) {
          const owningNode = allNodes.find((item) => item.id === record.node_id);
          if (!owningNode) return;
          const artifact = record.artifact_id ? owningNode.artifacts.find((item) => item.id === record.artifact_id && item.sha256 === record.artifact_sha256) || { id: record.artifact_id, sha256: record.artifact_sha256, name: '历史产物（当前未索引）' } : null;
          const delivery = [...(run.deliveries || [])].reverse().find((item) => item.comment_id === record.id && item.request_id === record.request_id);
          guard(() => setDraft({ session_id: record.session_id, sessionTitle: session ? sessionTitle(session, list.byId) : '历史未关联', canSend: session?.can_send !== false && record.session_id === ownerId, run: { id: run.id, root: run.root, title: run.title }, node: owningNode, artifact, bbox: record.bbox, region_id: record.region_id, selectedRegion: artifact?.regions?.find((item) => item.id === record.region_id) || owningNode.regions?.find((item) => item.id === record.region_id), note: record.note, request_id: record.request_id, persisted: true, submitted: true, saved: null, deliveryStatus: delivery?.status, message: delivery?.status === 'queued' ? '原会话已接收此点评。' : '点评已保存；继续发送将复用原请求编号。', error: false }));
        }
        function changeDraft(patch) { setDraft((old) => old && !old.saved && !old.submitted ? { ...old, ...patch, message: '', error: false } : old); }
        function patchDraft(patch) { if (alive.current) setDraft((old) => old && ({ ...old, ...patch })); }
        async function recordDelivery(record, status, error) { await post('workflow/delivery', { session_id: record.session_id, comment_id: record.id, request_id: record.request_id, status, ...(error ? { error } : {}) }); }
        async function saveComment(send) {
          const snapshot = state.current.draft;
          if (!snapshot || state.current.busy || !snapshot.session_id || !snapshot.note.trim() || (send && (!snapshot.canSend || snapshot.deliveryStatus === 'queued'))) return;
          const liveSession = state.current.data?.sessions?.find((item) => item.session_id === snapshot.session_id);
          const liveRun = liveSession?.runs?.find((item) => item.id === snapshot.run.id && item.session_id === snapshot.session_id);
          const liveNode = liveRun?.nodes?.find((item) => item.id === snapshot.node.id);
          const liveArtifact = liveNode?.artifacts?.find((item) => item.id === snapshot.artifact?.id);
          if (!liveNode || (send && liveSession.can_send === false) || (snapshot.artifact && (!liveArtifact || liveArtifact.sha256 !== snapshot.artifact.sha256))) {
            patchDraft({ message: '原会话或产物证据已变化，请保留意见，重新打开对应节点后核对。', error: true }); return;
          }
          state.current.busy = send ? 'send' : 'save'; setBusy(state.current.busy); patchDraft({ submitted: true });
          const expected = commentPayload(snapshot);
          let saved = snapshot.saved;
          try {
            if (!saved) { saved = await post('workflow/comments', expected); if (!validComment(saved, expected)) throw new Error('点评回执的归属或版本不一致，已停止发送。意见和请求编号已保留。'); patchDraft({ saved, message: '点评已保存。', error: false }); }
            if (!send) return;
            if (!validComment(saved, expected) || typeof saved.prompt !== 'string' || !saved.prompt.trim()) throw new Error('点评回执缺少有效的原会话指令，已保留点评。');
            let response;
            try { response = await ctx.remote.session.prompt({ requestId: saved.comment.request_id, sessionId: saved.comment.session_id, mode: 'queue', content: [{ type: 'text', text: saved.prompt }], clientTimeZone: 'Asia/Shanghai' }); }
            catch (failure) { patchDraft({ deliveryStatus: 'unknown', message: '发送结果尚未确认，点评已保存。可使用同一请求编号手动重试。', error: true }); try { await recordDelivery(saved.comment, 'unknown', failure.message); } catch { patchDraft({ message: '发送结果尚未确认，派发记录暂未保存；点评与原请求编号已保留。' }); } return; }
            if (!response.ok || response.value?.accepted !== true) {
              const message = response.error?.message || '原会话尚未确认接收。'; patchDraft({ deliveryStatus: 'failed', message: `${message} 点评已保存，可用同一请求编号重试。`, error: true });
              try { await recordDelivery(saved.comment, 'failed', message); } catch { patchDraft({ message: `${message} 派发记录暂未保存；点评与原请求编号已保留。` }); } return;
            }
            patchDraft({ deliveryStatus: 'queued', message: '原会话已接收，等待处理。', error: false });
            try { await recordDelivery(saved.comment, 'queued'); } catch { patchDraft({ message: '原会话已接收；派发记录暂未保存，请勿重复发送。', error: true }); }
          } catch (failure) { patchDraft({ message: failure.message, error: true }); }
          finally { state.current.busy = ''; if (alive.current) setBusy(''); }
        }
        async function bindRun() {
          if (!run || !unassigned || !bindTarget || state.current.busy) return; state.current.busy = 'bind'; setBusy('bind');
          try { await post('workflow/bind', { session_id: bindTarget, run_root: run.root, manual: true, title: run.title }); setSelection({ session: bindTarget, run: run.id || run.root }); setNotice({ message: '已关联到所选原会话。' }); await refresh(); }
          catch (failure) { setNotice({ error: true, message: failure.message }); } finally { state.current.busy = ''; if (alive.current) setBusy(''); }
        }
        async function saveResult() {
          if (!ownerId || !resultArtifact?.id || !resultArtifact.sha256 || state.current.busy) return; state.current.busy = 'result'; setBusy('result'); setSavedResult(null);
          try {
            const result = await post('workflow/results/save', { session_id: ownerId, run_id: run.id, artifact_id: resultArtifact.id, sha256: resultArtifact.sha256 });
            const saved = result.saved;
            if (!saved || saved.session_id !== ownerId || saved.run_id !== run.id || saved.artifact_id !== resultArtifact.id || saved.sha256 !== resultArtifact.sha256) throw new Error('保存回执与所选结果不一致，请核对后刷新。');
            setSavedResult(saved); setNotice({ message: '结果已保存。' });
          }
          catch (failure) { setNotice({ error: true, message: failure.message }); } finally { state.current.busy = ''; if (alive.current) setBusy(''); }
        }
        function renderSession(row) {
          if (search && !`${row.title} ${row.session_id} ${(row.runs || []).map((item) => item.title).join(' ')}`.toLowerCase().includes(search.toLowerCase())) return null;
          const active = selection.session === row.session_id;
          return h('section', { key: row.session_id, className: `session${active ? ' active' : ''}`, 'data-testid': 'session-row', 'data-session-id': row.session_id }, button(h(React.Fragment, null, h('div', { className: 'grow' }, h('span', { className: 'session-title', title: sessionTitle(row, list.byId) }, sessionTitle(row, list.byId)), h('span', { className: 'session-meta' }, h('span', null, statusName(row.status)), h('span', null, sessionProgress(row).label)), sessionProgress(row).total > 0 && h('progress', { className: 'session-progress', value: sessionProgress(row).completed, max: sessionProgress(row).total, 'aria-label': '已完成阶段' }), sessionProgress(row).text && !sessionProgress(row).text.startsWith(sessionProgress(row).label) && h('small', { className: 'session-activity' }, sessionProgress(row).text))), () => chooseSession(row), !!busy, { className: 'session-head', 'aria-expanded': active }), active && h('div', { className: 'runs' }, row.runs?.length ? row.runs.map((item) => button(item.title || item.root || item.id, () => chooseRun(row.session_id, item), !!busy, { key: item.id || item.root, className: 'run', title: item.root || item.id, 'aria-pressed': selection.run === item.id || selection.run === item.root })) : h('small', null, '等待步骤事件')));
        }
        function renderArtifact(item, { hero = false, label = '' } = {}) {
          const src = item.kind === 'image' && assetUrl(item.url);
          if (!src) return null;
          const matches = (candidate) => candidate.artifacts.some((artifact) => artifact.id === item.id && artifact.sha256 === item.sha256);
          const owningNode = node && matches(node) ? node : allNodes.find(matches);
          const caption = label || (comparisonArtifact(item) && item.comparison_status === 'historical_or_changed_inputs' ? '历史对比' : comparisonArtifact(item) && item.comparison_status === 'existing_export' ? '已有对比' : '') || ({ 'source.png': '原图', 'translated.png': '最终成品', 'background.png': '无字背景', 'text-layer.png': '透明字层', 'overlay.png': '透明字层', 'overlay-preview.png': '字层预览', 'comparison.png': '结果对比', 'board.png': '结果对比', 'view.png': '预览' })[artifactName(item)] || (artifactName(item).length < 36 ? artifactName(item) : '阶段图片');
          const image = h('img', { src, alt: caption, loading: hero ? 'eager' : 'lazy', onError: (event) => { event.currentTarget.alt = '图片读取失败，请刷新'; } });
          return h('figure', { key: item.id || item.path, 'data-testid': hero ? 'final-artifact' : 'node-artifact' }, h('a', { className: 'art-open', href: src, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `查看 ${caption}` }, image), h('figcaption', null, h('strong', { title: item.name || item.path || item.id }, caption), h('div', { className: 'artifact-actions' }, owningNode && ownerId && h('details', { className: 'more-actions' }, h('summary', null, '更多'), button('点评区域', () => openComment(item, null, owningNode), !!busy)), h('a', { href: src, target: '_blank', rel: 'noopener noreferrer' }, '打开大图'))));
        }
        function renderComparisons(pairs) {
          const unique = new Map();
          for (const pair of pairs) {
            for (const [item, label] of [[pair.source, '原图'], [pair.result, pair.language ? '成品 · ' + pair.language : '成品']]) {
              const key = item.sha256 || item.id;
              if (!unique.has(key)) unique.set(key, { item, labels: [] });
              if (!unique.get(key).labels.includes(label)) unique.get(key).labels.push(label);
            }
          }
          return h('div', { className: 'gallery comparison-gallery' }, [...unique.values()].map(({ item, labels }) => renderArtifact(item, { label: labels.join(' / ') })));
        }
        function renderDetail() {
          if (!stage) return renderWaitingSession();
          const selectedImages = stage.id === 'delivery' ? (resultArtifact ? [resultArtifact] : outputs) : stage.artifacts.filter(item => !comparisonArtifact(item));
          const comparisons = stage.artifacts.filter(comparisonArtifact), pairs = stage.members.flatMap(item => comparisonPairs(item, allNodes));
          const details = stage.id === 'delivery' ? stage.artifacts.filter(item => !comparisonArtifact(item) && !outputs.some(output => output.id === item.id || (output.sha256 && output.sha256 === item.sha256))) : [];
          const comments = (run.comments || []).filter(item => stage.members.some(member => member.id === item.node_id));
          const histories = stage.historyArtifacts || [];
          const showComparison = stage.id === 'delivery' && deliveryTab === 'comparison';
          const renderOwned = (item, options = {}) => {
            const names = [...new Set((stage.sources.get(item.sha256 || item.id) || []).map(source => source.title))];
            return h('div', { key: item.id }, renderArtifact(item, { ...options, label: options.label || (historicalArtifact(item) ? '历史产物 · ' + artifactName(item) : '') }), names.length > 1 && h('small', { className: 'artifact-provenance' }, '来源 · ' + names.join(' / ')));
          };
          return h('section', { className: 'detail', 'data-testid': 'step-result' }, h('header', { className: 'detail-head' }, h('h3', { className: 'grow' }, stage.title + ' · 结果'), badge(stage.status), h('details', { className: 'more-actions' }, h('summary', null, '更多'), button('记录点评', () => openComment(null), !!busy || !ownerId), stage.id === 'delivery' && button(busy === 'result' ? '保存中…' : '保存结果', () => guard(saveResult), !!busy || !ownerId || !resultArtifact?.id, { 'data-testid': 'save-result' }))),
            h('div', { className: 'detail-body' }, stage.id === 'delivery' && h('div', { className: 'output-tabs', role: 'tablist', 'aria-label': '交付结果' }, button('成品', () => setDeliveryTab('result'), !!busy, { role: 'tab', 'data-view': 'result', 'aria-selected': !showComparison }), button('对比', () => setDeliveryTab('comparison'), !!busy, { role: 'tab', 'data-view': 'comparison', 'aria-selected': showComparison }), outputs.length > 1 && h('select', { 'aria-label': '结果产物', value: resultArtifact?.id || '', disabled: !!busy, onChange: event => setResultId(event.target.value) }, h('option', { value: '' }, '全部语言'), outputs.map(item => h('option', { key: item.id, value: item.id }, item.language || artifactName(item))))),
              !showComparison && usefulContent(stage.content) && h(Reading, { content: stage.content, recognition: stage.id === 'recognition', locate: locateRegion, canLocate: false }),
              !showComparison && selectedImages.length > 0 && h('div', { className: 'gallery stage-gallery', 'data-testid': stage.id === 'delivery' ? 'final-result' : 'stage-images' }, galleryArtifacts(selectedImages).primary.map(item => renderOwned(item, { hero: false, label: stage.id === 'delivery' ? (item.language ? '成品 · ' + item.language : '最终成品') : '' }))),
              showComparison && h('div', { 'data-testid': 'result-comparison' }, comparisons.some(item => item.comparison_current === true) ? h('div', { className: 'gallery comparison-gallery' }, comparisons.filter(item => item.comparison_current === true).map(item => renderOwned(item))) : pairs.length > 0 ? renderComparisons(pairs) : h('div', { className: 'gallery comparison-gallery' }, comparisons.map(item => renderOwned(item))), !comparisons.length && !pairs.length && h('p', { className: 'muted' }, '尚无已关联的对比结果')),
              showComparison && details.length > 0 && h('details', { className: 'technical' }, h('summary', null, '局部对比 · ' + details.length), h('div', { className: 'gallery' }, details.map(item => renderOwned(item)))),
              stage.status === 'running' && !usefulContent(stage.content) && !selectedImages.length && h('p', { className: 'muted' }, '执行中，等待结果…'),
              histories.length > 0 && h('details', { className: 'technical' }, h('summary', null, (stage.id === 'input' ? '其它输入与版本 · ' : '版本记录 · ') + histories.length), h('div', { className: 'gallery' }, histories.map(item => renderOwned(item, { label: stage.id === 'input' && !historicalArtifact(item) ? '其它输入' : '历史产物' })))),
              comments.length > 0 && h('details', { className: 'technical' }, h('summary', null, '已保存点评 · ' + comments.length + ' 条'), comments.map(record => h('article', { key: record.id, className: 'comment-record' }, h('p', null, record.note), button('查看点评', () => reopenComment(record), !!busy))))),
            renderCommand());
        }
        function renderCommand() {
          if (!controlSessionId || unassigned) return null;
          return h('form', { className: 'step-command', onSubmit: event => { event.preventDefault(); control('instruction', { text: instruction.trim() }); } }, h('label', { className: 'field' }, h('span', null, selectedPlan ? '向计划步骤发指令 · ' + selectedPlan.content : run ? '向这一步发指令' : '向会话发指令'), h('textarea', { 'data-testid': 'step-instruction', value: instruction, placeholder: '说明需要调整或继续的内容', disabled: !!busy, onChange: event => setInstruction(event.target.value) })), h('div', { className: 'actions' }, button('发送指令', () => control('instruction', { text: instruction.trim() }), !!busy || !instruction.trim() || session?.can_control?.prompt !== true, { className: 'primary', 'data-testid': 'step-send' }), button('继续', () => guard(() => control('continue')), !!busy || session?.can_control?.prompt !== true, { 'data-testid': 'step-continue' })));
        }
        function renderWaitingSession() {
          const activity = sessionActivity(session), plan = explicitPlan(session);
          return h('section', { className: 'detail waiting-session', 'data-testid': 'waiting-session' }, h('header', { className: 'detail-head' }, h('h3', null, '等待步骤事件')), h('div', { className: 'detail-body' }, session?.active_stage?.text && !statusOnlyText(session.active_stage.text) && h('p', { className: 'reading-paragraph', 'data-testid': 'session-current-stage' }, '当前步骤 · ' + session.active_stage.text), activity && h('p', { className: 'reading-paragraph', 'data-testid': 'session-activity' }, activity), plan.length > 0 && h('ol', { className: 'explicit-plan' }, plan.map((item, index) => h('li', { key: item.id || index, 'data-status': item.status }, button(item.title, () => guard(() => setSelectedPlan({ event_id: session.plan.event_id, index, content: item.title })), !!busy, { 'aria-pressed': selectedPlan?.index === index && selectedPlan?.event_id === session.plan.event_id }), h('small', null, ' · ' + ({ pending: '待执行', in_progress: '进行中', completed: '已完成' })[item.status])))), !activity && !plan.length && h('p', { className: 'muted' }, '会话已接入，收到步骤结果后会在这里更新。')), renderCommand());
        }
        return h('section', { className: 'wfd', 'data-testid': 'image-review-workbench', 'aria-label': '多会话流程台' }, h('style', null, css + presentationCss), h('div', { 'data-testid': 'workflow-desk', style: { display: 'contents' } },
          h('header', { className: 'top' }, h('div', { className: 'top-title' }, h(Icon), h('strong', null, '流程台')), h('div', { className: 'context grow' }, session ? sessionTitle(session, list.byId) : unassigned ? '历史未关联' : '多个会话 · 实时步骤'), h('div', { className: 'connection', 'data-state': connected ? 'ready' : 'error' }, h('span', { className: `dot${connected ? '' : ' off'}` }), loading ? '同步中' : connected ? '实时同步' : '连接中断'), button('打开对话', () => guard(() => control('open')), !!busy || !controlSessionId, { 'data-testid': 'session-open' }), button('停止本轮', () => guard(() => control('stop')), !!busy || !controlSessionId || !canStopSession(session), { 'data-testid': 'session-stop' }), button('刷新', refresh, loading || !!busy, { 'data-testid': 'workflow-refresh' })),
          h('div', { className: 'layout' }, h('aside', { className: 'sidebar' }, h('input', { value: search, placeholder: '搜索会话 / 流程', 'aria-label': '搜索会话或流程', onChange: event => setSearch(event.target.value) }), h('div', { className: 'sidebar-label' }, h('span', null, '会话'), h('span', null, sessions.length)), sessions.map(renderSession), !sessions.length && h('small', null, loading ? '正在读取需求会话…' : '暂无以需求序号命名的会话')),
            h('main', { className: 'main', 'aria-busy': loading && !data }, notice && h('div', { className: `notice${notice.error ? ' error' : ''}`, role: notice.error ? 'alert' : 'status' }, notice.message), session?.detail_loaded === false && h('div', { className: 'notice', role: 'status' }, detailLoading ? '正在读取此需求的步骤和结果…' : '选择需求后读取步骤和结果'), !run ? (session ? renderWaitingSession() : h('div', { className: 'empty' }, h('p', null, data ? '选择会话，查看流程和步骤结果。' : '正在连接流程台…'))) : h(React.Fragment, null,
              h('div', { className: 'run-head' }, h('div', { className: 'grow' }, h('h2', null, run.title || run.root || run.id), h('p', { className: 'run-subtitle' }, routeLabel(run.route) + ' · ' + stages.length + ' 个业务步骤'))),
              unassigned && h('div', { className: 'binding' }, h('strong', { className: 'grow' }, '历史未关联 · 只读'), h('select', { 'aria-label': '明确关联原会话', value: bindTarget, disabled: !!busy, onChange: event => setBindTarget(event.target.value) }, h('option', { value: '' }, '选择原会话'), candidates.map(row => h('option', { key: row.id, value: row.id }, row.displayTitle || row.title || '会话'))), button('确认关联', bindRun, !!busy || !bindTarget)),
              stages.length > 0 && h('section', { className: 'flow', 'aria-label': '主要流程图' }, h('div', { className: 'flow-head' }, h('h3', { className: 'grow' }, '制作流程'), currentStage && h('span', { className: 'muted' }, '执行中 · ' + currentStage.title)), h('div', { className: 'flow-grid' }, stages.map((item, index) => h('div', { key: item.id, className: 'flow-cell' }, button(h(React.Fragment, null, h('span', { className: 'node-title' }, h('small', { className: 'stage-number' }, String(index + 1).padStart(2, '0') + ' '), item.title), badge(item.status), h('small', null, item.artifacts.length ? item.artifacts.length + ' 张图' : item.content.regions.length ? item.content.regions.length + ' 条文字' : usefulContent(item.content) ? '结果记录' : '等待结果')), () => guard(() => { setNodeId(item.id); setDeliveryTab('result'); }), !!busy, { className: 'node', 'aria-pressed': stage?.id === item.id, 'data-testid': 'flow-stage', 'data-stage-id': item.id, 'data-node-ids': JSON.stringify(item.members.map(member => member.id)), 'data-status': item.status }))))), renderDetail(),
              savedResult && h('div', { className: 'notice saved-link' }, '已保存 · ', text(savedResult.name || savedResult.artifact_id), assetUrl(savedResult.url) && h('a', { href: assetUrl(savedResult.url), target: '_blank', rel: 'noopener noreferrer' }, '打开结果')),
              h('details', { className: 'technical debug' }, h('summary', null, '调试信息'), h('pre', null, JSON.stringify({ session_id: ownerId || null, run_id: run.id, root: run.root, selected_stage: stage?.id, nodes: allNodes }, null, 2).slice(0, 32000))))))),
          draft && h(CommentDialog, { key: draft.request_id, draft, busy, onChange: changeDraft, onClose: () => guard(() => setDraft(null)), onSave: saveComment }), pending && h('div', { className: 'backdrop' }, h('section', { className: 'dialog confirm', role: 'alertdialog', 'aria-modal': true, 'aria-label': '保护未发送内容' }, h('h3', null, '还有未发送的内容'), h('p', null, '继续编辑，或丢弃草稿后切换。'), h('div', { className: 'actions' }, button('继续编辑', () => setPending(null)), button('丢弃草稿并继续', () => { const action = pending; setPending(null); setDraft(null); setInstruction(''); action(); })))));
      }
      ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({ name: 'sidebar.panellist', id: PANEL, order: 15, label: () => t('title') }, Icon));
      ctx.slots.inject('main', () => ctx.slots.register({ name: 'main', key: PANEL }, Panel));
    }
    return { inject: ['slots', 'locale', 'sessions', 'remote', 'remote.session', 'uiWorkspace', 'layout'], apply,
      __test: { canStopSession, stageGallery, historicalArtifact, controlIntentFor, validControlIntent, dispatchControlIntent, businessStages, selectedStage, sessionActivity, sessionProgress, explicitPlan, groupStatus, nodeGallery, semanticContent, artifactContent, nodeContent, usefulNode, visibleNodes, selectedNode, comparisonPairs, comparisonSections, comparisonArtifact, artifactName, galleryArtifacts, sessionTitle, mergeUnassignedRun, mergeWorkflowSnapshot, mergeSessionDetail, routeLabel, statusName, normalizePath, canTarget, assetUrl, workflowNodes, catalogRows, normalizedBox, regionBox, selectionBox, validComment, resultCandidates, selectedResult, commentPayload, statusNames, auxiliary, BASE, PANEL, PROJECT } };
  },
});
