#!/usr/bin/env node
/** Restricted image publication caller. Importing this module never connects. */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, open, realpath, stat, lstat } from 'node:fs/promises';
import { dirname, join, relative, resolve, isAbsolute } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { homedir, platform } from 'node:os';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH = /^[0-9a-f]{64}$/;
const TOOLS = new Set(['material_r2_upload_from_bytes', 'material_upload_from_url', 'material_append_images', 'material_attribution_update', 'requirement_bind_materials']);
const fail = message => { throw new Error(message); };
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const json = async path => JSON.parse(await readFile(path, 'utf8'));
const inside = (base, path) => { const rel = relative(base, path); return rel !== '..' && !rel.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(rel); };

export function decodeResult(result) {
  if (result?.structuredContent) return result.structuredContent;
  const text = result?.content?.filter(item => item.type === 'text').map(item => item.text).join('\n');
  if (text) {
    try { return JSON.parse(text); } catch { /* Some upstream text renders escaped JSON. */ }
    try { return JSON.parse(text.replaceAll('\\n', '\n').replaceAll('\\"', '"')); } catch { /* Keep opaque receipts. */ }
  }
  return result;
}

/** Validate the subset used by the pinned catalog, rejecting unknown object keys. */
export function validateSchema(value, schema, at = 'arguments') {
  if (schema.anyOf) {
    if (!schema.anyOf.some(branch => { try { validateSchema(value, branch, at); return true; } catch { return false; } })) fail(`${at}: anyOf mismatch`);
    return;
  }
  const type = schema.type;
  if (type === 'object' && (!value || typeof value !== 'object' || Array.isArray(value))) fail(`${at}: expected object`);
  if (type === 'array' && !Array.isArray(value)) fail(`${at}: expected array`);
  if (type === 'string' && typeof value !== 'string') fail(`${at}: expected string`);
  if (type === 'boolean' && typeof value !== 'boolean') fail(`${at}: expected boolean`);
  if (type === 'null' && value !== null) fail(`${at}: expected null`);
  if (type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) fail(`${at}: expected finite number`);
  if (type === 'integer' && !Number.isSafeInteger(value)) fail(`${at}: expected safe integer`);
  if (schema.enum && !schema.enum.includes(value)) fail(`${at}: invalid enum`);
  if ('const' in schema && value !== schema.const) fail(`${at}: invalid constant`);
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) fail(`${at}: too short`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) fail(`${at}: too long`);
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) fail(`${at}: pattern mismatch`);
    if (schema.format === 'uuid' && !UUID.test(value)) fail(`${at}: invalid UUID`);
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) fail(`${at}: below minimum`);
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) fail(`${at}: below exclusive minimum`);
    if (schema.maximum !== undefined && value > schema.maximum) fail(`${at}: above maximum`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) fail(`${at}: too few items`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) fail(`${at}: too many items`);
    value.forEach((item, index) => validateSchema(item, schema.items || {}, `${at}[${index}]`));
  } else if (type === 'object') {
    for (const key of schema.required || []) if (!(key in value)) fail(`${at}.${key}: required`);
    for (const [key, item] of Object.entries(value)) {
      if (!Object.hasOwn(schema.properties || {}, key)) fail(`${at}.${key}: unapproved field`);
      validateSchema(item, schema.properties[key], `${at}.${key}`);
    }
  }
}

function collect(value, key) {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([name, child]) => [ ...(name === key ? [child] : []), ...collect(child, key) ]);
}

async function appendJournal(path, entry) {
  const handle = await open(path, 'a');
  try { await handle.writeFile(`${JSON.stringify({ ...entry, at: new Date().toISOString() })}\n`); await handle.sync(); }
  finally { await handle.close(); }
}

