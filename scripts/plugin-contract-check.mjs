// Package/retirement checks only. Active behavior is covered by *.test.js.
import assert from 'node:assert/strict';
import { readFile, stat } from 'node:fs/promises';
import vm from 'node:vm';

const retired = new URL('../plugins/image-review-desk/', import.meta.url);
let loaded;
vm.runInNewContext(await readFile(new URL('client.js', retired), 'utf8'), {
  window: { __ModuleLoader__: { load: entry => { loaded = entry; } } },
});
assert.equal(loaded.id, '@local/image-review-desk');
// No host API is needed and no sidebar or session effect may be registered.
loaded.factory().apply({});

const active = new URL('../plugins/image-review-workbench/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('package.json', active), 'utf8'));
assert.equal(manifest.name, '@local/image-review-ui');
assert.equal(manifest.version, '5.2.1');
assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
assert.equal(manifest.dependencies, undefined);
for (const target of Object.values(manifest.exports).filter(value => !value.includes('*'))) {
  assert.ok((await stat(new URL(target, active))).isFile(), target);
  assert.ok(manifest.files.includes(target.replace(/^\.\//, '')), target);
}
const client = await readFile(new URL(manifest.exports['./client'], active), 'utf8');
assert.equal(client, await readFile(new URL('client.js', active), 'utf8'));
assert.match(client, /@local\/image-review-ui/);
for (const language of ['en', 'zh']) {
  const locale = JSON.parse(await readFile(new URL(`locale/${language}.json`, active), 'utf8'));
  assert.ok(locale.meta.title && locale.meta.description);
}
console.log('PASS: retired client is inert; active 5.2.1 exports, package files and client aliases agree. Live installation not checked.');
