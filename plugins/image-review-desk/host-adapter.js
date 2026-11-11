import { request as httpRequest } from 'node:http';
import { spawn } from 'node:child_process';

export const PREFIX = '/api/local-image-review/';
export const PROJECT = 'C:/Users/Echo/Desktop/单图片翻译';
export const PROJECT_ID = 'decff9d24111a4a25e50239801a9ed4bb1e868111e3b0a2fa18fadd640c1ace1';
export const APP_ID = 'single-image-localizer-review-desk';
export const PYTHON = 'C:/Users/Echo/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe';
const MAX_BODY = 128 * 1024;
const MAX_JSON = 8 * 1024 * 1024;

class AdapterError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

/** Fixed loopback transport: never follows redirects or consults HTTP proxy env. */
export function localRequest(port, path, { method = 'GET', token, body, signal, timeout = 10000, maxBytes = MAX_JSON } = {}) {
  return new Promise((resolve, reject) => {
    const headers = { Accept: 'application/json' };
    if (token) headers['X-Review-Token'] = token;
    if (body) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = body.length; }
    const req = httpRequest({ hostname: '127.0.0.1', port, path, method, headers, signal }, (res) => {
      const chunks = []; let size = 0;
      res.on('data', (chunk) => {
        size += chunk.length;
        if (size > maxBytes) { res.destroy(new AdapterError(502, 'Review response exceeds size limit')); return; }
        chunks.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => resolve({ status: res.statusCode, type: res.headers['content-type'] || '', body: Buffer.concat(chunks) }));
    });
    const timer = setTimeout(() => req.destroy(new AdapterError(504, 'Review service timed out')), timeout);
    req.on('close', () => clearTimeout(timer));
    req.on('error', reject);
    req.end(body);
  });
}

function parseJson(response) {
  if (!/^application\/json(?:;|$)/i.test(response.type)) throw new AdapterError(502, 'Invalid review service response');
  try { return JSON.parse(response.body.toString('utf8')); }
  catch { throw new AdapterError(502, 'Invalid review service response'); }
}

/** Owns only the Python process it starts. Existing verified services stay alive. */
export function createBackend({ port = 8765, project = PROJECT, projectId = PROJECT_ID, python = PYTHON, startupTimeout = 12000, transport = localRequest, spawnProcess = spawn } = {}) {
  let child, starting, disposed = false;
  const controller = new AbortController();
  async function identity() {
    const response = await transport(port, '/api/bootstrap', { timeout: 1500, signal: controller.signal, maxBytes: MAX_BODY });
    const data = parseJson(response);
    if (response.status !== 200 || data.app !== APP_ID || data.project_id !== projectId || typeof data.token !== 'string' || !data.token || data.token.length > 256) {
      throw new AdapterError(503, 'Port is occupied by another application or project');
    }
    return data;
  }
  async function stopChild() {
    const owned = child; child = undefined;
    if (!owned || owned.exitCode !== null || owned.signalCode !== null) return;
    await new Promise((resolve) => {
      let settled = false;
      const finish = () => { if (!settled) { settled = true; clearTimeout(timer); resolve(); } };
      const timer = setTimeout(() => { owned.kill('SIGKILL'); finish(); }, 1500);
      owned.once('exit', finish);
      owned.kill();
    });
  }
  async function start() {
    if (disposed) throw new AdapterError(503, 'Review adapter is closed');
    try { return await identity(); }
    catch (error) {
      if (error.code !== 'ECONNREFUSED') throw error;
    }
    if (disposed) throw new AdapterError(503, 'Review adapter is closed');
    await stopChild();
    try {
      child = spawnProcess(python, ['bin/it.py', 'review-desk', '--root', project, '--port', String(port)], {
        cwd: project, windowsHide: true, stdio: 'ignore', shell: false,
      });
    } catch (error) {
      const detail = ['EPERM', 'EACCES', 'ENOENT'].includes(error.code) ? ` (${error.code})` : '';
      throw new AdapterError(503, 'Unable to start the project review service' + detail);
    }
    const owned = child;
    let processError;
    owned.once('error', (error) => { processError = error; });
    const deadline = Date.now() + startupTimeout;
    while (!disposed && Date.now() < deadline) {
      if (processError || owned.exitCode !== null || owned.signalCode !== null) break;
      try { return await identity(); }
      catch (error) {
        if (error.code !== 'ECONNREFUSED') { await stopChild(); throw error; }
      }
      await new Promise((resolve) => {
        const done = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', done); resolve(); };
        const timer = setTimeout(done, 100);
        controller.signal.addEventListener('abort', done, { once: true });
      });
    }
    await stopChild();
    throw new AdapterError(503, 'Unable to start the project review service');
  }
  return {
    async ready() {
      if (!starting) starting = start().finally(() => { starting = undefined; });
      return starting;
    },
    async request(path, options = {}) {
      const data = await this.ready();
      return transport(port, path, { ...options, token: data.token, signal: controller.signal });
    },
    async dispose() { disposed = true; controller.abort(); await stopChild(); },
  };
}