async function loadJournal(path) {
  let text;
  try { text = await readFile(path, 'utf8'); } catch (error) { if (error.code === 'ENOENT') return []; throw error; }
  return Promise.all(text.split('\n').filter(Boolean).map(async line => {
    const entry = JSON.parse(line);
    if (!entry.receipt_sha256) return entry;
    if (entry.state !== 'received' || !UUID.test(entry.operation_id) || !HASH.test(entry.receipt_sha256)) fail('Invalid receipt reference');
    const bytes = await readFile(join(dirname(path),`publication-${entry.operation_id}.json`));
    if (sha256(bytes) !== entry.receipt_sha256) fail('Publication receipt hash mismatch');
    const receipt = JSON.parse(bytes);
    if (receipt.operation_id !== entry.operation_id || receipt.requirement_id !== entry.requirement_id ||
        receipt.tool !== entry.tool || receipt.state !== 'received') fail('Publication receipt identity mismatch');
    return {...entry,response:receipt.response};
  }));
}

async function reviewedOutputs(folder, request, requirementId) {
  if (typeof request.gate_file !== 'string') fail('Upload/import requires gate_file');
  const gatePath = await realpath(resolve(folder, request.gate_file));
  if (!inside(folder, gatePath)) fail('Gate outside requirement folder');
  const gate = await json(gatePath);
  if (gate.requirement_id !== requirementId || gate.passed !== true || !gate.reviewed_by?.trim() || !gate.evidence?.trim()) fail('Gate must assert this requirement, passed review, reviewer and evidence');
  if (!Array.isArray(gate.outputs) || !gate.outputs.length) fail('Gate outputs missing');
  const outputs = [];
  for (const output of gate.outputs) {
    if (!HASH.test(output.sha256) || typeof output.path !== 'string') fail('Gate output path/hash invalid');
    const path = resolve(folder, output.path);
    if (!inside(folder, path)) fail('Reviewed output outside requirement folder');
    const identity = memberIdentity(output);
    if (identity && identity.some(value => typeof value !== 'string' || !value.trim())) fail('Incomplete member identity');
    outputs.push({ ...output, path });
  }
  return outputs;
}

const memberFields = ['source_id', 'language', 'member_id'];
const memberIdentity = output => memberFields.some(key => key in output)
  ? memberFields.map(key => output[key]) : null;
const outputBinding = output => ({
  path: output.path, sha256: output.sha256,
  ...Object.fromEntries(memberFields.filter(key => key in output).map(key => [key, output[key]]))
});
function matchesMember(left, right) {
  const identity = memberIdentity(left);
  const other = memberIdentity(right);
  return identity && other && identity.every((value, index) => value === other[index]);
}

function hasUploadResource(entry) {
  const result = decodeResult(entry.response);
  return result?.success !== false &&
    [...collect(result, 'resourceId'), ...collect(result, 'resource_id'),
      ...collect(result, 'publicUrl'), ...collect(result, 'public_url')]
      .some(value => typeof value === 'string' && value.trim());
}

function provenUploads(entries, operationIds, requirementId) {
  if (!Array.isArray(operationIds) || !operationIds.length ||
      operationIds.length !== new Set(operationIds).size) fail('Upload operation IDs must be unique');
  const uploads = recordsFor(entries, 'material_r2_upload_from_bytes')
    .filter(entry => entry.requirement_id === requirementId && operationIds.includes(entry.operation_id));
  if (uploads.length !== operationIds.length) fail('Upload provenance not found');
  return uploads.map(entry => {
    const [output] = entry.outputs || [];
    if (entry.outputs?.length !== 1 || !HASH.test(output.sha256)) fail('Missing reviewed upload binding');
    return { ...uploadResource(decodeResult(entry.response)), output };
  });
}

function imageKind(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return { mime:'image/png', ext:'png' };
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return { mime:'image/jpeg', ext:'jpg' };
  if (/^GIF8[79]a$/.test(bytes.subarray(0, 6).toString('ascii'))) return { mime:'image/gif', ext:'gif' };
  fail('Only PNG/JPEG/GIF image bytes allowed');
}

function recordsFor(entries, tool) {
  return entries.filter(entry => entry.state === 'received' && !entry.response?.isError && entry.tool === tool);
}

function uploadResource(result) {
  const urls = [...new Set([...collect(result, 'publicUrl'), ...collect(result, 'public_url')])];
  const ids = [...new Set([...collect(result, 'resourceId'), ...collect(result, 'resource_id')])];
  if (result?.success === false || urls.length !== 1 || ids.length !== 1 ||
      typeof urls[0] !== 'string' || !urls[0].trim() ||
      typeof ids[0] !== 'string' || !UUID.test(ids[0])) {
    fail('Upload receipt does not prove one reviewed resource');
  }
  return {url:urls[0],id:ids[0]};
}

