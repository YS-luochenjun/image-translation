import { PROJECT, PREFIX } from './host-adapter.js';
import { controlAllowed, prepareControl } from './workflow-control.js?revision=workflow-v5.2.1';
import { projectSessionRecords } from './workflow-capture.js?revision=workflow-v5.2.1';

const MAX_BODY = 128 * 1024;
const MAX_JSON = 8 * 1024 * 1024;
const ROOT = PREFIX + 'workflow';
class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
const normalized = value => typeof value === 'string' ? value.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase() : '';
const header = (req, name) => typeof req.headers[name] === 'string' ? req.headers[name] : undefined;
function send(res, status, value, type = 'application/json; charset=utf-8') {
  const body = Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value));
  res.writeHead(status, { 'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin' });
  res.end(body);
}
function json(response) {
  if (!/^application\/json(?:;|$)/i.test(response.type)) throw new HttpError(502, 'Invalid workflow response');
  let value;
  try { value = JSON.parse(response.body.toString('utf8')); } catch { throw new HttpError(502, 'Invalid workflow JSON'); }
  if (response.status >= 300) throw new HttpError(response.status, value.error || 'Workflow request failed');
  return value;
}
async function readBody(req) {
  if (!/^application\/json(?:\s*;|$)/i.test(header(req, 'content-type') || '')) throw new HttpError(415, 'JSON required');
  const length = Number(header(req, 'content-length'));
  if (header(req, 'transfer-encoding') || !Number.isSafeInteger(length) || length < 1 || length > MAX_BODY) throw new HttpError(413, 'Invalid workflow request size');
  let size = 0; const chunks = [];
  for await (const chunk of req) { size += chunk.length; if (size > MAX_BODY) throw new HttpError(413, 'Workflow request too large'); chunks.push(chunk); }
  if (size !== length) throw new HttpError(400, 'Incomplete workflow request');
  try { const value = JSON.parse(Buffer.concat(chunks).toString('utf8')); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(); return value; }
  catch { throw new HttpError(400, 'JSON object required'); }
}

/** Explicitly delegate EXISTING session capabilities for the admitted principal.
 * Never call allows() outside a Remote context, fabricate an owner peer, or
 * register a replacement policy. Final message dispatch still uses session.prompt.
 */
export function permitted(ctx, peer, sessionId, write = false) {
  if (!peer?.principal || typeof sessionId !== 'string' || !sessionId || sessionId.length > 200) return false;
  const request = write ? { sessionId, mode: 'queue', content: [] } : { address: { kind: 'session', sessionId }, throughSeq: 0, maxMessages: 1 };
  return ctx.remoteAccess.decide({ principal: peer.principal, action: write ? 'session/prompt' : 'session/page', resources: [{ kind: 'session', id: sessionId }], args: { request } }).kind === 'allow';
}
function globalRead(ctx, peer) {
  return !!peer?.principal && ctx.remoteAccess.decide({ principal: peer.principal, action: 'session/page', resources: [{ kind: 'global' }], args: {} }).kind === 'allow';
}
export async function projectSessions(ctx) {
  const records = await projectSessionRecords(ctx, PROJECT);
  return new Set(records.filter(row => row.header.origin !== 'subagent').sort((a, b) => (b.header.createdAt || 0) - (a.header.createdAt || 0)).map(row => row.header.id));
}
function artifactUrl(sessionId, runId, artifact, saved = false) {
  const query = new URLSearchParams({ session_id: sessionId, run_id: runId, artifact_id: saved ? artifact.artifact_id : artifact.id, sha256: artifact.sha256 });
  return ROOT + (saved ? '/saved?' : '/artifact?') + query;
}
function presentRun(run, sessionId) {
  const present = artifact => ({ ...artifact, url: artifactUrl(sessionId, run.id, artifact) });
  return { ...run, nodes: (run.nodes || []).map(node => ({ ...node, artifacts: (node.artifacts || []).map(present) })), result: run.result?.id ? present(run.result) : null, results: (run.results || []).map(present), saved_results: (run.saved_results || []).map(saved => ({ ...saved, url: artifactUrl(sessionId, run.id, saved, true) })) };
}
async function snapshot(backend, ids) {
  return json(await backend.request('/api/workflow/snapshot', { method: 'POST', body: Buffer.from(JSON.stringify({ session_ids: ids })), maxBytes: MAX_JSON, timeout: 30000 }));
}
function findArtifact(data, sessionId, runId, artifactId, sha256, saved = false) {
  const session = (data.sessions || []).find(row => row.session_id === sessionId);
  const runs = sessionId === '__unassigned__' ? data.unassigned || [] : session?.runs || [];
  const run = runs.find(row => row.id === runId);
  const artifact = saved ? run?.saved_results?.find(row => row.artifact_id === artifactId) : run && (run.nodes || []).flatMap(node => node.artifacts || []).find(row => row.id === artifactId);
  if (!artifact) throw new HttpError(404, 'This artifact does not belong to the selected conversation and flow');
  if (!sha256 || artifact.sha256 !== sha256) throw new HttpError(409, 'The stage artifact changed; reopen it before commenting');
  return artifact;
}

