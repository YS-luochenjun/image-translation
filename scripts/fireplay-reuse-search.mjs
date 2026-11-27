#!/usr/bin/env node
import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, open, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs, promisify } from 'node:util';

import { definition, read } from './fireplay-discover-translations.mjs';
import { connectProxy } from './fireplay-mcp-publication.mjs';

const run = promisify(execFile);
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const referenceLimit = 15_000_000;
const defaultBudgetMs = 45_000;
const digest = /^[0-9a-f]{64}$/;

export async function supplementReuse(plan, { invoke, verify, readReference }) {
  if (plan.mode !== 'normal' || !plan.remote) return { state: 'not_needed' };
  const args = { ...plan.remote.image_lookup.arguments };
  if (!args.image_url) args.image_base64 = await readReference(plan.remote.reference_file);
  const imageSearch = await invoke('material_search_by_image', args);
  if (!Array.isArray(imageSearch.items) || imageSearch.items.length > args.limit) {
    throw new Error('Unexpected Fireplay visual search result scope');
  }
  const ids = [...new Set(imageSearch.items
    .filter(item => item?.canView === true && item.matchedResourceId && item.id)
    .map(item => item.id))];
  if (!ids.length) return { state: 'no_visual_candidates' };
  const resources = await invoke('material_resources_query', { material_ids: ids });
  const verified = await verify({ source_sha256: plan.remote.source_sha256, image_search: imageSearch, resources: [resources] });
  if (!verified.requirement_lookup) {
    return { state: 'no_verified_original_in_sample', unresolved_resources: verified.unverified_resource_ids };
  }
  const panel = await invoke('requirement_list', verified.requirement_lookup.arguments);
  const expected = verified.verified_source_material_ids;
  const returned = panel.query_meta?.filters_applied?.material_ids;
  if (panel.view !== 'summary' || !Array.isArray(panel.items) || !Array.isArray(returned) ||
      JSON.stringify([...returned].sort()) !== JSON.stringify([...expected].sort())) {
    throw new Error('Fireplay panel scope differs from the verified original material IDs');
  }
  const history = panel.items.filter(item => item?.code !== plan.code && item?.status === 'done' &&
    Array.isArray(item.matched_relations) && item.matched_relations.includes('source') && item.id).slice(0, 5);
  return {
    state: 'searched',
    original_material_ids: expected,
    requirements: history.map(({ id, code, matched_relations }) => ({ id, code, matched_relations })),
    partial: Boolean(panel.next_cursor || panel.next_offset || panel.query_meta?.source_limit_reached ||
      panel.query_meta?.total_exact === false || verified.unverified_resource_ids?.length),
    delivery_lookup: history.length ? { tool: 'requirement_list',
      arguments: { scope: 'all', view: 'delivery', requirement_ids: history.map(item => item.id), limit: 5 } } : null
  };
}

async function readCached(cache, binding) {
  try {
    const saved = JSON.parse(await readFile(cache, 'utf8'));
    if (!Object.keys(binding).every(key => saved[key] === binding[key]) || !saved.result?.state) {
      throw new Error('Reuse receipt identity mismatch');
    }
    return { ...saved.result, cached: true };
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return null;
  }
}