async function publicationContract(root, folder, tool) {
  const scopedPath = join(folder, `contract-${tool}.json`);
  let scoped = true;
  try {
    await lstat(scopedPath);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    // Legacy batches can keep their pinned catalog; a present scoped file is authoritative.
    scoped = false;
  }
  const path = scoped ? await realpath(scopedPath)
    : join(root, 'work', 'fireplay-readonly', 'publication-full-definitions.json');
  if (scoped && !inside(folder, path)) fail('Contract outside requirement folder');
  const bytes = await readFile(path);
  const snapshot = JSON.parse(bytes.toString('utf8'));
  if (!scoped && !Array.isArray(snapshot)) fail('Cached catalog must be an array');
  const definition = scoped ? snapshot : snapshot.find(item => item.name === tool);
  if (definition?.name !== tool) fail('Contract snapshot tool mismatch');
  return {definition, evidence:{path:relative(root, path),sha256:sha256(bytes)}};
}

async function prepareArguments({ request, intake, folder, entries }) {
  const args = structuredClone(request.arguments || {});
  if ('feishu_user_id' in args) fail('Caller identity must come from JWT');
  if (request.tool === 'material_r2_upload_from_bytes') {
    if ('file_base64' in args) fail('Do not supply base64; caller reads reviewed output');
    const outputs = await reviewedOutputs(folder, request, intake.summary.id);
    const selected = outputs.filter(item => item.sha256 === request.output_sha256 &&
      (request.output_path === undefined || item.path === resolve(folder, request.output_path)));
    if (selected.length !== 1) fail('output_sha256 must identify one reviewed output');
    const [output] = selected;
    const prior = recordsFor(entries, request.tool).filter(entry =>
      entry.requirement_id === intake.summary.id && hasUploadResource(entry));
    if (prior.some(entry => entry.outputs?.some(old =>
      matchesMember(old, output) || old.sha256 === output.sha256 || old.path === output.path))) {
      fail('Member already uploaded; reconcile the frozen resource');
    }
    const path = await realpath(output.path);
    if (!inside(folder, path)) fail('Reviewed output outside requirement folder');
    if ((await stat(path)).size > 50 * 1024 * 1024) fail('Upload exceeds 50MiB');
    const bytes = await readFile(path);
    if (sha256(bytes) !== output.sha256) fail('Reviewed output changed');
    const kind = imageKind(bytes);
    if (bytes.length > 50 * 1024 * 1024) fail('Upload exceeds 50MiB');
    if (args.content_type !== kind.mime || ![kind.ext, ...(kind.ext === 'jpg' ? ['jpeg'] : [])].includes(args.ext)) fail('MIME/ext do not match image bytes');
    return { args: { ...args, file_base64:bytes.toString('base64') }, outputs:[outputBinding({...output, path})] };
  }
  if (request.tool === 'material_upload_from_url') {
    if (args.confirmed !== true) fail('Import requires explicit confirmed:true; false is skipped');
    if ('VedioSouce' in args || !Array.isArray(args.items) || !args.items.length) fail('Only ordinary image/button import allowed');
    const pairs = provenUploads(entries, request.upload_operation_ids, intake.summary.id);
    await checkImportGate(folder, request, intake.summary.id, pairs);
    const groups = [];
    for (const item of args.items) {
      const allowed = new Set(['url','urls','already_in_r2','resource_id','resource_ids','source','type','name','file_name','file_names','tags','shared_dept_id','mime_type','file_size','md5','width','height']);
      for (const key of Object.keys(item)) if (!allowed.has(key)) fail(`Import field forbidden: ${key}`);
      if (!['image','button'].includes(item.type) || item.type !== intake.summary.assetType) fail('Import type must match image/button requirement');
      if (item.already_in_r2 !== true || item.source !== 'MCP' || !item.name?.startsWith(`${intake.summary.code}-`)) fail('Import requires exact code name, MCP source and uploaded R2');
      if (typeof item.shared_dept_id !== 'string' || !item.shared_dept_id.trim()) fail('Import requires explicitly researched group sharing ID');
      const urls = item.url ? [item.url] : item.urls;
      const ids = item.resource_id ? [item.resource_id] : item.resource_ids;
      if (Boolean(item.url) === Boolean(item.urls) || Boolean(item.resource_id) === Boolean(item.resource_ids)) fail('Choose single or bundled URL/resource fields');
      if (!Array.isArray(urls) || !urls.length || !Array.isArray(ids) || urls.length !== ids.length) fail('URL/resource alignment invalid');
      if (item.type === 'button' && (!item.url || urls.length !== 1)) fail('Button must be single URL');
      groups.push(urls.map((url, index) => {
        const match = pairs.find(pair => pair.url === url && pair.id === ids[index]);
        if (!match) fail('Import resource not proven by same-demand upload receipt');
        return match.output;
      }));
    }
    const used = groups.flat();
    assertNewImports(entries, used);
    return {args,outputs:used,outputGroups:groups};
  }
  const materialIds = new Set(recordsFor(entries, 'material_upload_from_url').flatMap(entry => collect(decodeResult(entry.response), 'material_id')).filter(id => typeof id === 'string'));
  if (request.tool === 'material_append_images') {
    if (args.confirmed !== true || !materialIds.has(args.material_id) || !Array.isArray(args.images) || !args.images.length) fail('Append requires confirmed same-demand imported material');
    const pairs = provenUploads(entries, request.upload_operation_ids, intake.summary.id);
    await checkImportGate(folder, request, intake.summary.id, pairs);
    const selected = args.images.map(image => {
      if (Object.keys(image).length !== 1 || typeof image.file_url !== 'string') fail('Append accepts only proven file_url');
      const matched = pairs.find(pair => pair.url === image.file_url);
      if (!matched) fail('Append resource not proven by same-demand reviewed upload');
      return matched.output;
    });
    assertNewImports(entries, selected);
    return {args,outputs:selected};
  }
  if (request.tool === 'material_attribution_update') {
    if (typeof args.confirmed !== 'boolean') fail('Attribution must explicitly choose preview or write');
    if (args.creation_type && args.creation_type !== 'derivative') fail('Only derivative attribution allowed');
    if (!args.material_ids?.length || args.material_ids.some(id => !materialIds.has(id))) fail('Attribution IDs must belong to recorded same-demand imports');
    if (args.designer_ids?.some(id => !intake.summary.assigneeIds?.includes(id))) fail('Designer ID is not a current intake assignee');
    if (args.designer_ids) {
      if (!Array.isArray(request.designer_members)) fail('Attribution requires verified designer_members with feishuUserId/username');
      const selected = request.designer_members.filter(member => !['罗晨钧','邹彬'].includes(member.username));
      if (selected.length !== args.designer_ids.length || selected.some(member => !member.username?.trim() || !args.designer_ids.includes(member.feishuUserId)) || args.designer_ids.some(id => !selected.some(member => member.feishuUserId === id))) fail('Attribution must retain exactly verified designers excluding 罗晨钧 and 邹彬');
    }
  } else if (request.tool === 'requirement_bind_materials') {
    if (args.confirmed !== true || !Array.isArray(args.items) || args.items.length !== 1) fail('Bind requires exactly one confirmed requirement');
    const item = args.items[0];
    if (item.requirement_id !== intake.summary.id || !item.material_ids?.length || item.material_ids.some(id => !materialIds.has(id))) fail('Wrong requirement or unproven material IDs');
  }
  return {args,outputs:[]};
}