function header(req, name) {
  const value = req.headers[name];
  return typeof value === 'string' ? value : undefined;
}
function send(res, status, body, type = 'application/json; charset=utf-8') {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': type, 'Content-Length': String(bytes.length), 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin' });
  res.end(bytes);
}
async function readBody(req) {
  if (!/^application\/json(?:\s*;|$)/i.test(header(req, 'content-type') || '')) throw new AdapterError(415, 'Content-Type must be application/json');
  if (header(req, 'transfer-encoding')) throw new AdapterError(400, 'Transfer encoding is not accepted');
  const length = header(req, 'content-length');
  if (!/^\d+$/.test(length || '') || Number(length) < 1 || Number(length) > MAX_BODY) throw new AdapterError(413, 'Request body is empty or too large');
  const chunks = []; let size = 0;
  const timer = setTimeout(() => req.destroy(), 10000);
  try {
    for await (const chunk of req) {
      size += chunk.length;
      if (size > MAX_BODY) throw new AdapterError(413, 'Request body is too large');
      chunks.push(chunk);
    }
  } finally { clearTimeout(timer); }
  if (size !== Number(length)) throw new AdapterError(400, 'Incomplete request body');
  const body = Buffer.concat(chunks);
  try { const value = JSON.parse(body.toString('utf8')); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(); }
  catch { throw new AdapterError(400, 'Invalid JSON object'); }
  return body;
}

/** Closed routing: no caller-controlled URL, backend path or credential. */
export function routeTarget(rawUrl, method) {
  if (typeof rawUrl !== 'string' || !rawUrl.startsWith(PREFIX) || rawUrl.includes('#') || rawUrl.includes('\\')) throw new AdapterError(404, 'Endpoint not found');
  const queryAt = rawUrl.indexOf('?');
  const path = queryAt < 0 ? rawUrl : rawUrl.slice(0, queryAt);
  const query = new URLSearchParams(queryAt < 0 ? '' : rawUrl.slice(queryAt + 1));
  if (method !== 'GET' && method !== 'POST') throw new AdapterError(405, 'Method not allowed');
  const leaf = path.slice(PREFIX.length);
  if (leaf === 'image' && method === 'GET') {
    const values = query.getAll('path');
    if ([...query.keys()].some((key) => key !== 'path') || values.length !== 1 || !values[0] || values[0].length > 4096 || /[\x00-\x1f\\]/.test(values[0]) || values[0].startsWith('/') || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(values[0]) || values[0].split('/').includes('..')) throw new AdapterError(400, 'One project-relative image path is required');
    return { path: '/api/assets?path=' + encodeURIComponent(values[0]), image: true };
  }
  if (query.size) throw new AdapterError(400, 'Unexpected query parameters');
  if (leaf === 'health' && method === 'GET') return { health: true };
  if (leaf === 'tasks' && method === 'GET') return { path: '/api/tasks' };
  const match = /^tasks\/([^/]+)(?:\/(revision-prompt|feedback))?$/.exec(leaf);
  if (!match) throw new AdapterError(404, 'Endpoint not found');
  let id;
  try { id = decodeURIComponent(match[1]); } catch { throw new AdapterError(400, 'Invalid task ID'); }
  if (!id || id.length > 2048 || /[\x00-\x1f\\]/.test(id)) throw new AdapterError(400, 'Invalid task ID');
  const action = match[2];
  if ((action === 'feedback') !== (method === 'POST')) throw new AdapterError(405, 'Method not allowed');
  return { path: '/api/tasks/' + encodeURIComponent(id) + (action ? '/' + action : '') };
}

export function createHandler({ connection, getOrigin, backend, projectId = PROJECT_ID }) {
  return async (req, res) => {
    try {
      // Admission receives ORIGINAL carrier headers, before proxy headers exist.
      const admission = connection.admit(req);
      if ('rejection' in admission) throw new AdapterError(admission.rejection, 'DSH authentication required');
      const origin = new URL(getOrigin());
      if (header(req, 'host') !== origin.host || (header(req, 'origin') !== undefined && header(req, 'origin') !== origin.origin) || ![undefined, 'same-origin', 'none'].includes(header(req, 'sec-fetch-site'))) throw new AdapterError(403, 'Same-origin request required');
      if (req.method === 'POST' && header(req, 'origin') !== origin.origin) throw new AdapterError(403, 'Same-origin Origin required for feedback');
      const target = routeTarget(req.url, req.method);
      const body = req.method === 'POST' ? await readBody(req) : undefined;
      if (target.health) {
        await backend.ready();
        send(res, 200, { app: APP_ID, project_id: projectId, ready: true });
        return;
      }
      const response = await backend.request(target.path, { method: req.method, body, maxBytes: target.image ? 64 * 1024 * 1024 : MAX_JSON });
      if (response.status >= 300 && response.status < 400) throw new AdapterError(502, 'Review service redirects are refused');
      if (target.image && response.status === 200) {
        if (!/^image\/(png|jpeg|webp|gif)(?:;|$)/i.test(response.type)) throw new AdapterError(502, 'Invalid review image');
        send(res, response.status, response.body, response.type);
      } else {
        const data = parseJson(response);
        if (data && typeof data === 'object' && 'token' in data) throw new AdapterError(502, 'Unexpected review response');
        send(res, response.status, data);
      }
    } catch (error) {
      if (!res.headersSent && !res.destroyed) send(res, error.status || 503, { error: error.status ? error.message : 'Local review service is unavailable' });
      else if (!res.writableEnded) res.destroy();
    }
  };
}
