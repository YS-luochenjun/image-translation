#!/usr/bin/env node
/** Scoped Fireplay image processing. Receipts are exclusive, append-only JSONL. */
import { createHash } from 'node:crypto';
import { readFile, open, realpath, lstat } from 'node:fs/promises';
import { dirname, basename, relative, resolve, isAbsolute } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { connectProxy, decodeResult, validateSchema } from './fireplay-mcp-publication.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CONTRACTS = 'work/fireplay-readonly/crop-mcp-contracts-20261104.json';
const CATALOG = 'work/fireplay-readonly/crop-mcp-generation-20261104.json';
const WRITE_TOOLS = new Set(['generation_crop_create', 'generation_translate_create', 'generation_image_resize', 'generation_asset_upload_url', 'generation_workspace_create']);
const READ_TOOLS = new Set(['generation_image_job_query', 'generation_workspace_list', 'generation_model_query', 'generation_quota']);
const PAID_TOOLS = new Set(['generation_crop_create', 'generation_translate_create']);
const fail = message => { throw new Error(message); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const json = async path => JSON.parse(await readFile(path, 'utf8'));
const inside = (base, target) => {
  const path = relative(base, target);
  return path !== '..' && !path.startsWith('../') && !path.startsWith('..\\') && !isAbsolute(path);
};

/** The contracts' UUID patterns explicitly permit nil/max sentinel UUIDs. */
function schemaWithUuidPatterns(schema) {
  if (Array.isArray(schema)) return schema.map(schemaWithUuidPatterns);
  if (!object(schema)) return schema;
  return Object.fromEntries(Object.entries(schema)
    .filter(([key]) => !(key === 'format' && schema.format === 'uuid' && schema.pattern))
    .map(([key, value]) => [key, schemaWithUuidPatterns(value)]));
}

/** Add oneOf support without changing the shared publication validator. */
export function validateArguments(value, schema, at = 'arguments') {
  if (schema.oneOf) {
    const matches = schema.oneOf.filter(branch => {
      try { validateArguments(value, branch, at); return true; } catch { return false; }
    });
    if (matches.length !== 1) fail(`${at}: oneOf requires exactly one matching shape`);
    const { oneOf, ...siblings } = schema;
    // Pinned model-query schema puts object properties only on its branches.
    if (siblings.type === 'object' && !siblings.properties) {
      if (!object(value)) fail(`${at}: expected object`);
      delete siblings.type;
    }
    validateSchema(value, siblings, at);
    return;
  }
  validateSchema(value, schemaWithUuidPatterns(schema), at);
  if (Array.isArray(value)) value.forEach((item, index) => validateArguments(item, schema.items || {}, `${at}[${index}]`));
  else if (object(value)) {
    for (const [key, item] of Object.entries(value)) {
      if (schema.properties?.[key]) validateArguments(item, schema.properties[key], `${at}.${key}`);
    }
  }
}

async function pinnedDefinition(root, tool) {
  const definitions = (await json(resolve(root, CONTRACTS))).map(entry => decodeResult(entry.result));
  if (definitions.length !== 9 || new Set(definitions.map(item => item.name)).size !== 9 || definitions.some(item => !WRITE_TOOLS.has(item.name) && !READ_TOOLS.has(item.name))) fail('Pinned receipt must contain exactly the nine scoped contracts');
  const definition = definitions.find(item => item.name === tool);
  const catalog = decodeResult((await json(resolve(root, CATALOG)))[0].result);
  const listed = catalog.tools?.find(item => item.name === tool);
  const operation = WRITE_TOOLS.has(tool) ? 'write' : 'read';
  if (!definition || !listed || definition.operation_class !== operation || definition.invoke_via !== `tool_${operation}` || listed.operation_class !== operation || listed.invoke_via !== definition.invoke_via || !definition.schema_version || !object(definition.inputSchema)) fail('Pinned contract/catalog identity mismatch');
  return definition;
}

async function scopedReceipt(root, receiptFile) {
  if (typeof receiptFile !== 'string' || !receiptFile) fail('Receipt file required');
  const project = await realpath(root);
  const path = resolve(project, receiptFile);
  const parent = await realpath(dirname(path));
  const resolved = resolve(parent, basename(path));
  let allowed = false;
  for (const folder of ['work/production', 'work/fireplay-readonly']) {
    const lexicalBase = resolve(project, folder);
    if (!inside(lexicalBase, path)) continue;
    const base = await realpath(lexicalBase);
    if (!inside(project, base)) fail('Output base escapes project');
    if (inside(base, resolved)) allowed = true;
  }
  if (!allowed) fail('Receipt must stay within project work/production or work/fireplay-readonly');
  try { await lstat(resolved); fail('Receipt already exists; replay forbidden, inspect prior outcome'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  return resolved;
}

function prepare(request, definition) {
  if (!object(request) || !object(request.arguments)) fail('Request must be an object with object arguments');
  for (const key of Object.keys(request)) if (!['tool', 'arguments', 'allow_paid', 'authorization'].includes(key)) fail(`Unknown request field: ${key}`);
  if ('allow_paid' in request && typeof request.allow_paid !== 'boolean') fail('allow_paid must be boolean');
  if ('authorization' in request && (typeof request.authorization !== 'string' || !request.authorization.trim())) fail('authorization must be a nonempty explicit authorization string');
  const args = structuredClone(request.arguments);
  if (definition.operation_class === 'write' && !Object.hasOwn(args, 'confirmed')) args.confirmed = false;
  validateArguments(args, definition.inputSchema);
  if (PAID_TOOLS.has(request.tool) && args.confirmed === true && (request.allow_paid !== true || !request.authorization?.trim())) fail('Paid creation requires allow_paid:true and explicit authorization string');
  if (request.tool === 'generation_asset_upload_url') {
    let url;
    try { url = new URL(args.source_url); } catch { fail('source_url must be a valid public HTTP(S) URL'); }
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) fail('source_url must be HTTP(S) without embedded credentials');
    if ([...url.searchParams.keys()].some(key => /token|secret|password|credential|authorization|api[_-]?key|signature|^sig$|^key$/i.test(key))) fail('source_url must not carry credentials in query parameters');
  }
  return args;
}

async function append(handle, entry) {
  await handle.writeFile(`${JSON.stringify({ ...entry, at: new Date().toISOString() })}\n`);
  await handle.sync();
}

/** One catalog refresh and at most one business call; transport injection is offline only. */
export async function processImage({ root = ROOT, request, receiptFile, transport }) {
  if (!object(request) || (!READ_TOOLS.has(request.tool) && !WRITE_TOOLS.has(request.tool))) fail('Tool forbidden: only nine pinned image-processing tools allowed');
  const definition = await pinnedDefinition(root, request.tool);
  const args = prepare(request, definition);
  const receiptPath = await scopedReceipt(root, receiptFile);
  const connection = transport || await connectProxy(root);
  try {
    const live = decodeResult(await connection.call('tool_catalog', { tool: request.tool }));
    if (live?.name !== request.tool || live.operation_class !== definition.operation_class || live.invoke_via !== definition.invoke_via || live.schema_version !== definition.schema_version || !isDeepStrictEqual(live.inputSchema, definition.inputSchema)) fail('Live contract drift; refresh and review pinned receipt before dispatch');
    const handle = await open(receiptPath, 'wx');
    try {
      const entry = {
        tool: request.tool, schema_version: definition.schema_version,
        invoke_via: live.invoke_via, operation_class: live.operation_class,
        request_sha256: createHash('sha256').update(JSON.stringify(request)).digest('hex'),
        arguments: args, allow_paid: request.allow_paid === true,
        ...(request.authorization ? { authorization: request.authorization } : {}),
      };
      await append(handle, { ...entry, state: 'unknown' });
      // No retry: timeout/error leaves the durable unknown recovery record.
      const response = await connection.call(live.invoke_via, { tool: request.tool, schema_version: live.schema_version, arguments: args });
      await append(handle, { ...entry, state: 'received', response, result: decodeResult(response) });
      return { receiptPath, state: 'received', isError: response?.isError === true, businessSuccessNotAssumed: true };
    } finally { await handle.close(); }
  } finally { if (!transport) connection.close(); }
}

export async function main(argv = process.argv.slice(2)) {
  const options = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--request', '--receipt'].includes(argv[i]) || !argv[i + 1] || Object.hasOwn(options, argv[i])) fail('Expected --request FILE --receipt FILE');
    options[argv[i]] = argv[i + 1];
  }
  if (Object.keys(options).length !== 2) fail('Expected --request FILE --receipt FILE');
  return processImage({ request: await json(resolve(options['--request'])), receiptFile: options['--receipt'] });
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then(result => console.log(JSON.stringify(result))).catch(error => {
    console.error(JSON.stringify({ status: 'IMAGE_PROCESSING_STOPPED', message: error.message }));
    process.exitCode = 1;
  });
}