async function checkImportGate(folder, request, requirementId, pairs) {
  if (request.gate_file === undefined) return;
  const outputs = await reviewedOutputs(folder, request, requirementId);
  if (pairs.some(({output}) => !outputs.some(current => current.sha256 === output.sha256 &&
    (!memberIdentity(output) || matchesMember(output, current))))) fail('Upload receipt does not prove one reviewed resource');
}

function assertNewImports(entries, outputs) {
  if (new Set(outputs.map(output => output.sha256)).size !== outputs.length) fail('Duplicate resource in request');
  const previous = ['material_upload_from_url', 'material_append_images'].flatMap(tool =>
    recordsFor(entries, tool).flatMap(registeredOutputs));
  if (previous.some(old => outputs.some(output => old.sha256 === output.sha256 || matchesMember(old, output)))) {
    fail('Resource already registered; reconcile the existing material');
  }
}

function registeredOutputs(entry) {
  const response = decodeResult(entry.response);
  const items = response?.results ?? response?.items;
  if (Array.isArray(entry.output_groups) && Array.isArray(items) &&
      items.every(item => Number.isInteger(item.item_index) &&
        item.item_index >= 0 && item.item_index < entry.output_groups.length)) {
    return entry.output_groups.flatMap((group, index) => {
      const item = items.find(result => result.item_index === index);
      return item?.success === false && !item.material_id ? [] : group;
    });
  }
  return (collect(response, 'material_id').length || response?.success === true) ? entry.outputs || [] : [];
}

