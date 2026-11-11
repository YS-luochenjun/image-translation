import { PROJECT } from './host-adapter.js';

class ControlError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 200 && !/[\u0000-\u001f]/.test(value);
const fail = (status, message) => { throw new ControlError(status, message); };

export function controlAllowed(ctx, peer, sessionId, action, request) {
  if (!peer?.principal || !validId(sessionId) || !['prompt', 'cancel'].includes(action)) return false;
  const args = request || (action === 'cancel' ? { sessionId } : { requestId: 'capability-probe', sessionId, mode: 'queue', content: [] });
  return ctx.remoteAccess.decide({ principal: peer.principal, action: 'session/' + action, resources: [{ kind: 'session', id: sessionId }], args: { request: args } }).kind === 'allow';
}

/** Prepare an exact request; only the Client's official Remote performs it. */
export async function prepareControl(ctx, peer, projectIds, body, loadSnapshot) {
  const keys = new Set(['action', 'session_id', 'request_id', 'run_id', 'node_id', 'instruction', 'mode', 'plan_event_id', 'plan_index']);
  if (Object.keys(body).some(key => !keys.has(key))) fail(400, 'Unknown flow control field');
  const sid = body.session_id, action = body.action;
  if (!validId(sid) || sid === '__unassigned__' || !projectIds.has(sid)) fail(403, 'Conversation does not belong to this project');
  if (!['prompt', 'cancel'].includes(action) || !validId(body.request_id)) fail(400, 'A control action and request identity are required');
  const read = { address: { kind: 'session', sessionId: sid }, throughSeq: 0, maxMessages: 1 };
  if (ctx.remoteAccess.decide({ principal: peer.principal, action: 'session/page', resources: [{ kind: 'session', id: sid }], args: { request: read } }).kind !== 'allow') fail(403, 'Conversation access denied');
  if (action === 'cancel') {
    if (['run_id', 'node_id', 'instruction', 'mode', 'plan_event_id', 'plan_index'].some(key => body[key] !== undefined)) fail(400, 'Stopping addresses the active conversation turn');
    const request = { sessionId: sid };
    if (!controlAllowed(ctx, peer, sid, action, request)) fail(403, 'Stopping this conversation is not permitted');
    return { prepared: true, action, session_id: sid, request_id: body.request_id, scope: { kind: 'session' }, request };
  }
  if (typeof body.instruction !== 'string' || !body.instruction.trim() || body.instruction.length > 6000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(body.instruction)) fail(400, 'Enter a concise instruction for this step');
  const mode = body.mode || 'queue';
  if (!['queue', 'steer'].includes(mode)) fail(400, 'Unknown instruction delivery mode');
  const data = await loadSnapshot([sid]);
  const session = data.sessions?.find(row => row.session_id === sid);
  if (!session) fail(404, 'Conversation state is no longer available');
  let scope = { kind: 'session' }, context = '会话任务';
  if (body.run_id !== undefined || body.node_id !== undefined) {
    if (!validId(body.run_id) || !validId(body.node_id) || body.plan_event_id !== undefined || body.plan_index !== undefined) fail(400, 'Select one exact production step');
    const run = session.runs?.find(row => row.id === body.run_id && row.session_id === sid);
    const node = run?.nodes?.find(row => row.id === body.node_id);
    if (!node) fail(404, 'This step does not belong to the selected conversation and flow');
    scope = { kind: 'node', run_id: run.id, node_id: node.id };
    context = `制作流程：${run.root}\n目标步骤：${node.title || node.id}（${node.id}）`;
  } else if (body.plan_event_id !== undefined || body.plan_index !== undefined) {
    if (!validId(body.plan_event_id) || !Number.isInteger(body.plan_index) || body.plan_index < 0 || body.plan_index >= 20) fail(400, 'Select one exact conversation plan step');
    if (session.plan?.event_id !== body.plan_event_id) fail(409, 'The conversation plan changed; select the step again');
    const item = session.plan.items?.[body.plan_index];
    if (!item || typeof item.content !== 'string') fail(404, 'Conversation plan step not found');
    scope = { kind: 'plan', plan_event_id: body.plan_event_id, plan_index: body.plan_index };
    context = `对话计划步骤：${item.content}`;
  }
  const text = `用户从项目流程台发来执行指令。\n项目：${PROJECT}\n${context}\n\n${body.instruction.trim()}\n\n请基于本会话真实进度处理，保留已有产物；报告本步骤关键结果与下一步。接收本指令不代表该步骤已完成。`;
  const request = { requestId: body.request_id, sessionId: sid, mode, content: [{ type: 'text', text }], clientTimeZone: 'Asia/Shanghai' };
  if (!controlAllowed(ctx, peer, sid, action, request)) fail(403, 'Sending an instruction to this conversation is not permitted');
  return { prepared: true, action, session_id: sid, request_id: body.request_id, scope, request };
}