export function createWorkflowHandler(ctx, backend, fallback) {
  // Immutable, scoped handles avoid rebuilding the whole project for every thumbnail.
  // Each request still checks the real principal and the private server rehashes bytes.
  const handles = new Map(), resolving = new Map();
  const handleKey = (sid, run, id, hash, saved) => JSON.stringify([sid, run, id, hash, saved]);
  function indexRun(run, sid) {
    const add = (artifact, saved = false) => {
      const key = handleKey(sid, run.id, saved ? artifact.artifact_id : artifact.id, artifact.sha256, saved);
      handles.delete(key); handles.set(key, { ...artifact, expires: Date.now() + 60000 });
      while (handles.size > 4000) handles.delete(handles.keys().next().value);
    };
    for (const node of run.nodes || []) for (const artifact of node.artifacts || []) add(artifact);
    for (const saved of run.saved_results || []) add(saved, true);
  }
  async function loadSnapshot(ids) {
    const data = await snapshot(backend, ids);
    for (const session of data.sessions || []) for (const run of session.runs || []) indexRun(run, session.session_id);
    return data;
  }
  async function loadIndex(ids) {
    return json(await backend.request('/api/workflow/index', { method: 'POST', body: Buffer.from(JSON.stringify({ session_ids: ids })), maxBytes: MAX_JSON, timeout: 30000 }));
  }
  async function titleMetadata() {
    const controller = ctx.get?.('sessionController');
    if (controller) {
      try {
        const result = await controller.list({}, new AbortController().signal);
        if (Array.isArray(result?.items)) return { metadata: new Map(result.items.map(item => [item.sessionId, item])), unavailable: false };
      } catch { /* No authoritative current titles means no visible requirements. */ }
    }
    return { metadata: new Map(), unavailable: true };
  }
  const requirementTitle = info => typeof info?.projections?.values?.title === 'string' ? info.projections.values.title.trim() : '';
  const isRequirement = info => /^[A-Z][A-Z0-9]{1,15}-\d{8}-\d{3,6}$/i.test(requirementTitle(info));
  function presentSession(row, info, peer, detailLoaded) {
    return { ...row, title: requirementTitle(info), status: typeof info?.running === 'boolean' ? info.running ? 'running' : 'idle' : row.status, detail_loaded: detailLoaded,
      can_send: permitted(ctx, peer, row.session_id, true), can_control: { prompt: controlAllowed(ctx, peer, row.session_id, 'prompt'), cancel: controlAllowed(ctx, peer, row.session_id, 'cancel') },
      runs: (row.runs || []).map(run => detailLoaded ? presentRun(run, row.session_id) : { ...run, detail_loaded: false }) };
  }
  async function inspectRoot(root) {
    const data = json(await backend.request('/api/workflow/unassigned', { method: 'POST', body: Buffer.from(JSON.stringify({ run_root: root })), maxBytes: MAX_JSON, timeout: 30000 }));
    indexRun(data.run, '__unassigned__');
    return data;
  }
  return async (req, res) => {
    if (typeof req.url !== 'string' || !(req.url === ROOT || req.url.startsWith(ROOT + '/') || req.url.startsWith(ROOT + '?'))) return fallback(req, res);
    try {
      const admission = ctx.connection.admit(req);
      if ('rejection' in admission) throw new HttpError(admission.rejection, 'DSH authentication required');
      const peer = admission.peer;
      const origin = new URL(ctx.webServer.getOrigin());
      if (header(req, 'host') !== origin.host || (header(req, 'origin') !== undefined && header(req, 'origin') !== origin.origin) || ![undefined, 'same-origin', 'none'].includes(header(req, 'sec-fetch-site'))) throw new HttpError(403, 'Same-origin request required');
      if (req.method === 'POST' && header(req, 'origin') !== origin.origin) throw new HttpError(403, 'Same-origin Origin required');
      const url = new URL(req.url, origin);
      const ids = await projectSessions(ctx);
      const membership = json(await backend.request('/api/workflow/session-ids'));
      if (Array.isArray(membership.session_ids) && membership.session_ids.length) {
        const stillPresent = new Map((await ctx.sessionQuery.listSessions()).map(row => [row.header.id, row.header]));
        for (const id of membership.session_ids) if (stillPresent.has(id) && stillPresent.get(id).origin !== 'subagent') ids.add(id);
      }
      if (req.method === 'GET' && url.pathname === ROOT && !url.search) {
        const { metadata, unavailable: titleFilterUnavailable } = await titleMetadata();
        const permittedIds = [...ids].filter(id => permitted(ctx, peer, id));
        const matched = permittedIds.filter(id => isRequirement(metadata.get(id)));
        const allowed = matched.slice(0, 100);
        let data = allowed.length ? await loadIndex(allowed) : { schema_version: 1, sessions: [], unassigned: [] };
        const known = new Set((data.sessions || []).map(row => row.session_id));
        const missing = allowed.filter(id => !known.has(id));
        if (missing.length) {
          json(await backend.request('/api/workflow/ingest', { method: 'POST', body: Buffer.from(JSON.stringify({ sessions: missing.map(session_id => ({ session_id, cwd: PROJECT, events: [] })) })) }));
          data = await loadIndex(allowed);
        }
        const allowedSet = new Set(allowed);
        data.sessions = (data.sessions || []).filter(row => allowedSet.has(row.session_id)).map(row => presentSession(row, metadata.get(row.session_id), peer, false));
        data.unassigned = [];
        data.discovery = { project_sessions: ids.size, visible_sessions: allowed.length, matched: matched.length, hidden_count: permittedIds.length - matched.length, title_filter_unavailable: titleFilterUnavailable };
        send(res, 200, data); return;
      }
      if (req.method === 'GET' && url.pathname === ROOT + '/session') {
        if ([...url.searchParams.keys()].some(key => key !== 'session_id') || url.searchParams.getAll('session_id').length !== 1 || !url.searchParams.get('session_id')) throw new HttpError(400, 'Exactly one conversation is required');
        const sessionId = url.searchParams.get('session_id');
        if (!ids.has(sessionId) || !permitted(ctx, peer, sessionId)) throw new HttpError(403, 'Conversation access denied');
        const { metadata, unavailable } = await titleMetadata();
        if (unavailable) throw new HttpError(503, 'Current conversation titles are unavailable');
        const info = metadata.get(sessionId);
        if (!isRequirement(info)) throw new HttpError(403, 'Requirement conversation required');
        const data = await loadSnapshot([sessionId]);
        const row = (data.sessions || []).find(item => item.session_id === sessionId);
        if (!row) throw new HttpError(404, 'Conversation workflow not found');
        send(res, 200, { session: presentSession(row, info, peer, true), node_catalog: data.node_catalog || [], warnings: data.warnings || [] }); return;
      }
      if (req.method === 'GET' && url.pathname === ROOT + '/unassigned') {
        if (!globalRead(ctx, peer)) throw new HttpError(403, 'Project history access denied');
        if ([...url.searchParams.keys()].some(key => key !== 'run_root') || url.searchParams.getAll('run_root').length !== 1) throw new HttpError(400, 'One historical flow is required');
        const data = await inspectRoot(url.searchParams.get('run_root'));
        send(res, 200, { ...data, run: presentRun(data.run, '__unassigned__') }); return;
      }
      if (req.method === 'GET' && [ROOT + '/artifact', ROOT + '/saved'].includes(url.pathname)) {
        if ([...url.searchParams.keys()].some(key => !['session_id', 'run_id', 'artifact_id', 'sha256'].includes(key)) || ['session_id', 'run_id', 'artifact_id', 'sha256'].some(key => url.searchParams.getAll(key).length !== 1)) throw new HttpError(400, 'Exactly one bound stage artifact is required');
        const sessionId = url.searchParams.get('session_id');
        const historical = sessionId === '__unassigned__', saved = url.pathname.endsWith('/saved');
        if (historical ? saved || !globalRead(ctx, peer) : !ids.has(sessionId) || !permitted(ctx, peer, sessionId)) throw new HttpError(403, 'Conversation access denied');
        const runId = url.searchParams.get('run_id'), artifactId = url.searchParams.get('artifact_id'), hash = url.searchParams.get('sha256');
        const key = handleKey(sessionId, runId, artifactId, hash, saved);
        let artifact = handles.get(key);
        if (!artifact || artifact.expires < Date.now()) {
          const scope = historical ? sessionId + ':' + runId : sessionId;
          if (!resolving.has(scope)) resolving.set(scope, (async () => {
            const data = await loadSnapshot(historical ? [] : [sessionId]);
            if (historical) {
              const metadata = (data.unassigned || []).find(item => item.id === runId);
              if (!metadata) throw new HttpError(404, 'Historical flow not found');
              data.unassigned = [(await inspectRoot(metadata.root)).run];
            }
            return data;
          })().finally(() => resolving.delete(scope)));
          artifact = findArtifact(await resolving.get(scope), sessionId, runId, artifactId, hash, saved);
        }
        const response = await backend.request('/api/workflow/asset?path=' + encodeURIComponent(artifact.path) + '&sha256=' + encodeURIComponent(artifact.sha256), { maxBytes: 48 * 1024 * 1024 });
        if (response.status !== 200) { json(response); return; }
        if (!/^(image\/(png|jpeg|webp|gif)|application\/json|text\/plain)(?:;|$)/i.test(response.type)) throw new HttpError(415, 'Unsupported stage artifact');
        send(res, 200, response.body, response.type); return;
      }
      if (req.method !== 'POST' || url.search) throw new HttpError(404, 'Workflow endpoint not found');
      if (url.pathname === ROOT + '/control') {
        const body = await readBody(req);
        const intent = await prepareControl(ctx, peer, ids, body, loadSnapshot);
        send(res, 200, intent); return;
      }
      const actions = new Map([[ROOT + '/comments', '/api/workflow/comments'], [ROOT + '/delivery', '/api/workflow/delivery'], [ROOT + '/results/save', '/api/workflow/results/save'], [ROOT + '/bind', '/api/workflow/bind']]);
      const target = actions.get(url.pathname);
      if (!target) throw new HttpError(404, 'Workflow endpoint not found');
      const body = await readBody(req);
      const sessionId = body.session_id;
      if (!ids.has(sessionId) || !permitted(ctx, peer, sessionId, target !== '/api/workflow/results/save')) throw new HttpError(403, 'The original conversation is unavailable or not permitted');
      const response = await backend.request(target, { method: 'POST', body: Buffer.from(JSON.stringify(body)), maxBytes: MAX_JSON });
      const data = json(response);
      if ((data.session_id && data.session_id !== sessionId) || (data.comment?.session_id && data.comment.session_id !== sessionId)) throw new HttpError(502, 'Workflow conversation binding mismatch');
      if (data.saved) {
        if (data.saved.session_id !== sessionId || data.saved.run_id !== body.run_id || data.saved.artifact_id !== body.artifact_id || data.saved.sha256 !== body.sha256) throw new HttpError(502, 'Saved result binding mismatch');
        data.saved = { ...data.saved, url: artifactUrl(sessionId, body.run_id, data.saved, true) };
        indexRun({ id: body.run_id, saved_results: [data.saved] }, sessionId);
      }
      send(res, response.status, data);
    } catch (error) {
      if (!res.headersSent && !res.destroyed) send(res, error.status || 503, { error: error.status ? error.message : 'Workflow service is unavailable' });
      else if (!res.writableEnded) res.destroy();
    }
  };
}
