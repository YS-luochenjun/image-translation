import { posix, win32 } from 'node:path';
import { createHash } from 'node:crypto';
import { PROJECT, PROJECT_ID } from './host-adapter.js';

export const inject = ['sessionQuery', 'sessions', 'workingDirectory'];
export const CAPTURE_STAGES = Object.freeze({ init: 'task.initialize', 'init-job': 'job.initialize', packet: 'analysis.packet', validate: 'semantic.validate', 'prepare-job': 'job.freeze', 'prepare-layered': 'background.prepare', render: 'basic.render', 'render-job': 'job.render', 'render-layered': 'composite.render', 'check-job': 'quality.job', 'check-layered': 'quality.layered', 'review-packet': 'review.packet', quality: 'quality.basic', compare: 'evidence.compare', benchmark: 'evidence.benchmark' });
const FILE_STAGES = Object.freeze({ 'manifest.json': 'semantic.analyze', 'job.json': 'style.plan', 'plan.json': 'style.plan', 'background-review.json': 'background.review', 'review.json': 'review.visual', 'quality.json': 'quality.basic' });
const FILE_TOOLS = new Set(['write', 'edit', 'ops_write_file', 'ops_edit_file', 'read_image', 'ops_inspect_local_file']);
const MAX_ARGUMENT_BYTES = 65536;
const INGEST_REASONS = new Map([
  ['Workflow ledger budget exceeded', 'ledger-budget'],
  ['Workflow ledger unavailable or exceeds budget', 'ledger-unavailable'],
  ['Workflow ledger line budget exceeded', 'ledger-budget'],
  ['Request body too large or empty', 'body-budget'],
]);
function ingestFailureReason(response, failure) {
  let reason;
  if (response?.body && Buffer.byteLength(response.body) <= 8192) {
    try { reason = INGEST_REASONS.get(JSON.parse(response.body.toString()).error); } catch { /* Never expose raw response text. */ }
  }
  if (reason) return reason;
  const status = response?.status ?? failure?.status;
  if (Number.isInteger(status) && status >= 100 && status <= 599) return `http-${status}`;
  if (['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'EPIPE', 'ABORT_ERR'].includes(failure?.code)) return failure.code.toLowerCase();
  return 'transport-unavailable';
}
const safeId = (value) => typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\x00-\x1f]/.test(value);
const toolKey = (name) => typeof name === 'string' ? name.split('.').at(-1) : '';
const normalize = (path) => path.replace(/\\/g, '/').replace(/\/+$/, '');
const pathKey = (path) => /^[A-Za-z]:/.test(path) ? normalize(path).toLowerCase() : normalize(path);
const samePath = (left, right) => typeof left === 'string' && typeof right === 'string' && pathKey(left) === pathKey(right);
function resolvePath(value, cwd) {
  if (typeof value !== 'string' || !value || value.length > 2048 || /[\x00-\x1f]/.test(value)) return undefined;
  return normalize((/^[A-Za-z]:[\\/]/.test(cwd) ? win32 : posix).resolve(cwd, value));
}
function runPath(value, cwd, project) {
  const resolved = resolvePath(value, cwd);
  if (!resolved || !pathKey(resolved).startsWith(`${pathKey(project)}/`)) return undefined;
  const match = /^(runs|work)\/([^/]+)(?:\/|$)/.exec(resolved.slice(normalize(project).length + 1));
  return match ? { path: resolved, root: normalize(project) + '/' + match[1] + '/' + match[2] } : undefined;
}
/** Accept a single literal CLI invocation, never shell programs or inline Python. */
function commandWords(command) {
  if (typeof command !== 'string' || command.length > MAX_ARGUMENT_BYTES || /[\r\n]/.test(command)) return undefined;
  const words = [];
  let word = '', quote = '', started = false;
  for (const character of command) {
    if (quote) {
      if (character === quote) { quote = ''; continue; }
      if (quote === '"' && /[$`]/.test(character)) return undefined;
      word += character;
    } else if (character === '"' || character === "'") { quote = character; started = true; }
    else if (/\s/.test(character)) {
      if (started) { words.push(word); word = ''; started = false; }
    } else {
      if (/[;&|<>$`(){}]/.test(character)) return undefined;
      word += character; started = true;
    }
  }
  if (quote) return undefined;
  if (started) words.push(word);
  return words;
}
function parseCli(args, cwd, project) {
  const words = commandWords(args.command);
  if (!words || words.length < 4) return undefined;
  if (!/^python(?:3(?:\.\d+)?)?(?:\.exe)?$/i.test(words[0].replace(/\\/g, '/').split('/').at(-1))) return undefined;
  if (!samePath(resolvePath(words[1], cwd), `${normalize(project)}/bin/it.py`)) return undefined;
  const command = words[2], stage = CAPTURE_STAGES[command];
  if (!stage) return undefined;
  const paths = [], positional = [];
  let output;
  for (let index = 3; index < words.length; index++) {
    const word = words[index];
    if (!word.startsWith('--')) { positional.push(word); continue; }
    if (!['--output', '--run', '--clean-background', '--background', '--targets', '--target'].includes(word) || index + 1 === words.length) return undefined;
    const value = words[++index];
    if (value.startsWith('--')) return undefined;
    if (['--output', '--run'].includes(word)) output = value;
    else if (['--clean-background', '--background'].includes(word)) paths.push(value);
  }
  if (positional.length !== 1 || paths.length > 4) return undefined;
  const input = runPath(positional[0], cwd, project), destination = output ? runPath(output, cwd, project) : undefined;
  const primary = destination || input;
  if (!primary || (output && !destination)) return undefined;
  const inputs = [input, ...paths.map((path) => runPath(path, cwd, project))].filter(Boolean);
  const outputs = destination ? [destination.path] : command === 'render' && input ? [input.path] : [];
  return { run_root: primary.root, stage, command_kind: command, input_paths: inputs.map((entry) => entry.path), output_paths: outputs, input_run_roots: [...new Set(inputs.map((entry) => entry.root))] };
}
function parsedArguments(raw) {
  if (typeof raw !== 'string') return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : undefined;
  if (raw.length > MAX_ARGUMENT_BYTES) return undefined;
  try { return JSON.parse(raw); } catch { return undefined; }
}
/** No text, prompts, raw commands, arbitrary file names, or unbound generated images leave this parser. */
export function parseWorkflowTool(name, rawArgs, { cwd, project = PROJECT } = {}) {
  if (!cwd || !samePath(cwd, project)) return undefined;
  const args = parsedArguments(rawArgs), key = toolKey(name);
  if (!args) return undefined;
  if (key === 'ops_bash') {
    const effective = args.workdir ? resolvePath(args.workdir, cwd) : cwd;
    if (!effective || (!samePath(effective, project) && !pathKey(effective).startsWith(`${pathKey(project)}/`))) return undefined;
    const parsed = parseCli(args, effective, project);
    return parsed ? { ...parsed, tool_name: key, cwd: normalize(cwd), tool_cwd: effective } : undefined;
  }
  if (!FILE_TOOLS.has(key) || (['ops_write_file', 'ops_edit_file'].includes(key) && args.dryRun === true)) return undefined;
  const file = runPath(['ops_inspect_local_file', 'ops_write_file', 'ops_edit_file'].includes(key) ? args.path : args.file_path, cwd, project);
  if (!file) return undefined;
  const basename = file.path.split('/').at(-1), image = /\.(?:png|jpe?g|webp)$/i.test(basename);
  if (key === 'read_image' ? !image : !FILE_STAGES[basename]) return undefined;
  return { run_root: file.root, stage: FILE_STAGES[basename] || 'review.visual', command_kind: key === 'read_image' ? 'inspect-image' : `${key}-file`, input_paths: [file.path], output_paths: ['write', 'edit', 'ops_write_file', 'ops_edit_file'].includes(key) ? [file.path] : [], input_run_roots: [file.root], tool_name: key, cwd: normalize(cwd) };
}
const objectValue = (result) => result?.value && typeof result.value === 'object' ? result.value : undefined;
/** Only OPS status/handle/exit-code fields survive; tool completion never means visual acceptance. */
export function summarizeWorkflowResult(result) {
  if (result?.isError) return { phase: 'failed' };
  const value = objectValue(result);
  if (!value) return { phase: 'completed' };
  const jobId = value.jobId ?? value.job_id;
  if (safeId(jobId) && (['background', 'promoted'].includes(value.kind) || ['running', 'queued', 'pending'].includes(value.status))) return { phase: 'queued', job_id: jobId };
  const exitCode = value.exitCode ?? value.exit_code;
  if (['failed', 'error', 'rejected', 'aborted'].includes(value.status) || (typeof exitCode === 'number' && exitCode !== 0)) return { phase: 'failed', ...(typeof exitCode === 'number' ? { exit_code: exitCode } : {}) };
  return { phase: 'completed', ...(typeof exitCode === 'number' ? { exit_code: exitCode } : {}) };
}
function historicalResult(message) {
  if (!Array.isArray(message?.content)) return { isError: message?.isError === true };
  let text = '';
  for (const block of message.content) {
    if (block?.type !== 'text' || typeof block.text !== 'string') continue;
    if (text.length + block.text.length > MAX_ARGUMENT_BYTES) return { isError: message?.isError === true };
    text += block.text;
  }
  try { return { isError: message?.isError === true, value: JSON.parse(text) }; }
  catch { return { isError: message?.isError === true }; }
}
function originFor(header, headers) {
  let current = header;
  const seen = new Set();
  while (current?.origin === 'subagent') {
    if (!safeId(current.parentSession) || seen.has(current.id)) return undefined;
    seen.add(current.id); current = headers.get(current.parentSession);
  }
  return current?.id;
}

/** Metadata-only discovery: inspect bodies only after an original/effective project match. */
export async function projectSessionRecords(ctx, project = PROJECT, signal) {
  const records = await ctx.sessionQuery.listSessions(signal);
  const byId = new Map(records.map((record) => [record.header.id, record]));
  for (const session of ctx.sessions.list()) {
    if (!byId.has(session.id)) byId.set(session.id, { header: session.header, live: true, persisted: false });
  }
  return [...byId.values()].filter((record) => {
    if (samePath(record.header.cwd, project)) return true;
    const live = ctx.sessions.get(record.header.id);
    if (!live) return false;
    try { return samePath(ctx.workingDirectory.get(live), project); } catch { return false; }
  });
}

const PRODUCER_COMMANDS = new Set(['init', 'init-job', 'prepare-job', 'prepare-layered', 'render', 'render-job', 'render-layered', 'compare', 'benchmark']);
const WRITE_TOOLS = new Set(['write', 'edit', 'ops_write_file', 'ops_edit_file']);
const HIGHLIGHT_HEADING = /^(?:#{1,6}\s+|\*\*|【)?(识别结果|原文识别|识别与分析|译文|翻译结果|翻译|定稿|审核结论|审阅结论|检查结论|背景审阅|最终结果|制作结果|完成结果|核心产出|结果|Recognition(?: results?)?|Translations?|Review(?: findings| results?)?|Final (?:results?|outputs?)|Results?|Findings)(?:\*\*|】)?(?:\s*[:：]\s*|\s*$)/i;
const BROADCAST = /^(?:[-*]\s*)?(?:我(?:会|将|准备|正在|先|接下来)|接下来|下一步|准备|正在|稍后|先(?:检查|查看|读取|运行)|检查一下|I\s+(?:will|shall|am going to|am checking|am preparing)|I'll|Next[, :]|Let me)/i;
const SECRET_TEXT = /-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|(?:api[_ -]?key|password|secret|access[_ -]?token|密钥|令牌|密码)\s*[:=：]\s*\S+/i;
function safeHighlightParagraph(text) {
  if (!text.trim() || BROADCAST.test(text.trim()) || /```|~~~|(?:^|\n)\s*(?:\{|\[)\s*["{\[]|\b(?:ops_bash|exec_command|powershell|curl)\b|\b(?:python\S*\s+\S*it\.py|node\s+-e|npm\s+(?:run|install))\b/i.test(text)) return false;
  const lines = text.split('\n');
  if ((text.match(/(?:[A-Za-z]:[\\/]|\b(?:runs|work)\/)[^\s`"'<>()[\]{}，。；：;,]+/g) || []).length > 1) return false;
  if (lines.some(line => BROADCAST.test(line.trim()))) return false;
  if (lines.filter(line => /(?:[A-Za-z]:[\\/]|(?:^|[\s`(])(?:runs|work)\/)/.test(line)).length > 1) return false;
  return !lines.every(line => /^\s*(?:[-*]\s*)?(?:\[[^\]]+\]\([^)]*\)|`?[^\s]+\.(?:png|jpg|json|py|md)`?)\s*$/.test(line));
}
/** Bounded verbatim sections, never reasoning, stream chunks, commands, or model summarization. */
export function selectWorkflowHighlights(message) {
  if (message?.role !== 'assistant' || !Array.isArray(message.content)) return [];
  let text = '';
  for (const block of message.content) {
    if (block?.type !== 'text' || typeof block.text !== 'string') continue;
    if (text.length + block.text.length > 32000) return [];
    text += (text ? '\n\n' : '') + block.text;
  }
  if (SECRET_TEXT.test(text)) return [];
  const sections = []; let section;
  for (const line of text.split('\n')) {
    const heading = HIGHLIGHT_HEADING.exec(line.trim());
    if (heading) { section = { heading: heading[1], lines: [line] }; sections.push(section); }
    else if (/^\s*(?:#{1,6}\s+|\*\*[^*]+\*\*\s*$|【[^】]+】\s*$)/.test(line)) section = undefined;
    else if (section) section.lines.push(line);
  }
  const selected = [];
  for (const section of sections) {
    const original = section.lines.join('\n').trim();
    const split = original.indexOf('\n'), first = split < 0 ? original : original.slice(0, split);
    const inline = first.replace(HIGHLIGHT_HEADING, '').trim();
    const body = [inline, split < 0 ? '' : original.slice(split + 1).trim()].filter(Boolean).join('\n');
    if (!body || !safeHighlightParagraph(body)) continue;
    let excerpt = original;
    if (excerpt.length > 1200) {
      const boundaries = [...original.matchAll(/\n\s*\n/g)].map(match => match.index).filter(index => index <= 1200);
      if (!boundaries.length) continue;
      excerpt = original.slice(0, boundaries.at(-1)).trimEnd();
      const firstBreak = excerpt.indexOf('\n');
      const excerptBody = firstBreak < 0 ? excerpt.replace(HIGHLIGHT_HEADING, '').trim() : excerpt.slice(firstBreak + 1).trim();
      if (!excerptBody) continue;
    }
    selected.push({ text: excerpt, heading: section.heading });
    if (selected.length === 3) break;
  }
  return selected;
}
function explicitHighlightRoots(text, project, known) {
  const references = new Set(), normalized = normalize(text), base = normalize(project);
  const pattern = /\b(runs|work)\/([^/\s`"'<>()[\]{}，。；：!?;,]+)/g;
  for (const match of normalized.matchAll(pattern)) {
    const before = normalized.slice(0, match.index);
    if (before.endsWith('/') && !before.toLowerCase().endsWith((base + '/').toLowerCase())) return { invalid: true, references };
    const relative = match[1] + '/' + match[2].replace(/\.$/, '');
    const key = pathKey(base + '/' + relative);
    if (!known.has(key)) return { invalid: true, references };
    references.add(key);
  }
  return { invalid: false, references };
}
function highlightContext(operations, turn, text, heading, project, explicitOnly = false) {
  const roots = new Set(operations.map(item => pathKey(item.run_root)));
  const { invalid, references } = explicitHighlightRoots(text, project, roots);
  if (invalid || references.size > 1 || (explicitOnly && !references.size)) return undefined;
  let candidates = references.size ? operations.filter(item => references.has(pathKey(item.run_root))) : operations.filter(item => item.turn === turn);
  if (!candidates.length) return undefined;
  const unique = new Map(candidates.map(item => [pathKey(item.run_root) + ':' + item.stage, item]));
  if (unique.size === 1) return unique.values().next().value;
  if (!references.size) return undefined;
  const stages = /背景审阅/.test(heading) ? ['background.review'] : /审核|审阅|检查|review|findings/i.test(heading) ? ['review.visual', 'background.review'] : /翻译|译文|定稿|translat/i.test(heading) ? ['semantic.translate', 'semantic.analyze'] : /识别|recognition/i.test(heading) ? ['semantic.analyze'] : ['basic.render', 'composite.render', 'job.render'];
  candidates = [...unique.values()].filter(item => stages.includes(item.stage));
  return candidates.length === 1 ? candidates[0] : undefined;
}

const ACTIVITY_HEADING = /^(?:#{1,6}\s+|\*\*|【)([^\n*】]{2,100})(?:\*\*|】)?\s*[:：]?\s*$/;
const ACTIVITY_INLINE = /^(?:当前阶段|当前步骤|阶段|步骤|进度|计划|任务计划|Current (?:stage|step)|Plan)\s*[:：]\s*(.{2,180})$/i;
function activityText(text, limit = 600) {
  return typeof text === 'string' && text.trim().length <= limit && text.trim() && !SECRET_TEXT.test(text) && !/https?:\/\/|file:\/\/|```|~~~|[\x00-\x08]|\b(?:ops_bash|exec_command|powershell|curl)\b|(?:[A-Za-z]:[\\/]|\b(?:runs|work)\/)|\[.{0,100}\]\(|(?:^|\n)\s*[\[{]/.test(text) ? text.trim() : undefined;
}
/** Conversation display only: these records never establish workflow ownership. */
export function selectSessionActivities(message) {
  if (message?.role !== 'assistant' || !Array.isArray(message.content)) return [];
  const blocks = message.content.filter(block => block.type === 'text' && typeof block.text === 'string');
  if (blocks.reduce((size, block) => size + block.text.length, 0) > 32000) return [];
  const text = blocks.map(block => block.text).join('\n\n');
  if (SECRET_TEXT.test(text)) return [];
  const output = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim(), match = ACTIVITY_INLINE.exec(trimmed) || ACTIVITY_HEADING.exec(trimmed);
    const title = match && activityText(match[1].replace(/\*\*$/, '').trim(), 180);
    if (!title || /^(?:参考|链接|文件|调试|references?|links?|files?|debug)$/i.test(title)) continue;
    output.push({ type: 'stage', stage: title, text: title, status: /计划|plan/i.test(trimmed) ? 'planned' : 'observed' });
    if (output.length === 2) break;
  }
  for (const section of selectWorkflowHighlights(message)) {
    const value = activityText(section.text);
    if (value) output.push({ type: 'result', text: value, status: 'observed' });
    if (output.length >= 3) break;
  }
  return output.slice(0, 3);
}
function todoItems(args) {
  if (!Array.isArray(args?.todos) || args.todos.length > 20) return undefined;
  const items = args.todos.map(item => ({ content: activityText(item?.content, 300), status: item?.status }));
  return items.every(item => item.content && ['pending', 'in_progress', 'completed'].includes(item.status)) ? items : undefined;
}

/** Subscribe before backfill; return an idempotent disposer with ready/flush/status helpers. */
export function installWorkflowCapture(ctx, backend, options = {}) {
  const project = normalize(options.project || PROJECT), projectId = options.projectId || PROJECT_ID;
  const maxPending = options.maxPending || 1024, batchSize = options.batchSize || 32;
  const maxBatchBytes = options.maxBatchBytes || 96000, maxBatchSessions = Math.min(options.maxBatchSessions || 8, 100);
  const maxSessions = options.maxSessions || 256, maxCalls = options.maxCalls || 512, maxEvents = options.maxEvents || 2048;
  const now = options.now || Date.now, schedule = options.setTimeout || setTimeout, cancel = options.clearTimeout || clearTimeout;
  const abort = new AbortController(), headers = new Map(), calls = new Map(), jobs = new Map(), cursors = new Map(), pending = new Map();
  const executorStates = new Map(), statusPending = new Map(), producers = new Map(), summarySeen = new Map(), activeTurns = new Map(), foreignTurns = new Map();
  const activitySeen = new Map(), todoCalls = new Map();
  function activityOwner(id) { return sessionRecord(id)?.session_id === id && headers.get(id)?.origin !== 'subagent'; }
  function sessionActivity(id, event, activity, suffix = '') {
    if (!activityOwner(id)) return true;
    const eventId = 'activity:' + createHash('sha256').update(JSON.stringify([id, event?.seq ?? suffix, activity.type, suffix])).digest('hex');
    if (activitySeen.has(eventId)) return true;
    const value = { ...activity, event_id: eventId, executor_session_id: id, time: timestamp(event?.time), ...(Number.isSafeInteger(event?.seq) ? { seq: event.seq } : {}) };
    if (!enqueue(id, { ...value, phase: 'activity' })) return false;
    activitySeen.set(eventId, true);
    while (activitySeen.size > maxEvents * 3) activitySeen.delete(activitySeen.keys().next().value);
    return true;
  }
  function observeTodo(id, data) {
    if (toolKey(data.name) !== 'todo_write' || !activityOwner(id)) return;
    const items = todoItems(parsedArguments(data.arguments));
    if (!items || !safeId(data.callId)) return;
    todoCalls.set(keyFor(id, data.callId), { items });
    while (todoCalls.size > maxCalls) todoCalls.delete(todoCalls.keys().next().value);
  }
  function finishTodo(id, callId, outcome, event) {
    const call = todoCalls.get(keyFor(id, callId));
    if (!call || outcome?.isError) return true;
    const value = objectValue(outcome);
    if (value && ['failed', 'error', 'rejected', 'aborted'].includes(value.status)) return true;
    return sessionActivity(id, event, { type: 'plan', text: '会话任务计划', status: 'observed', items: call.items }, callId);
  }
  function assistantActivity(id, event) {
    if (!activityOwner(id) || !Number.isSafeInteger(event.seq) || foreignTurns.has(keyFor(id, event.data?.turn))) return true;
    for (const [index, item] of selectSessionActivities(event.data.message).entries()) if (!sessionActivity(id, event, item, String(index))) return false;
    return true;
  }
  function observeLocation(id, name, args, cwd, turn) {
    if (!Number.isSafeInteger(turn)) return;
    let effective = cwd;
    if (toolKey(name) === 'ops_bash' && typeof args?.workdir === 'string' && args.workdir) effective = resolvePath(args.workdir, cwd);
    if (toolKey(name) === 'working_directory' && typeof args?.cd === 'string' && args.cd) effective = resolvePath(args.cd, cwd);
    if (!effective || (samePath(effective, project) || pathKey(effective).startsWith(pathKey(project) + '/'))) return;
    foreignTurns.set(keyFor(id, turn), true);
    while (foreignTurns.size > maxCalls) foreignTurns.delete(foreignTurns.keys().next().value);
  }
  function recordProducer(id, call, phase, extra) {
    if (phase !== 'completed' || !sessionRecord(id)) return;
    if (!(call.parsed.tool_name === 'ops_bash' ? PRODUCER_COMMANDS.has(call.parsed.command_kind) : WRITE_TOOLS.has(call.parsed.tool_name))) return;
    const key = keyFor(id, call.callId), previous = producers.get(key);
    const turn = Number.isSafeInteger(extra.turn) ? extra.turn : previous?.turn ?? call.turn;
    if (!Number.isSafeInteger(turn) || turn < 0) return;
    producers.delete(key); producers.set(key, { executor: id, turn, run_root: call.parsed.run_root, stage: call.parsed.stage, seq: Number.isSafeInteger(extra.seq) ? extra.seq : previous?.seq, time: extra.time ?? previous?.time ?? now() });
    while (producers.size > maxCalls) producers.delete(producers.keys().next().value);
  }
  function assistantHighlights(id, event) {
    const data = event.data;
    if (!sessionRecord(id) || !Number.isSafeInteger(event.seq) || !Number.isSafeInteger(data?.turn)) return true;
    const sections = selectWorkflowHighlights(data.message);
    if (!sections.length) return true;
    const text = data.message.content.filter(block => block.type === 'text' && typeof block.text === 'string').map(block => block.text).join('\n\n');
    const operations = [...producers.values()].filter(item => item.executor === id && (Number.isSafeInteger(item.seq) ? item.seq < event.seq : Number.isFinite(item.time) && Number.isFinite(event.time) && item.time <= event.time));
    const implicitDisabled = foreignTurns.has(keyFor(id, data.turn));
    const excerpts = sections.flatMap(section => implicitDisabled ? section.text.split(/\n\s*\n/).filter(Boolean).map(text => ({ ...section, text })) : [section]);
    let matched = 0;
    for (const [index, section] of excerpts.entries()) {
      const operation = highlightContext(operations, data.turn, implicitDisabled ? section.text : text, section.heading, project, implicitDisabled);
      if (!operation) continue;
      if (++matched > 3) break;
      const eventId = 'summary:' + createHash('sha256').update(JSON.stringify([id, event.seq, index, operation.run_root, operation.stage])).digest('hex');
      if (summarySeen.has(eventId)) continue;
      if (!enqueue(id, { event_id: eventId, phase: 'summary', run_root: operation.run_root, stage: operation.stage, text: section.text, executor_session_id: id, time: timestamp(event.time) })) return false;
      summarySeen.set(eventId, true);
      while (summarySeen.size > maxEvents * 3) summarySeen.delete(summarySeen.keys().next().value);
    }
    return true;
  }
  let statusRevision = 0, historyCandidates = 0;
  let disposed = false, timer, flushing, scanning, retryDelay = options.retryMs || 1000, lastError, lastIngestReason;
  const keyFor = (id, callId) => `${id}:${callId}`;
  function belongs(header) {
    if (!header || !safeId(header.id)) return false;
    if (samePath(header.cwd, project)) return true;
    const live = ctx.sessions.get(header.id);
    if (!live) return false;
    try { return samePath(ctx.workingDirectory.get(live), project); } catch { return false; }
  }
  const timestamp = (value = now()) => new Date(value).toISOString();
  function remember(header) {
    if (!belongs(header)) return false;
    if (!headers.has(header.id) && headers.size >= maxSessions) { lastError = 'session-limit'; return false; }
    headers.set(header.id, header); return true;
  }
  function originStatus(origin) {
    let known = false;
    for (const [executor, state] of executorStates) {
      if (originFor(headers.get(executor), headers) !== origin) continue;
      known = true;
      if (state.running) return 'running';
    }
    return known ? 'idle' : undefined;
  }
  function sessionRecord(executor) {
    const header = headers.get(executor), origin = originFor(header, headers);
    if (!origin || !belongs(header) || !belongs(headers.get(origin))) return undefined;
    const status = originStatus(origin);
    return { session_id: origin, origin_session_id: origin, executor_session_id: executor, ...(header.parentSession ? { parent_session_id: header.parentSession } : {}), cwd: project, ...(status ? { status } : {}) };
  }
  function updateStatus(id, running) {
    if (disposed || !safeId(id) || typeof running !== 'boolean') return;
    if (!headers.has(id)) {
      const session = ctx.sessions.get(id);
      if (!session || !remember(session.header)) return;
    }
    if (executorStates.get(id)?.running === running) return;
    const origin = originFor(headers.get(id), headers), before = origin ? originStatus(origin) : undefined;
    executorStates.set(id, { running, revision: ++statusRevision });
    const status = origin ? originStatus(origin) : undefined;
    if (origin && status && status !== before) {
      statusPending.set(origin, { revision: statusRevision, status });
      if (activityOwner(origin)) sessionActivity(origin, null, { type: 'status', text: status === 'running' ? '会话正在执行' : '会话已空闲', status }, `status:${statusRevision}:${now()}`);
      arm();
    }
  }
  async function refreshStatuses() {
    const controller = ctx.get?.('sessionController');
    if (!controller) return;
    const beforeRead = statusRevision;
    try {
      const result = await controller.list({}, abort.signal);
      if (disposed || !Array.isArray(result?.items)) return;
      for (const summary of result.items) {
        if (!headers.has(summary.sessionId) || !belongs(headers.get(summary.sessionId))) continue;
        if ((executorStates.get(summary.sessionId)?.revision ?? -1) > beforeRead) continue;
        updateStatus(summary.sessionId, summary.running);
      }
    } catch { if (!disposed) lastError = 'capture-status-unavailable'; }
  }
  function arm(delay = 20) {
    if (disposed || timer !== undefined) return;
    timer = schedule(() => { timer = undefined; void flush().catch(() => { lastError = 'capture-source-unavailable'; arm(retryDelay); }); }, delay);
    timer?.unref?.();
  }
  function enqueue(id, event) {
    if (disposed || !headers.has(id)) return false;
    const key = `${id}:${event.event_id}`;
    if (!pending.has(key) && pending.size >= maxPending) { arm(); return false; }
    pending.set(key, { executor: id, event }); arm(); return true;
  }
  function emitCall(id, call, phase, extra = {}) {
    const accepted = enqueue(id, { ...call.parsed, event_id: `${call.callId}:${phase}:${extra.seq ?? extra.source ?? 'runtime'}`, call_id: call.callId, ...(call.rootCallId ? { root_call_id: call.rootCallId } : {}), phase, ...extra, time: timestamp(extra.time) });
    if (accepted) recordProducer(id, call, phase, extra);
    return accepted;
  }
  function storeCall(id, call) {
    const key = keyFor(id, call.callId);
    if (!calls.has(key) && calls.size >= maxCalls) calls.delete(calls.keys().next().value);
    calls.set(key, call);
  }
  function callFrom(id, callId, name, args, cwd = project, rootCallId, turn = activeTurns.get(id)) {
    observeLocation(id, name, parsedArguments(args), cwd, turn);
    if (!safeId(callId)) return undefined;
    const parsed = parseWorkflowTool(name, args, { cwd, project });
    if (!parsed) return undefined;
    const call = { callId, rootCallId, parsed, turn: calls.get(keyFor(id, callId))?.turn }; storeCall(id, call); return call;
  }
  function finishJob(id, jobId, outcome, extra) {
    const call = jobs.get(keyFor(id, jobId)), value = objectValue(outcome);
    if (!call) return true;
    const terminal = outcome?.isError || ['completed', 'finished', 'failed', 'error', 'killed', 'cancelled'].includes(value?.status) || typeof (value?.exitCode ?? value?.exit_code) === 'number';
    if (!terminal) return true;
    const summary = summarizeWorkflowResult(outcome), accepted = emitCall(id, call, summary.phase, { ...extra, job_id: jobId, ...summary });
    if (accepted) jobs.delete(keyFor(id, jobId));
    return accepted;
  }
  function resultFor(id, callId, outcome, extra) {
    const call = calls.get(keyFor(id, callId));
    if (!call) return true;
    if (call.jobId) return finishJob(id, call.jobId, outcome, extra);
    const summary = summarizeWorkflowResult(outcome);
    if (['ops_write_file', 'ops_edit_file'].includes(call.parsed.tool_name) && summary.phase === 'completed' && objectValue(outcome)?.status !== 'completed') summary.phase = 'observed';
    if (call.parsed.tool_name === 'ops_bash' && !outcome?.isError) {
      const value = objectValue(outcome);
      const exitCode = value?.exitCode ?? value?.exit_code;
      if (!summary.job_id && typeof exitCode !== 'number' && !['completed', 'failed', 'error'].includes(value?.status)) summary.phase = 'observed';
      if (exitCode === 2 && ['quality', 'check-layered', 'check-job'].includes(call.parsed.command_kind)) summary.phase = 'completed';
    }
    if (summary.job_id) {
      if (jobs.size >= maxCalls) jobs.delete(jobs.keys().next().value);
      jobs.set(keyFor(id, summary.job_id), call);
    }
    return emitCall(id, call, summary.phase, { ...extra, ...summary });
  }
  function observeCall(id, data, historical = false) {
    if (toolKey(data.name) === 'job_output') {
      const args = parsedArguments(data.arguments), original = jobs.get(keyFor(id, args?.job_id));
      if (original && safeId(data.callId)) storeCall(id, { ...original, callId: data.callId, jobId: args.job_id });
      return undefined;
    }
    const args = parsedArguments(data.arguments);
    observeLocation(id, data.name, args, project, data.turn);
    if (historical && !samePath(headers.get(id)?.cwd, project)) {
      const explicit = toolKey(data.name) === 'ops_bash' ? args?.workdir : (args?.file_path ?? args?.path);
      if (typeof explicit !== 'string' || !/^(?:[A-Za-z]:[\\/]|\/)/.test(explicit)) return undefined;
      if (toolKey(data.name) === 'ops_bash' && !samePath(resolvePath(explicit, project), project)) return undefined;
    }
    let cwd = project;
    const live = historical ? undefined : ctx.sessions.get(id);
    if (live) cwd = ctx.workingDirectory.get(live);
    const call = callFrom(id, data.callId, data.name, args, cwd, undefined, data.turn);
    if (call && Number.isSafeInteger(data.turn)) call.turn = data.turn;
    return call;
  }
  function durable(session, event, inheritedCount = 0, historical = false) {
    if (!remember(session.header) || !event || event.seq < inheritedCount) return true;
    const id = session.id || session.header.id, data = event.data;
    if (Number.isSafeInteger(data?.turn)) activeTurns.set(id, data.turn);
    if (event.type === 'tool/call') {
      observeTodo(id, data);
      const call = observeCall(id, data, historical);
      return !call || emitCall(id, call, 'observed', { seq: event.seq, time: event.time, turn: data.turn, step: data.step, source: 'session' });
    }
    if (event.type === 'tool/result') {
      const outcome = historicalResult(data.message);
      if (!finishTodo(id, data.message?.toolCallId, outcome, event)) return false;
      return resultFor(id, data.message?.toolCallId, outcome, { seq: event.seq, time: event.time, turn: data.turn, step: data.step, source: 'session' });
    }
    if (event.type === 'turn/start' || event.type === 'turn/end') {
      const reason = data?.reason?.kind, running = event.type === 'turn/start';
      const status = running ? 'running' : ['blocked', 'error', 'interrupted', 'aborted'].includes(reason) ? reason : 'idle';
      const text = running ? '会话开始执行' : ({ blocked: '会话遇到阻塞', error: '会话执行出错', interrupted: '会话已中断', aborted: '会话已停止' })[status] || '本轮会话已结束';
      return sessionActivity(id, event, { type: 'status', text, status });
    }
    if (event.type === 'assistant/message') return assistantActivity(id, event) && assistantHighlights(id, event);
    return true;
  }
  function liveSession(exec) {
    if (!safeId(exec.agent?.id)) return undefined;
    const session = ctx.sessions.get(exec.agent.id);
    return session && remember(session.header) ? session : undefined;
  }
  function execute(exec, next) {
    try {
      const session = liveSession(exec);
      if (session) {
        const call = callFrom(session.id, exec.callId, exec.name, exec.arguments, ctx.workingDirectory.get(session), exec.rootCallId);
        if (call) emitCall(session.id, call, 'started', { source: 'runtime' });
      }
    } catch { lastError = 'capture-source-unavailable'; }
    // No I/O, awaiting observers, cancellation changes, or result transformations.
    return next();
  }
  function result(exec, outcome) {
    try {
      const session = liveSession(exec);
      if (!session) return;
      if (toolKey(exec.name) === 'job_output') { finishJob(session.id, exec.arguments?.job_id, outcome, { source: 'job-output' }); return; }
      if (!calls.has(keyFor(session.id, exec.callId))) callFrom(session.id, exec.callId, exec.name, exec.arguments, ctx.workingDirectory.get(session), exec.rootCallId);
      resultFor(session.id, exec.callId, outcome, { source: 'runtime' });
    } catch { lastError = 'capture-source-unavailable'; }
  }
  async function backfill() {
    if (disposed || scanning) return scanning;
    scanning = (async () => {
      const records = await projectSessionRecords(ctx, project, abort.signal);
      if (disposed) return;
      historyCandidates = records.length;
      for (const record of records) remember(record.header);
      await refreshStatuses();
      let budget = maxEvents;
      for (const record of records) {
        if (disposed || pending.size >= maxPending || budget <= 0) break;
        if (!headers.has(record.header.id) || record.formatStatus === 'migration-required') continue;
        let snapshot;
        try { snapshot = await ctx.sessionQuery.readSession(record.header.id); } catch { lastError = 'capture-source-unavailable'; continue; }
        if (disposed || !belongs(snapshot.session)) continue;
        const id = snapshot.session.id, session = { id, header: snapshot.session }, after = cursors.get(id) ?? -1;
        for (const event of snapshot.events) {
          if (disposed) return;
          if (event.seq <= after || event.seq < snapshot.inheritedEventCount) continue;
          if (budget-- <= 0 || !durable(session, event, snapshot.inheritedEventCount, true)) break;
          cursors.set(id, event.seq);
        }
      }
    })().finally(() => { scanning = undefined; });
    return scanning;
  }
  async function flush() {
    if (disposed || flushing) return flushing;
    flushing = (async () => {
      await backfill();
      if (disposed || (!pending.size && !statusPending.size)) { arm(options.rescanMs || 15000); return; }
      const batch = [], byExecutor = new Map(), statusBatch = [];
      const encodeBatch = () => Buffer.from(JSON.stringify({ project_id: projectId, sessions: [...byExecutor.values()] }));
      for (const [origin, value] of statusPending) {
        if (statusBatch.length >= batchSize || byExecutor.size >= maxBatchSessions) break;
        const metadata = sessionRecord(origin);
        if (!metadata) continue;
        byExecutor.set(origin, { ...metadata, events: [] });
        if (encodeBatch().length > maxBatchBytes) { byExecutor.delete(origin); break; }
        statusBatch.push([origin, value]);
      }
      for (const [key, value] of pending) {
        const metadata = sessionRecord(value.executor);
        if (!metadata) continue; // Missing/out-of-project ancestry stays unassigned.
        const previous = byExecutor.get(value.executor);
        if (batch.length >= batchSize || (!previous && byExecutor.size >= maxBatchSessions)) break;
        const row = { ...(previous || metadata), events: [...(previous?.events || [])] };
        if (value.event.phase === 'activity' && (row.activities?.length || 0) >= 20) break;
        if (value.event.phase === 'activity') {
          const { phase, ...activity } = value.event;
          row.activities = [...(row.activities || []), activity];
        } else if (value.event.phase === 'summary') {
          const { phase, ...highlight } = value.event;
          row.highlights = [...(row.highlights || []), highlight];
        } else row.events.push(value.event);
        byExecutor.set(value.executor, row);
        if (encodeBatch().length > maxBatchBytes) {
          if (previous) byExecutor.set(value.executor, previous); else byExecutor.delete(value.executor);
          if (!byExecutor.size) lastError = 'ingest-record-too-large';
          break;
        }
        batch.push([key, value]);
      }
      let response;
      try {
        if (!byExecutor.size) { if (lastError !== 'ingest-record-too-large') lastError = 'unresolved-session-lineage'; arm(options.rescanMs || 15000); return; }
        response = await backend.request('/api/workflow/ingest', { method: 'POST', body: encodeBatch(), signal: abort.signal });
        if (disposed) return;
        if (!response || response.status < 200 || response.status >= 300) throw new Error('ingest-rejected');
        for (const [key, value] of batch) if (pending.get(key) === value) pending.delete(key);
        for (const [origin, value] of statusBatch) if (byExecutor.has(origin) && statusPending.get(origin) === value) statusPending.delete(origin);
        retryDelay = options.retryMs || 1000;
        if (lastError === 'ingest-unavailable' || lastError === 'ingest-record-too-large') lastError = undefined;
        lastIngestReason = undefined;
      } catch (failure) {
        if (!disposed) { lastError = 'ingest-unavailable'; lastIngestReason = ingestFailureReason(response, failure); arm(retryDelay); retryDelay = Math.min(retryDelay * 2, 30000); }
        return;
      }
      arm(pending.size || statusPending.size ? 20 : (options.rescanMs || 15000));
    })().finally(() => { flushing = undefined; });
    return flushing;
  }
  const removers = [ctx.on('tools/execute', execute), ctx.on('tools/result', result), ctx.on('session/event', (session, event) => {
    try {
      durable(session, event, session.inheritedEventCount || 0);
      if (belongs(session.header) && event.seq >= (session.inheritedEventCount || 0)) {
        if (event.type === 'turn/start') updateStatus(session.id, true);
        if (event.type === 'turn/end') updateStatus(session.id, false);
      }
    } catch { lastError = 'capture-source-unavailable'; }
  }), ctx.on('api-session/status', (sessionId, running) => {
    try { updateStatus(sessionId, running); } catch { lastError = 'capture-status-unavailable'; }
  }), ctx.on('agent/status', ({ agent, status }) => {
    try {
      if (status === 'running' || status === 'idle') updateStatus(agent?.id, status === 'running');
    } catch { lastError = 'capture-status-unavailable'; }
  })];
  const ready = backfill().catch(() => { lastError = 'capture-source-unavailable'; }).finally(() => arm());
  function dispose() {
    if (disposed) return;
    disposed = true; abort.abort(); if (timer !== undefined) cancel(timer);
    for (const remove of removers) remove?.();
    pending.clear(); activitySeen.clear(); todoCalls.clear(); calls.clear(); jobs.clear(); headers.clear(); cursors.clear(); executorStates.clear(); statusPending.clear(); producers.clear(); summarySeen.clear(); activeTurns.clear(); foreignTurns.clear();
  }
  dispose.ready = ready;
  dispose.flush = flush;
  dispose.getKnownSessions = () => [...headers.keys()].map(sessionRecord).filter(Boolean);
  dispose.status = () => ({ pending: pending.size, status_pending: statusPending.size, known_sessions: headers.size, history_candidates: historyCandidates, recorded_calls: calls.size, disposed, ...(lastError ? { error: lastError } : {}), ...(lastIngestReason ? { error_reason: lastIngestReason } : {}) });
  return dispose;
}
