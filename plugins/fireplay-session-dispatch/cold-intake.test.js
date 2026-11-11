import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createDispatcher,MANIFEST,MANIFEST_SHA256} from './dispatch.js';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = await readFile(new URL('./fixtures/intake-index-20261103.json', import.meta.url));
const manifest = JSON.parse(bytes);
const cold = manifest.records.filter(row => !row.title && !row.session_title);
const normalized = path => path.split(String.fromCharCode(92)).filter(Boolean).join('/');
const action = (p,action) => ({action,requirement_id:p.requirement_id,snapshot_sha256:p.snapshot_sha256});
async function fixture(t, row=cold[0]) {
  const project = await mkdtemp(join(tmpdir(),'cold-intake-'));
  t.after(() => rm(project,{recursive:true,force:true}));
  await mkdir(join(project,'work/fireplay-production'),{recursive:true});
  await writeFile(join(project,MANIFEST),bytes);
  const handoff = normalized(row.handoff);
  await mkdir(join(project,`work/fireplay-production/${row.code}`),{recursive:true});
  await writeFile(join(project,handoff),'Frozen original handoff with old scope');
  const control = 'Latest control scope\nDo not infer old three\n';
  await writeFile(join(project,'work/fireplay-production/unified-control.md'),control);
  const calls=[],sessions=new Map();
  const controller={
    async create(r){calls.push(['create',r]);sessions.set(r.sessionId,{meta:{cwd:project},events:[]});return {sessionId:r.sessionId};},
    async rename(r){calls.push(['rename',r]);return {title:r.title};},
    async inspect(id){calls.push(['inspect',id]);return sessions.get(id);},
    async projections(){return {};},
    async prompt(r){calls.push(['prompt',r]);return {accepted:true};},
    async resume(){throw Error('must never resume');},
    async archive(){throw Error('must never archive');},
    async run(){throw Error('must never run');},
  };
  const options={controller,registry:{list:()=>[{id:'project',path:project}]},project};
  const dispatch=createDispatcher(options);
  const journal=join(project,'work/fireplay-production/session-dispatch',`${row.id}.json`);
  async function preview(){return dispatch.execute({action:'preview',requirement_id:row.id});}
  async function review(p) {
    const folder=`work/fireplay-production/${row.code}`;
    const evidence=[];
    for(const kind of ['source','delivery']) {
      const value={requirement_id:row.id,code:row.code,...(kind==='source'?{brief:'Actual nonempty brief',sources:[]}:{queried_at:new Date().toISOString(),materials:[],resources:[]})};
      const data=JSON.stringify(value),path=`${folder}/context-${kind}.json`;
      await writeFile(join(project,path),data);evidence.push({kind,path,sha256:sha(data)});
    }
    await writeFile(join(project,folder,'cold-intake-context-review.json'),JSON.stringify({reviewed_by:'parent',reviewed_at:new Date().toISOString(),requirement_id:row.id,code:row.code,manifest_sha256:MANIFEST_SHA256,snapshot_sha256:p.snapshot_sha256,evidence}));
  }
  return {project,row,calls,options,dispatch,journal,preview,review,control,sessions};
}
test('fixed reviewed manifest has exactly 37 unique IDs/codes and four empty-title candidates',()=>{
  assert.equal(sha(bytes),MANIFEST_SHA256);assert.equal(manifest.records.length,37);assert.equal(cold.length,4);
  assert.equal(new Set(manifest.records.map(r=>r.id)).size,37);
  assert.equal(new Set(manifest.records.map(r=>r.code)).size,37);
});
test('all four empty titles preview as code-only with frozen latest scope and no Host calls',async t=>{
  for(const row of cold){const f=await fixture(t,row),p=await f.preview();
    assert.equal(p.title,row.code);assert.equal(p.preview.title,row.code);assert.equal(p.preview.code,row.code);
    assert.equal(p.preview.control_sha256,sha(f.control));assert.ok(p.preview.prompt.endsWith(f.control));
    assert.ok(p.preview.prompt.includes('structured fields 空不等于正文为空'));
    assert.ok(p.preview.prompt.includes('latest exact brief'));assert.ok(p.preview.prompt.includes('real sources'));
    assert.ok(p.preview.prompt.includes('已有 delivery'));assert.ok(p.preview.prompt.includes('父只控不生产'));
    assert.ok(p.preview.prompt.includes('old three'));assert.ok(p.preview.prompt.includes(String.fromCharCode(10)));
    assert.equal(p.snapshot_sha256,sha(JSON.stringify(p.preview)));assert.deepEqual(f.calls,[]);
  }
});
test('nonempty platform title also becomes code-only only for a new original',async t=>{
  const row=manifest.records.find(r=>r.title),f=await fixture(t,row),p=await f.preview();assert.equal(p.title,row.code);
});
test('arbitrary sid/prompt, unknown allowlist ID and malformed UUID cannot preview',async t=>{
  const f=await fixture(t);
  for(const extra of [{session_id:'arbitrary'},{prompt:'arbitrary'},{title:'arbitrary'}])await assert.rejects(f.dispatch.execute({action:'preview',requirement_id:f.row.id,...extra}),/Arbitrary/);
  await assert.rejects(f.dispatch.execute({action:'preview',requirement_id:'11111111-1111-1111-1111-111111111111'}),/reviewed intake/);
  await assert.rejects(f.dispatch.execute({action:'preview',requirement_id:'------------------------------------'}),/identity/);
  assert.deepEqual(f.calls,[]);
});
test('create requires exact user snapshot hash and parent source/delivery evidence, then only creates and renames',async t=>{
  const f=await fixture(t),p=await f.preview();
  await assert.rejects(f.dispatch.execute({...action(p,'create'),snapshot_sha256:'bad'}),/hash/);
  await assert.rejects(f.dispatch.execute(action(p,'create')),/ENOENT/);assert.deepEqual(f.calls,[]);
  await f.review(p);assert.equal((await f.dispatch.execute(action(p,'create'))).stage,'titled');
  assert.deepEqual(f.calls.map(c=>c[0]),['create','rename']);assert.equal(f.calls[1][1].title,f.row.code);
});
test('changed source evidence and cross-requirement content fail closed before create',async t=>{
  const f=await fixture(t),p=await f.preview();await f.review(p);
  const path=join(f.project,`work/fireplay-production/${f.row.code}/context-source.json`);
  await writeFile(path,'{}');await assert.rejects(f.dispatch.execute(action(p,'create')),/evidence changed/);assert.deepEqual(f.calls,[]);
});
test('changed frozen manifest fails preview/create and never replaces journal',async t=>{
  const f=await fixture(t),p=await f.preview(),original=await readFile(f.journal);
  await writeFile(join(f.project,MANIFEST),'{}');
  await assert.rejects(f.preview(),/manifest changed/);await assert.rejects(f.dispatch.execute(action(p,'create')),/manifest changed/);
  assert.deepEqual(await readFile(f.journal),original);assert.deepEqual(f.calls,[]);
});
test('new scope changes report stale without rewriting or taking over existing titled journal',async t=>{
  const f=await fixture(t),p=await f.preview();await f.review(p);await f.dispatch.execute(action(p,'create'));
  const original=await readFile(f.journal);await writeFile(join(f.project,'work/fireplay-production/unified-control.md'),'changed scope');
  assert.equal((await f.preview()).current_input_matches,false);
  await assert.rejects(f.dispatch.execute(action(p,'dispatch')),/scope stale/);
  assert.deepEqual(await readFile(f.journal),original);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);
});
test('beforeDispatch failure prevents prompt and stage mutation after fresh observation',async t=>{
  const f=await fixture(t),p=await f.preview();await f.review(p);await f.dispatch.execute(action(p,'create'));
  const original=await readFile(f.journal),dispatch=createDispatcher({...f.options,beforeDispatch:async()=>{throw Error('capacity gate');}});
  await assert.rejects(dispatch.execute(action(p,'dispatch')),/capacity gate/);
  assert.equal(f.calls.at(-1)[0],'inspect');assert.deepEqual(await readFile(f.journal),original);
});
test('legacy accepted snapshot/hash/prompt/sid/rpc remain immutable and inspect does not adopt new scope',async t=>{
  const row=manifest.records.find(r=>r.title),f=await fixture(t,row);
  const snapshot={requirement_id:row.id,code:row.code,title:row.title,prompt:'legacy accepted exact prompt',handoff:row.handoff,manifest_sha256:MANIFEST_SHA256};
  const state={snapshot,snapshot_sha256:sha(JSON.stringify(snapshot)),session_id:'session-11111111-1111-1111-1111-111111111111',request_id:'22222222-2222-2222-2222-222222222222',stage:'accepted'};
  await mkdir(join(f.project,'work/fireplay-production/session-dispatch'),{recursive:true});await writeFile(f.journal,JSON.stringify(state));
  f.sessions.set(state.session_id,{meta:{cwd:f.project},events:[]});const original=await readFile(f.journal);
  const p=await f.preview();assert.equal(p.snapshot_sha256,state.snapshot_sha256);assert.equal(p.preview.prompt,snapshot.prompt);
  assert.equal((await f.dispatch.execute(action(p,'inspect'))).stage,'accepted');
  assert.deepEqual(await readFile(f.journal),original);assert.equal((await f.dispatch.execute(action(p,'create'))).skipped,true);
});
test('all pre-existing legacy stages keep frozen bytes and IDs during preview',async t=>{
  for(const stage of ['previewed','creating','created','titled','create_unknown','dispatching','dispatch_unknown','accepted']) {
    const row=manifest.records.find(r=>r.title),f=await fixture(t,row);
    const snapshot={requirement_id:row.id,code:row.code,title:row.title,prompt:'old frozen prompt',handoff:row.handoff,manifest_sha256:MANIFEST_SHA256};
    const state={snapshot,snapshot_sha256:sha(JSON.stringify(snapshot)),session_id:'session-11111111-1111-1111-1111-111111111111',request_id:'22222222-2222-2222-2222-222222222222',stage};
    await mkdir(join(f.project,'work/fireplay-production/session-dispatch'),{recursive:true});await writeFile(f.journal,JSON.stringify(state));
    const original=await readFile(f.journal),p=await f.preview();
    assert.equal(p.stage,stage);assert.equal(p.preview.prompt,snapshot.prompt);assert.equal(p.session_id,state.session_id);
    assert.equal(p.request_id,state.request_id);assert.equal(p.snapshot_sha256,state.snapshot_sha256);
    assert.deepEqual(await readFile(f.journal),original);assert.deepEqual(f.calls,[]);
  }
});
test('matching evidence SHA cannot disguise another requirement or missing delivery fields',async t=>{
  for(const kind of ['source','delivery']) {
    const f=await fixture(t),p=await f.preview();await f.review(p);
    const folder=`work/fireplay-production/${f.row.code}`,path=`${folder}/context-${kind}.json`;
    const data=JSON.stringify({requirement_id:manifest.records[0].id,code:f.row.code,brief:'wrong requirement'});
    await writeFile(join(f.project,path),data);
    const reviewPath=join(f.project,folder,'cold-intake-context-review.json');
    const review=JSON.parse(await readFile(reviewPath,'utf8'));review.evidence.find(item=>item.kind===kind).sha256=sha(data);
    await writeFile(reviewPath,JSON.stringify(review));
    await assert.rejects(f.dispatch.execute(action(p,'create')),/exact identity or content missing/);assert.deepEqual(f.calls,[]);
  }
});
test('legacy empty-title created journal remains stale and cannot automatically take over',async t=>{
  const f=await fixture(t);const snapshot={requirement_id:f.row.id,code:f.row.code,title:'',prompt:'legacy',handoff:f.row.handoff,manifest_sha256:MANIFEST_SHA256};
  const state={snapshot,snapshot_sha256:sha(JSON.stringify(snapshot)),session_id:'session-11111111-1111-1111-1111-111111111111',request_id:'22222222-2222-2222-2222-222222222222',stage:'created'};
  await mkdir(join(f.project,'work/fireplay-production/session-dispatch'),{recursive:true});await writeFile(f.journal,JSON.stringify(state));const original=await readFile(f.journal);
  await assert.rejects(f.preview(),{code:'exact_title_unavailable'});
  await assert.rejects(f.dispatch.execute(action({...state,requirement_id:f.row.id},'create')),{code:'exact_title_unavailable'});
  assert.deepEqual(await readFile(f.journal),original);assert.deepEqual(f.calls,[]);
});
