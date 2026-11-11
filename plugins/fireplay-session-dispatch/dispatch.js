import {createHash, randomUUID} from 'node:crypto';
import {mkdir, open, readFile, realpath, rename} from 'node:fs/promises';
import {dirname, isAbsolute, relative, resolve, sep} from 'node:path';

export const PROJECT = 'C:/Users/Echo/Desktop/单图片翻译';
export const MANIFEST = 'work/fireplay-production/intake-index-20261103.json';
export const MANIFEST_SHA256 = 'cd1c3e5d62be3a59bb158b714162584c3ad67d9b8781fa264236ca501ce88c6b';
const digest = value => createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(value);
const samePath = (a, b) => resolve(a).toLowerCase() === resolve(b).toLowerCase();
const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const COLD_INTAKE = 'fixed37-code-v1';
const COLD_INSTRUCTIONS = [
  '先读 work/fireplay-production/unified-control.md，再读取本需求 latest exact brief 正文、real sources 与最新 exact UUID delivery；structured fields 空不等于正文为空。',
  '标题只用 manifest code（需求编号，code only）；本需求原普通会话自行负责解析、制作、真实审核、上传、入库、共享、exact UUID 绑定和读回，父只控不生产。',
  '已有 delivery、materialId、resourceId 先核实再复用，保留所有已交付成员；unknown 操作查原 journal，不换 key 重放。',
  '最新正文、真实来源和明确交付参照优先，不继承 handoff 的 old three（三张）或尺寸倍数推导；不明确的范围保留待核实，不猜补。',
].join(String.fromCharCode(10));