/** Execute at most one business call; transport is injected for offline tests. */
export async function publish({ root=ROOT, code, operationId, request, transport }) {
  if (!/^[A-Z]+-\d{8}-\d+$/.test(code || '') || !UUID.test(operationId || '')) fail('Exact requirement code and operation UUID required');
  if (!request || typeof request !== 'object' || Array.isArray(request)) fail('Request must be an object');
  const envelopeFields = new Set(['tool','schema_version','arguments','gate_file','output_sha256','output_path','upload_operation_ids','designer_members','reupload_authorization_file','isolate_unknown_uploads']);
  for (const key of Object.keys(request)) if (!envelopeFields.has(key)) fail(`Unknown request control: ${key}`);
  if (!TOOLS.has(request.tool)) fail('Tool forbidden');
  if ('output_path' in request && (typeof request.output_path !== 'string' || request.tool !== 'material_r2_upload_from_bytes')) fail('output_path must identify an image upload');
  if ('isolate_unknown_uploads' in request && typeof request.isolate_unknown_uploads !== 'boolean') fail('isolate_unknown_uploads must be boolean');
  if (request.isolate_unknown_uploads && request.reupload_authorization_file) fail('Choose isolation or explicit reupload authorization');
  const folder = await realpath(join(root,'work','fireplay-production',code));
  const intake = await json(join(folder,'intake.json'));
  if (intake.summary?.code !== code || !UUID.test(intake.summary?.id || '') || intake.brief?.id !== intake.summary.id || intake.delivery?.id !== intake.summary.id) fail('Intake identity mismatch');
  if (intake.summary.canBindMaterials !== true) fail('Intake has no delivery permission');
  const lock = await open(join(folder,'publication.lock'),'wx');
  const journalPath = join(folder,'publication-journal.jsonl');
  try {
    const entries = await loadJournal(journalPath);
    if (entries.some(entry => entry.operation_id === operationId)) fail('Operation ID already recorded; replay forbidden');
    const unresolved = entries.filter(entry => entry.state === 'unknown').filter(entry => !entries.some(other => other.operation_id === entry.operation_id && other.state === 'received'));
    let reuploadAuthorization;
    if (request.reupload_authorization_file) {
      const authorizationPath = await realpath(resolve(folder, request.reupload_authorization_file));
      if (!inside(folder, authorizationPath)) fail('Reupload authorization outside requirement folder');
      const authorizationBytes = await readFile(authorizationPath);
      const authorization = JSON.parse(authorizationBytes.toString('utf8'));
      if (authorization.requirement_id !== intake.summary.id || authorization.code !== code || authorization.authorization_source !== 'explicit_current_user_message' || !authorization.user_request?.trim()) fail('Explicit same-requirement reupload authorization required');
      if (authorization.new_operations?.[operationId] !== request.tool) fail('Operation not included in authorized reupload plan');
      if (!Array.isArray(authorization.historical_unknown_operations) || unresolved.some(entry => !authorization.historical_unknown_operations.some(original => original.operation_id === entry.operation_id && original.request_sha256 === entry.request_sha256))) fail('Unapproved unknown operation exists');
      reuploadAuthorization = {path:relative(root, authorizationPath),sha256:sha256(authorizationBytes),user_request:authorization.user_request};
    } else if (unresolved.length && !request.isolate_unknown_uploads) fail('Unknown operation exists; inspect remote state, no automatic retry');
    let isolatedOutputs = [];
    if (unresolved.length && request.isolate_unknown_uploads) {
      isolatedOutputs = unresolved.map(entry => {
        const [output] = entry.outputs || [];
        if (entry.tool !== 'material_r2_upload_from_bytes' || entry.requirement_id !== intake.summary.id || entry.outputs?.length !== 1 ||
            typeof output?.path !== 'string' || !isAbsolute(output.path) || !inside(folder, output.path) ||
            !HASH.test(output.sha256)) fail('Only attributable unknown R2 uploads can be isolated');
        return output;
      });
    }
    const prepared = await prepareArguments({request,intake,folder,entries});
    if (isolatedOutputs.length) {
      let selected = prepared.outputs;
      if (request.tool === 'requirement_bind_materials' || request.tool === 'material_attribution_update') {
        const ids = prepared.args.material_ids ?? prepared.args.items[0].material_ids;
        const imports = recordsFor(entries, 'material_upload_from_url');
        selected = ids.flatMap(id => {
          const originating = imports.filter(entry => collect(decodeResult(entry.response), 'material_id').includes(id));
          if (!originating.length || originating.some(entry => !entry.outputs?.length)) fail('Missing scoped import provenance');
          return originating.flatMap(entry => entry.outputs);
        });
      }
      if (!selected.length || selected.some(output => isolatedOutputs.some(unknown =>
        unknown.path === output.path || unknown.sha256 === output.sha256 || matchesMember(unknown, output)))) {
        fail('Operation overlaps an unresolved upload; original write cannot be replayed');
      }
    }
    const {definition, evidence:contract} = await publicationContract(root, folder, request.tool);
    if (!definition || request.schema_version !== definition.schema_version) fail('Request does not pin cached schema version');
    validateSchema(prepared.args,definition.inputSchema);
    const live = decodeResult(await transport.call('tool_catalog',{tool:request.tool}));
    if (live.name !== request.tool || live.operation_class !== 'write' || live.schema_version !== definition.schema_version || live.invoke_via !== definition.invoke_via || JSON.stringify(live.inputSchema) !== JSON.stringify(definition.inputSchema)) fail('Live contract drift; refresh and review before write');
    const receiptPath = join(folder,`publication-${operationId}.json`);
    const receipt = await open(receiptPath,'wx');
    try {
      const entry = {operation_id:operationId,requirement_id:intake.summary.id,code,tool:request.tool,schema_version:definition.schema_version,contract,request_sha256:sha256(JSON.stringify(request)),outputs:prepared.outputs,state:'unknown'};
      if (prepared.outputGroups?.length > 1) entry.output_groups = prepared.outputGroups;
      if (reuploadAuthorization) entry.reupload_authorization = reuploadAuthorization;
      if (request.isolate_unknown_uploads) entry.isolated_unknown_upload_ids = unresolved.map(item => item.operation_id);
      if (request.tool === 'material_upload_from_url') {
        entry.request_file = `publication-request-${operationId}.json`;
        const original = await open(join(folder, entry.request_file), 'wx');
        try { await original.writeFile(JSON.stringify(request)); await original.sync(); }
        finally { await original.close(); }
      }
      await appendJournal(journalPath,entry);
      const parameters = definition.invoke_via === 'tool_write' ? {tool:request.tool,schema_version:definition.schema_version,arguments:prepared.args} : prepared.args;
      const response = await transport.call(definition.invoke_via,parameters);
      const received = {...entry,state:'received',response};
      const receiptBytes = JSON.stringify(received,null,2);
      await receipt.writeFile(receiptBytes); await receipt.sync();
      await appendJournal(journalPath,{...entry,state:'received',receipt_sha256:sha256(receiptBytes)});
      return {receiptPath,operationId,received:true,businessSuccessNotAssumed:true,isError:response?.isError===true};
    } finally { await receipt.close(); }
  } finally {
    await lock.close();
    const {unlink} = await import('node:fs/promises'); await unlink(join(folder,'publication.lock'));
  }
}

