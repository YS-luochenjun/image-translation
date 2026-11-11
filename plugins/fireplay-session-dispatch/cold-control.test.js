import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createControl,createDispatchExecutor} from './control.js';
import {createDispatcher,MANIFEST,MANIFEST_SHA256} from './dispatch.js';
const hash=v=>createHash('sha256').update(v).digest('hex');
const manifestBytes=await readFile(new URL('./fixtures/intake-index-20261103.json',import.meta.url));
const records=JSON.parse(manifestBytes).records,coldRows=records.filter(r=>!r.title);
const root='work/fireplay-production',scope='Current explicit per-requirement scope\nPreserve accepted delivery members.\n';
const request=(p,action='dispatch')=>({action,requirement_id:p.requirement_id,snapshot_sha256:p.snapshot_sha256});
async function fixture(t,{running=0,targets=1}={}){
 const project=await mkdtemp(join(tmpdir(),'cold-control-'));t.after(()=>rm(project,{recursive:true,force:true}));
 const put=async(path,value)=>{await mkdir(resolve(project,path,'..'),{recursive:true});await writeFile(join(project,path),typeof value==='string'||Buffer.isBuffer(value)?value:JSON.stringify(value)+'\n');};
 await put(MANIFEST,manifestBytes);await put(root+'/unified-control.md',scope);
 const sessions=new Map(),calls=[],flags={unknown:false,mutation:null,inspectCount:0};
 const registry={archivedSessionIds:[],list:()=>[{id:'same-project',path:project,sessionIds:[...sessions.keys()]}]};
 const controller={
  async create(args){calls.push(['create',args]);sessions.set(args.sessionId,{cwd:project,title:null,status:null,events:[],inbox:[],parent:null});return {sessionId:args.sessionId};},
  async rename(args){calls.push(['rename',args]);const s=sessions.get(args.sessionId);s.title=args.title;s.events.push({type:'session/title',seq:1,data:{title:args.title,messageSeqs:[],source:{kind:'user'}}});return {title:args.title,seq:1};},
  async inspect(id){flags.inspectCount++;if(flags.mutation)await flags.mutation(flags.inspectCount,id);const s=sessions.get(id);return s?{meta:{cwd:s.cwd,parentSessionId:s.parent},events:s.events}:null;},
  async projections({sessionId}){const s=sessions.get(sessionId);return {kind:'sequenced',values:{title:s.title,subagent:null,inbox:{'next-turn':s.inbox,'next-step':[]}}};},
  async prompt(args){calls.push(['prompt',args]);const s=sessions.get(args.sessionId);s.status='running';s.events.push({type:'user/message',seq:2,data:{source:{rpcId:args.requestId}}});if(flags.unknown)throw Error('unknown response');return {accepted:true};},
 };
 const options={project,registry,controller,getStatus:id=>sessions.get(id)?.status??null};
 const control=createControl(options),dispatcher=createDispatcher({...options,beforeDispatch:args=>control.checkColdDispatch(args)}),execute=createDispatchExecutor({dispatcher,control,project});
 const previews=[];
 for(const row of coldRows.slice(0,targets)){
  await put(row.handoff.split('\\').filter(Boolean).join('/'),'Historical handoff must remain overridden by current scope');
  const p=await execute({action:'preview',requirement_id:row.id});previews.push(p);
  const evidence=[];
  for(const kind of ['source','delivery']){const path=`${root}/${row.code}/cold-${kind}.json`,value={requirement_id:row.id,code:row.code,...(kind==='source'?{brief:'Actual source brief'}:{queried_at:new Date().toISOString(),materials:[],resources:[]})};await put(path,value);evidence.push({kind,path,sha256:hash(await readFile(join(project,path)))});}
  await put(`${root}/${row.code}/cold-intake-context-review.json`,{reviewed_by:'parent',reviewed_at:new Date().toISOString(),requirement_id:row.id,code:row.code,manifest_sha256:MANIFEST_SHA256,snapshot_sha256:p.snapshot_sha256,evidence});
  assert.equal((await execute(request(p,'create'))).stage,'titled');
 }
 for(const row of records.filter(r=>r.title).slice(0,running)){
  const sid='session-'+randomUUID(),rpc=randomUUID(),snapshot={requirement_id:row.id,code:row.code,title:row.code,manifest_sha256:MANIFEST_SHA256};sessions.set(sid,{cwd:project,title:row.code,status:'running',inbox:[],events:[{type:'user/message',seq:1,data:{source:{rpcId:rpc}}}],parent:null});await put(`${root}/session-dispatch/${row.id}.json`,{snapshot,snapshot_sha256:hash(JSON.stringify(snapshot)),session_id:sid,request_id:rpc,stage:'accepted'});
 }
 const review=async({ids=previews.map(p=>p.requirement_id),hashes=Object.fromEntries(previews.map(p=>[p.requirement_id,p.snapshot_sha256])),revision=hash(scope)}={})=>put(root+'/session-control-review.json',{control_sha256:revision,requirement_ids:ids,cold_dispatch_ids:ids,cold_snapshot_sha256:hashes});
 return {project,put,sessions,calls,flags,registry,controller,control,dispatcher,execute,previews,review};
}
test('cold initial dispatch cannot bypass parent review, then sends exact frozen scope once',async t=>{const f=await fixture(t),p=f.previews[0];await assert.rejects(f.execute(request(p)),/new parent reviewed/);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);await f.review();const accepted=await f.execute(request(p));assert.equal(accepted.stage,'accepted');const sent=f.calls.filter(c=>c[0]==='prompt');assert.equal(sent.length,1);assert.ok(sent[0][1].content[0].text.endsWith(scope));assert.equal(sent[0][1].sessionId,p.session_id);});
test('four actual running plus one cold rejects fifth without prompt',async t=>{const f=await fixture(t,{running:4});await f.review();await assert.rejects(f.execute(request(f.previews[0])),/Four active\/reserved\/wake\/cold/);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);});
test('concurrent cold admissions cannot become fourth and fifth when both are reserved',async t=>{const f=await fixture(t,{running:3,targets:2});await f.review();const result=await Promise.allSettled(f.previews.map(p=>f.execute(request(p))));assert.ok(result.every(v=>v.status==='rejected'));assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);});
test('two cold admissions with two running share four slots and retain independent once-only journals',async t=>{const f=await fixture(t,{running:2,targets:2});await f.review();const result=await Promise.all(f.previews.map(p=>f.execute(request(p))));assert.ok(result.every(v=>v.stage==='accepted'));assert.equal(f.calls.filter(c=>c[0]==='prompt').length,2);});
test('wrong snapshot, stale current scope, other reviewed ID and arbitrary prompt are refused',async t=>{const f=await fixture(t),p=f.previews[0];await f.review({hashes:{[p.requirement_id]:'0'.repeat(64)}});await assert.rejects(f.execute(request(p)),/new parent reviewed/);await f.review();await assert.rejects(f.execute({...request(p),prompt:'arbitrary'}),/Invalid cold dispatch/);await f.put(root+'/unified-control.md','Changed user scope');await assert.rejects(f.execute(request(p)),/current reviewed control/);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);});
test('cold no-journal, archive, foreign cwd, parent, nonempty inbox and existing user prompt fail closed',async t=>{const f=await fixture(t),p=f.previews[0];await f.review();const s=f.sessions.get(p.session_id);for(const [key,value]of [['cwd',tmpdir()],['parent','other'],['inbox',[{}]],['status','running']]){const old=s[key];s[key]=value;await assert.rejects(f.execute(request(p)));s[key]=old;}s.events.push({type:'user/message',seq:4,data:{source:{rpcId:randomUUID()}}});await assert.rejects(f.execute(request(p)),/no-previous-prompt/);s.events.pop();f.registry.archivedSessionIds.push(p.session_id);await assert.rejects(f.execute(request(p)),/archived/);const unknown={action:'dispatch',requirement_id:coldRows[1].id,snapshot_sha256:'0'.repeat(64)};await assert.rejects(f.execute(unknown),e=>e.code==='ENOENT');assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);});
test('beforeDispatch second check closes changed-review window after dispatcher observed',async t=>{const f=await fixture(t),p=f.previews[0];await f.review();f.flags.inspectCount=0;f.flags.mutation=async n=>{if(n===2)await f.review({revision:'0'.repeat(64)});};await assert.rejects(f.execute(request(p)),/new parent reviewed/);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,0);const state=JSON.parse(await readFile(join(f.project,root+'/session-dispatch/'+p.requirement_id+'.json')));assert.equal(state.stage,'titled');});
test('unknown first dispatch never replays; only inspect can recover positive rpcId',async t=>{const f=await fixture(t),p=f.previews[0];await f.review();f.flags.unknown=true;assert.equal((await f.execute(request(p))).stage,'dispatch_unknown');await assert.rejects(f.execute(request(p)),/confirmed titled/);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,1);const result=await f.execute(request(p,'inspect'));assert.equal(result.stage,'accepted');assert.equal(result.durable_prompt_observed,true);assert.equal(f.calls.filter(c=>c[0]==='prompt').length,1);});