export function createDispatcher({controller, registry, project = PROJECT,
  manifest = MANIFEST, manifestSha256 = MANIFEST_SHA256, uuid = randomUUID,
  getStatus = () => null, beforeDispatch}) {
  let queue = Promise.resolve();
  const stateRoot = resolve(project, 'work/fireplay-production/session-dispatch');
  const serial = operation => {
    const result = queue.then(operation);
    queue = result.catch(() => {});
    return result;
  };
  async function inside(path) {
    const full = await realpath(resolve(project, path));
    const base = await realpath(project);
    const rel = relative(base, full);
    if (isAbsolute(rel) || rel === '..' || rel.startsWith(`..${sep}`))
      throw new Error('Input path leaves project');
    return full;
  }
  async function reviewedRow(id) {
    const bytes = await readFile(await inside(manifest));
    if (digest(bytes) !== manifestSha256) throw new Error('Reviewed intake manifest changed');
    const doc = JSON.parse(bytes);
    if (!Array.isArray(doc.records)) throw new Error('Invalid reviewed intake records');
    // The production route is pinned to the reviewed 37-row bytes; custom fixtures retain legacy semantics.
    if (manifestSha256 === MANIFEST_SHA256 && (doc.records.length !== 37 ||
      new Set(doc.records.map(row => row.id)).size !== 37 ||
      new Set(doc.records.map(row => row.code)).size !== 37 ||
      doc.records.some(row => !UUID.test(row.id) || !/^[A-Z]+-\d{8}-\d{3,4}$/.test(row.code) ||
        row.handoff?.replaceAll('\\', '/').replaceAll('//', '/') !== `work/fireplay-production/${row.code}/handoff.txt`)))
      throw new Error('Invalid fixed 37 requirement identities/code/handoff');
    const matches = doc.records.filter(row => row.id === id);
    if (matches.length !== 1) throw new Error('Requirement is not uniquely in reviewed intake');
    return matches[0];
  }
  async function intake(id, cold = false) {
    const row = await reviewedRow(id);
    if (!cold && (row.title !== row.session_title || !row.title?.trim())) {
      const error = new Error('平台标题为空或与 session_title 不一致，无法创建用户要求的精确标题会话');
      error.code = 'exact_title_unavailable';
      throw error;
    }
    const handoffPath = row.handoff.replaceAll('\\', '/');
    const handoffFull = await inside(handoffPath);
    if (cold && !samePath(handoffFull, resolve(project, handoffPath)))
      throw new Error('Cold intake handoff alias forbidden');
    const handoff = await readFile(handoffFull, 'utf8');
    if (!handoff.trim()) throw new Error('Empty handoff');
    if (cold) {
      const controlBytes = await readFile(await inside('work/fireplay-production/unified-control.md'));
      if (!controlBytes.toString('utf8').trim()) throw new Error('Empty unified control');
      return {requirement_id:row.id, code:row.code, title:row.code,
        prompt:[handoff, '先读 work/fireplay-production/execution-addendum.md；以下最新统一控制覆盖 handoff 旧分工与范围。',
          COLD_INSTRUCTIONS, controlBytes.toString('utf8')].join(String.fromCharCode(10,10)),
        handoff:row.handoff, manifest_sha256:manifestSha256, intake_version:COLD_INTAKE,
        control_sha256:digest(controlBytes)};
    }
    return {requirement_id:row.id, code:row.code, title:row.title,
      prompt:handoff + '\n\n先读 work/fireplay-production/execution-addendum.md；用户最新要求每需求会话端到端制作/MCP上传/绑定，覆盖handoff旧的父会话统一上传分工。',
      handoff:row.handoff, manifest_sha256:manifestSha256};
  }
  const statePath = id => resolve(stateRoot, `${id}.json`);
  async function load(id) {
    try { return JSON.parse(await readFile(statePath(id), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }
  async function save(state) {
    await mkdir(stateRoot, {recursive:true});
    const path = statePath(state.snapshot.requirement_id);
    const temporary = `${path}.${uuid()}.tmp`;
    const handle = await open(temporary, 'wx');
    try {
      await handle.writeFile(`${JSON.stringify(state, null, 2)}\n`);
      await handle.sync();
    } finally { await handle.close(); }
    await rename(temporary, path);
  }
  function summary(state) {
    return {requirement_id:state.snapshot.requirement_id, code:state.snapshot.code,
      title:state.snapshot.title, snapshot_sha256:state.snapshot_sha256,
      session_id:state.session_id, request_id:state.request_id, stage:state.stage,
      error:state.error, updated_at:state.updated_at};
  }
  async function transition(state, stage, error) {
    state.stage = stage;
    state.updated_at = new Date().toISOString();
    if (error) state.error = String(error.message || error);
    else delete state.error;
    await save(state);
  }
  async function checkedState(args) {
    const state = await load(args.requirement_id);
    await intake(args.requirement_id, state?.snapshot?.intake_version === COLD_INTAKE); // legacy snapshot inputs stay legacy
    if (!state || state.snapshot_sha256 !== args.snapshot_sha256 ||
      digest(canonical(state.snapshot)) !== state.snapshot_sha256)
      throw new Error('Preview snapshot hash mismatch');
    return state;
  }
  async function coldContextReview(state) {
    const folder = `work/fireplay-production/${state.snapshot.code}`;
    const review = JSON.parse(await readFile(await inside(`${folder}/cold-intake-context-review.json`), 'utf8'));
    if (review.reviewed_by !== 'parent' || !Number.isFinite(Date.parse(review.reviewed_at)) ||
      review.requirement_id !== state.snapshot.requirement_id || review.code !== state.snapshot.code ||
      review.snapshot_sha256 !== state.snapshot_sha256 || review.manifest_sha256 !== manifestSha256 ||
      !Array.isArray(review.evidence) || !review.evidence.some(item => item.kind === 'source') ||
      !review.evidence.some(item => item.kind === 'delivery'))
      throw new Error('Cold intake requires parent frozen source/delivery context review');
    for (const item of review.evidence) {
      if (!['source','delivery'].includes(item.kind) || typeof item.path !== 'string' ||
        !item.path.startsWith(`${folder}/`) || item.path.includes('..') ||
        !/^[0-9a-f]{64}$/.test(item.sha256))
        throw new Error('Cold intake context evidence changed or invalid');
      const full = await inside(item.path);
      const parentFolder = await inside(folder);
      if (!samePath(full, resolve(project, item.path)) || !samePath(parentFolder, resolve(project, folder)) ||
        ['handoff.txt','cold-intake-context-review.json'].includes(relative(parentFolder, full)))
        throw new Error('Cold intake evidence alias or self-reference forbidden');
      const bytes = await readFile(full);
      if (digest(bytes) !== item.sha256) throw new Error('Cold intake context evidence changed or invalid');
      const evidence = JSON.parse(bytes);
      if (evidence.requirement_id !== state.snapshot.requirement_id || evidence.code !== state.snapshot.code ||
        (item.kind === 'source' && !(typeof evidence.brief === 'string' && evidence.brief.trim()) &&
          !(Array.isArray(evidence.sources) && evidence.sources.length)) ||
        (item.kind === 'delivery' && (!Number.isFinite(Date.parse(evidence.queried_at)) ||
          !Array.isArray(evidence.materials) || !Array.isArray(evidence.resources))))
        throw new Error('Cold intake source/delivery exact identity or content missing');
    }
  }
  async function assertColdScope(state) {
    if (state.snapshot.intake_version !== COLD_INTAKE) return;
    const current = await intake(state.snapshot.requirement_id, true);
    if (digest(canonical(current)) !== state.snapshot_sha256)
      throw new Error('Cold intake scope stale; parent review required; frozen journal preserved');
  }
  async function projectWorkspace() {
    const rows = registry.list();
    const matches = rows.filter(row => samePath(row.path, project));
    if (matches.length !== 1) throw new Error('Existing project workspace not uniquely found');
    return matches[0].workspaceId ?? matches[0].id;
  }
  async function observed(state) {
    const observation = await controller.inspect(state.session_id);
    if (!observation || !samePath(observation.meta.cwd, project)) throw new Error('Session project mismatch');
    return observation;
  }
  const accepted = (events, requestId) => events.some(event =>
    event.type === 'user/message' && event.data?.source?.rpcId === requestId);

  async function execute(args) {
    if (!UUID.test(args.requirement_id)) throw new Error('Invalid requirement identity');
    if (Object.keys(args).some(key => !['action','requirement_id','snapshot_sha256'].includes(key)))
      throw new Error('Arbitrary session IDs or prompt arguments forbidden');
    if (args.action === 'preview') {
      let state = await load(args.requirement_id);
      const cold = state ? state.snapshot?.intake_version === COLD_INTAKE : manifestSha256 === MANIFEST_SHA256;
      const snapshot = await intake(args.requirement_id, cold);
      if (!state) {
        state = {snapshot, snapshot_sha256:digest(canonical(snapshot)),
          session_id:`session-${uuid()}`, request_id:uuid(), stage:'previewed',
          updated_at:new Date().toISOString()};
        await save(state);
      }
      return {...summary(state), preview:state.snapshot, current_input_matches:
        digest(canonical(snapshot)) === state.snapshot_sha256};
    }
    const state = await checkedState(args);
    if (args.action === 'inspect') {
      if (state.stage === 'previewed') return summary(state);
      try {
        const observation = await observed(state);
        const title = observation.events.filter(e => e.type === 'session/title').at(-1)?.data;
        const seen = accepted(observation.events, state.request_id);
        // Only positive durable prompt evidence can recover an uncertain submission.
        if (seen && ['dispatching','dispatch_unknown'].includes(state.stage))
          await transition(state, 'accepted');
        const status = getStatus(state.session_id);
        const projections = await controller.projections({sessionId:state.session_id}, new AbortController().signal);
        return {...summary(state), observed:true, observed_title:title,
          durable_prompt_observed:seen, event_count:observation.events.length,
          agent_status:status, projections};
      } catch (error) { return {...summary(state), observed:false, inspection_error:error.message}; }
    }
    if (args.action === 'create') {
      if (state.stage !== 'previewed') return {...summary(state), skipped:true,
        reason:'Creation already attempted; inspect before any recovery'};
      await assertColdScope(state);
      if (state.snapshot.intake_version === COLD_INTAKE) await coldContextReview(state);
      const workspaceId = await projectWorkspace();
      await transition(state, 'creating'); // persist IDs before Host creates anything
      try {
        const created = await controller.create({workspaceId, sessionId:state.session_id});
        if (created.sessionId !== state.session_id)
          throw new Error(`Host returned another session ${created.sessionId}; inspect before recovery`);
        await transition(state, 'created');
        const renamed = await controller.rename({sessionId:state.session_id,title:state.snapshot.title});
        if (renamed.title !== state.snapshot.title) throw new Error('Host normalized title; exact title requirement not met');
        await transition(state, 'titled');
      } catch (error) { await transition(state, 'create_unknown', error); }
      return summary(state);
    }
    if (args.action === 'dispatch') {
      if (state.stage !== 'titled') return {...summary(state), skipped:true,
        reason:'Only a confirmed exactly titled fresh session can be dispatched'};
      await assertColdScope(state);
      if (state.snapshot.intake_version === COLD_INTAKE) await coldContextReview(state);
      await observed(state);
      await beforeDispatch?.(args);
      await transition(state, 'dispatching');
      try {
        const result = await controller.prompt({requestId:state.request_id,
          sessionId:state.session_id, mode:'queue',
          content:[{type:'text',text:state.snapshot.prompt}],clientTimeZone:'Asia/Shanghai'},
        new AbortController().signal);
        if (result.accepted !== true) throw new Error('Host did not confirm acceptance');
        await transition(state, 'accepted');
      } catch (error) { await transition(state, 'dispatch_unknown', error); }
      return summary(state);
    }
    throw new Error('Unsupported action');
  }
  return {execute:args => serial(() => execute(args))};
}