export async function connectProxy(root=ROOT) {
  const authDirectory = process.env.LOCALAPPDATA
    ? join(process.env.LOCALAPPDATA, 'Tanyue', 'FireplayMcpSuite')
    : join(homedir(), '.fireplay-mcp-suite');
  const authPath = join(authDirectory, platform() === 'win32' ? 'auth.dpapi' : 'auth.json');
  await stat(authPath); // Existence only; the approved proxy owns authentication.
  const child = spawn(process.execPath,[join(root,'plugins/fireplay-requirements-mcp/scripts/authhub-mcp-proxy.mjs'),'--label','ai-designer','--url','https://ai-designer-api.fireplay.ai/api/mcp/catalog/v1'],{stdio:['pipe','pipe','pipe'],windowsHide:true});
  let buffer=''; let nextId=1; const pending=new Map();
  const rejectAll = error => { for(const waiter of pending.values()) { clearTimeout(waiter.timer); waiter.reject(error); } pending.clear(); };
  child.stderr.on('data',()=>{}); // Do not relay proxy credentials/login links to stdout.
  child.on('error',rejectAll); child.on('exit',()=>rejectAll(new Error('Proxy exited; dispatched operation may be unknown')));
  child.stdin.on('error',rejectAll);
  child.stdout.on('data',chunk=>{
    buffer+=chunk.toString();
    try {
      while (buffer.length) {
        buffer=buffer.replace(/^(?:\s|\\n)+/,''); if (!buffer.length) break;
        let depth=0,quoted=false,escaped=false,end=-1;
        for(let index=0;index<buffer.length;index++) {
          const char=buffer[index];
          if(quoted) { if(escaped)escaped=false; else if(char==='\\')escaped=true; else if(char==='"')quoted=false; continue; }
          if(char==='"')quoted=true; else if(char==='{'||char==='[')depth++; else if(char==='}'||char===']'){depth--;if(depth===0){end=index+1;break;}}
        }
        if(end<0)break;
        const message=JSON.parse(buffer.slice(0,end)); buffer=buffer.slice(end);
        const waiter=pending.get(message.id);
        if(waiter){pending.delete(message.id);clearTimeout(waiter.timer);message.error?waiter.reject(new Error('MCP RPC rejected; inspect exact remote state')):waiter.resolve(message.result);}
      }
    } catch { rejectAll(new Error('Malformed MCP response; dispatched operation may be unknown')); }
  });
  const rpc=(method,params)=>new Promise((resolve,reject)=>{
    const id=nextId++;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error('MCP timeout; no automatic retry'));},120000);
    pending.set(id,{resolve,reject,timer});
    child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });
  const close=()=>{rejectAll(new Error('Transport closed'));child.stdin.end();child.kill();};
  try {
    await rpc('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'scoped-image-publication',version:'1.0.0'}});
    child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
    const listed=await rpc('tools/list',{});
    for(const name of ['tool_catalog','tool_write','material_upload_from_url','requirement_bind_materials']) if(!listed.tools?.some(tool=>tool.name===name)) fail(`Required registered entrypoint absent: ${name}`);
    return {call:(name,args)=>rpc('tools/call',{name,arguments:args}),close};
  } catch(error){close();throw error;}
}

async function main() {
  const values={};
  for(let index=2;index<process.argv.length;index+=2){const key=process.argv[index];if(!['--requirement','--request','--operation-id'].includes(key)||!process.argv[index+1]||key in values)fail('Expected --requirement CODE --request FILE --operation-id UUID');values[key]=process.argv[index+1];}
  if(Object.keys(values).length!==3)fail('All three CLI options required');
  const request=await json(resolve(values['--request']));
  const transport=await connectProxy();
  try { console.log(JSON.stringify(await publish({code:values['--requirement'],operationId:values['--operation-id'],request,transport}))); }
  finally {transport.close();}
}
if(process.argv[1] && pathToFileURL(resolve(process.argv[1])).href===import.meta.url) main().catch(error=>{console.error(JSON.stringify({status:'PUBLICATION_STOPPED',message:error.message}));process.exitCode=1;});
