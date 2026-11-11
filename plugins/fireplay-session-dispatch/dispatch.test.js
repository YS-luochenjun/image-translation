import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createDispatcher} from './dispatch.js';
const ids=['11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222'];
async function fixture(t,overrides={}){
  const project=await mkdtemp(join(tmpdir(),'fireplay-dispatch-'));
  t.after(()=>rm(project,{recursive:true,force:true}));
  await mkdir(join(project,'input'));
  await writeFile(join(project,'input/handoff.txt'),'Authorized production handoff with requirement identities');
  const manifest=JSON.stringify({records:ids.map((id,i)=>({id,code:`SHE-${i}`,title:'翻译',session_title:'翻译',handoff:'input/handoff.txt'}))});
  await writeFile(join(project,'manifest.json'),manifest);
  const sessions=new Map(),calls=[];
  const controller={
    async create(request){calls.push(['create',request]);sessions.set(request.sessionId,{meta:{cwd:project},events:[]});return {sessionId:request.sessionId};},
    async rename(request){calls.push(['rename',request]);sessions.get(request.sessionId).events.push({type:'session/title',data:{title:request.title}});return {title:request.title,seq:1};},
    async inspect(id){if(!sessions.has(id))throw Error('missing');return sessions.get(id);},
    async projections(){return {kind:'sequenced',asOfSeq:2,values:{title:'翻译'}};},
    async prompt(request){calls.push(['prompt',request]);sessions.get(request.sessionId).events.push({type:'user/message',data:{source:{rpcId:request.requestId}}});return {accepted:true};},
    ...overrides,
  };
  const options={controller,registry:{list:()=>[{id:'existing-project',path:project}]},project,
    manifest:'manifest.json',manifestSha256:createHash('sha256').update(manifest).digest('hex')};
  return {project,sessions,calls,controller,options,dispatch:createDispatcher(options)};
}
const args=(preview,action)=>({action,requirement_id:preview.requirement_id,snapshot_sha256:preview.snapshot_sha256});
test('duplicate exact titles create distinct ordinary sessions and dispatch once across restart',async t=>{
  const f=await fixture(t);
  const previews=await Promise.all(ids.map(requirement_id=>f.dispatch.execute({action:'preview',requirement_id})));
  assert.notEqual(previews[0].session_id,previews[1].session_id);
  for(const p of previews){assert.equal((await f.dispatch.execute(args(p,'create'))).stage,'titled');assert.equal((await f.dispatch.execute(args(p,'dispatch'))).stage,'accepted');}
  const restarted=createDispatcher(f.options);
  for(const p of previews){await restarted.execute(args(p,'create'));await restarted.execute(args(p,'dispatch'));}
  assert.equal(f.calls.filter(c=>c[0]==='create').length,2);
  assert.equal(f.calls.filter(c=>c[0]==='prompt').length,2);
  assert.ok(f.calls.filter(c=>c[0]==='rename').every(c=>c[1].title==='翻译'));
});
test('unreviewed hash and changed intake cannot create or dispatch',async t=>{
  const f=await fixture(t);const p=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  await assert.rejects(f.dispatch.execute({...args(p,'create'),snapshot_sha256:'bad'}),/hash/);
  await writeFile(join(f.project,'manifest.json'),'{}');
  await assert.rejects(f.dispatch.execute(args(p,'create')),/manifest changed/);
  assert.equal(f.calls.length,0);
});
test('persisted IDs and phase exist before host mutation; unknown creation is not retried',async t=>{
  const f=await fixture(t);const p=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  f.controller.create=async request=>{const state=JSON.parse(await readFile(join(f.project,'work/fireplay-production/session-dispatch',`${ids[0]}.json`),'utf8'));assert.equal(state.stage,'creating');assert.equal(state.session_id,request.sessionId);throw Error('unknown connection result');};
  assert.equal((await f.dispatch.execute(args(p,'create'))).stage,'create_unknown');
  const restarted=createDispatcher(f.options);
  assert.equal((await restarted.execute(args(p,'create'))).skipped,true);
});
test('unknown prompt recovers only from exact durable request evidence and never replays',async t=>{
  const f=await fixture(t);const p=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  await f.dispatch.execute(args(p,'create'));
  f.controller.prompt=async request=>{f.sessions.get(request.sessionId).events.push({type:'user/message',data:{source:{rpcId:request.requestId}}});throw Error('response lost');};
  assert.equal((await f.dispatch.execute(args(p,'dispatch'))).stage,'dispatch_unknown');
  assert.equal((await f.dispatch.execute(args(p,'inspect'))).stage,'accepted');
  assert.equal((await f.dispatch.execute(args(p,'dispatch'))).skipped,true);
});
test('title normalization prevents dispatch and unknown IDs are refused',async t=>{
  const f=await fixture(t,{rename:async()=>({title:'different',seq:1})});
  const p=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  assert.equal((await f.dispatch.execute(args(p,'create'))).stage,'create_unknown');
  assert.equal((await f.dispatch.execute(args(p,'dispatch'))).skipped,true);
  await assert.rejects(f.dispatch.execute({action:'preview',requirement_id:'33333333-3333-3333-3333-333333333333'}),/reviewed intake/);
});
test('preview freezes handoff and reports changed input without silently rewriting it',async t=>{
  const f=await fixture(t);const p=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  await writeFile(join(f.project,'input/handoff.txt'),'changed');
  const next=await f.dispatch.execute({action:'preview',requirement_id:ids[0]});
  assert.equal(next.current_input_matches,false);assert.equal(next.snapshot_sha256,p.snapshot_sha256);
  assert.equal(next.preview.prompt,p.preview.prompt);
});