async function searchWithDeadline(plan, python, pythonArgs) {
  const folder = await mkdtemp(join(tmpdir(), 'fireplay-reuse-'));
  const started = Date.now();
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error('Fireplay supplemental search deadline reached'));
      controller.abort();
    }, defaultBudgetMs);
  });
  const remote = async () => {
    const transport = await connectProxy(root, { signal: controller.signal });
    return supplementReuse(plan, {
      invoke: async (name, args) => read(transport, await definition(transport, name), args),
      verify: async evidence => {
        const path = join(folder, 'lookup.json');
        await writeFile(path, JSON.stringify(evidence), { mode: 0o600 });
        const remaining = defaultBudgetMs - (Date.now() - started);
        if (remaining <= 0) throw new Error('Fireplay supplemental search deadline reached');
        const result = await run(python, [...pythonArgs, '--lookup', path],
          { cwd: root, timeout: remaining, signal: controller.signal, maxBuffer: 4 * 1024 * 1024 });
        return JSON.parse(result.stdout).remote;
      },
      readReference: async path => {
        if ((await stat(path)).size > referenceLimit) throw new Error('Reference exceeds the image search byte limit');
        const bytes = await readFile(path);
        if (bytes.length > referenceLimit) throw new Error('Reference exceeds the image search byte limit');
        return bytes.toString('base64');
      }
    });
  };
  let supplement;
  try {
    supplement = await Promise.race([remote(), deadline]);
  } catch {
    supplement = { state: Date.now() - started >= defaultBudgetMs ? 'timed_out' : 'unavailable' };
  } finally {
    clearTimeout(timer);
    controller.abort();
    await rm(folder, { recursive: true, force: true });
  }
  return { ...supplement, elapsed_seconds: (Date.now() - started) / 1000 };
}

async function main() {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    python: { type: 'string', default: process.platform === 'win32' ? 'python' : 'python3' },
    image: { type: 'string' },
    rework: { type: 'boolean' }
  } });
  if (positionals.length !== 2 || (values.image && !/^[1-9]\d*$/.test(values.image))) {
    throw new Error('Usage: node scripts/fireplay-reuse-search.mjs CODE DELIVERY_STATE [--python PATH] [--image N] [--rework]');
  }
  const [code, state] = positionals;
  const pythonArgs = [join(root, 'bin/it.py'), 'fireplay-reuse-plan', code, resolve(state)];
  if (values.image) pythonArgs.push('--remote-image', values.image);
  if (values.rework) pythonArgs.push('--rework');
  const local = await run(values.python, pythonArgs, { cwd: root, timeout: defaultBudgetMs, maxBuffer: 4 * 1024 * 1024 });
  const plan = JSON.parse(local.stdout);
  if (plan.mode !== 'normal' || !plan.remote) {
    console.log(JSON.stringify({ ...plan, fireplay_supplement: { state: 'not_needed' } }, null, 2));
    return;
  }
  const fingerprint = [plan.content_version, plan.size_spec_sha256, plan.remote.source_sha256];
  if (!fingerprint.every(value => digest.test(value))) throw new Error('Preflight lacks frozen input identities');
  const scratch = join(root, 'work', 'fireplay-production', code, 'scratch');
  await mkdir(scratch, { recursive: true });
  if (await realpath(scratch) !== scratch) throw new Error('Search receipts require a local scratch directory');
  const cache = join(scratch, `reuse-search-${fingerprint[0].slice(0, 12)}-${fingerprint[1].slice(0, 12)}-${fingerprint[2].slice(0, 12)}.json`);
  const binding = { content_version: fingerprint[0], size_spec_sha256: fingerprint[1], source_sha256: fingerprint[2] };
  const saved = await readCached(cache, binding);
  if (saved) {
    console.log(JSON.stringify({ ...plan, fireplay_supplement: saved }, null, 2));
    return;
  }
  let lock;
  try {
    lock = await open(cache + '.lock', 'wx', 0o600);
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    console.log(JSON.stringify({ ...plan, fireplay_supplement: { state: 'search_in_progress' } }, null, 2));
    return;
  }
  const pending = cache + '.tmp';
  let result;
  try {
    result = await readCached(cache, binding);
    if (!result) {
      result = await searchWithDeadline(plan, values.python, pythonArgs);
      await writeFile(pending, JSON.stringify({ ...binding, checked_at: new Date().toISOString(), result }), { mode: 0o600 });
      await rename(pending, cache);
    }
  } finally {
    await lock.close();
    await rm(pending, { force: true });
    await rm(cache + '.lock', { force: true });
  }
  console.log(JSON.stringify({ ...plan, fireplay_supplement: result }, null, 2));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(() => {
    console.error(JSON.stringify({ state: 'local_preflight_failed' }));
    process.exitCode = 1;
  });
}
