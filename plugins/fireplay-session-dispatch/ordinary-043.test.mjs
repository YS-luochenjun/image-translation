import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {createOrdinary043,CODE,REQUIREMENT} from './ordinary-043.js';
const sha=text=>createHash('sha256').update(text).digest('hex');
async function fixture(t,{existing=[],capacity=0,failCreate=false,failPrompt=false,failRename=false}={}){
 const project=await mkdtemp(join(tmpdir(),'dsh043-'));t.after(()=>rm(project,{recursive:true,force:true}));
 const folder=join(project,'work/fireplay-production',CODE);await mkdir(folder,{recursive:true});
 await writeFile(join(folder,'HANDOFF.md'),'stopped worker: no inflight writes');
 const plan={version:1,approved:true,requirement_id:REQUIREMENT,code:CODE,worker_stopped:true,inflight_unknown:false,handoff:`work/fireplay-production/${CODE}/HANDOFF.md`,handoff_sha256:sha('stopped worker: no inflight writes'),prompt:`Authorized actual production ${CODE} ${REQUIREMENT}`};
 const bytes=JSON.stringify(plan);await writeFile(join(folder,'ordinary-session-plan.json'),bytes);
 const rows=new Map(existing.map(row=>[row.sessionId,{values:{title:CODE,subagent:null,inbox:{'next-turn':[],'next-step':[]},...row.values},events:row.events??[],status:row.status??'idle',archived:row.archived??false}]));
 for(let index=0;index<capacity;index++)rows.set(`busy-${index}`,{values:{title:`SHE-20261103-00${index}`,subagent:null,inbox:{'next-turn':[],'next-step':[]}},events:[],status:'running',archived:false});
 const workspace={workspaceId:'workspace-test',path:project,sessionIds:[...rows.keys()]};const calls=[];
 const registry={list:()=>[workspace],archivedSessionIds:[...rows].filter(([,row])=>row.archived).map(([id])=>id)};
 const controller={
  async projections({sessionId}){return {kind:'sequenced',values:rows.get(sessionId)?.values};},
  async inspect(sessionId){return {meta:{cwd:project},events:rows.get(sessionId)?.events};},
  async create(args){calls.push(['create',args]);if(failCreate)throw Error('lost create result');rows.set(args.sessionId,{values:{title:'',subagent:null,inbox:{'next-turn':[],'next-step':[]}},events:[],status:'idle'});workspace.sessionIds.push(args.sessionId);return {sessionId:args.sessionId};},
  async rename(args){calls.push(['rename',args]);if(failRename)throw Error('lost rename result');rows.get(args.sessionId).values.title=args.title;return {title:args.title,seq:1};},
  async prompt(args){calls.push(['prompt',args]);if(failPrompt)throw Error('lost prompt result');rows.get(args.sessionId).events.push({type:'user/message',data:{source:{rpcId:args.requestId}}});rows.get(args.sessionId).status='running';return {accepted:true};},
 };
 const options={controller,registry,getStatus:id=>rows.get(id)?.status??null,project,expectedPlanSha:sha(bytes),allowPrompt:true};
 return {project,folder,plan,bytes,rows,calls,options,launcher:createOrdinary043(options)};
}
test('ordinary 043 same workspace, exact title, authorized queue and restart no replay',async t=>{
 const f=await fixture(t),result=await f.launcher.run();assert.equal(result.stage,'accepted');assert.equal(result.ordinary,true);assert.equal(result.agent_status,'running');
 assert.deepEqual(f.calls.map(([name])=>name),['create','rename','prompt']);assert.equal(f.calls[0][1].workspaceId,'workspace-test');assert.equal(f.calls[1][1].title,CODE);assert.equal(f.calls[2][1].mode,'queue');
 const before=f.calls.length;await createOrdinary043(f.options).run();assert.equal(f.calls.length,before);
 const j=JSON.parse(await readFile(join(f.folder,'ordinary-session-journal.json'),'utf8'));assert.equal(j.stage,'accepted');assert.equal(j.request_id,f.calls[2][1].requestId);
});
test('full four slots creates visible cold 043 but no prompt',async t=>{const f=await fixture(t,{capacity:4}),result=await f.launcher.run();assert.equal(result.stage,'created');assert.equal(result.waiting_for_capacity,true);assert.equal(result.ordinary,true);assert.deepEqual(f.calls.map(([name])=>name),['create','rename']);});
test('stale frozen plan and changed stopped handoff reject before mutations',async t=>{
 const f=await fixture(t);await writeFile(join(f.folder,'ordinary-session-plan.json'),f.bytes+' ');assert.equal((await f.launcher.run()).stage,'launch_failed');assert.equal(f.calls.length,0);
 await writeFile(join(f.folder,'ordinary-session-plan.json'),f.bytes);await writeFile(join(f.folder,'HANDOFF.md'),'changed');assert.equal((await f.launcher.run()).stage,'launch_failed');assert.equal(f.calls.length,0);
});
for(const [option,stage,sequence] of [['failCreate','create_unknown',['create']],['failRename','rename_unknown',['create','rename']],['failPrompt','dispatch_unknown',['create','rename','prompt']]]){
 test(`${stage} remains inspect-only after restart`,async t=>{const f=await fixture(t,{[option]:true});assert.equal((await f.launcher.run()).stage,stage);await createOrdinary043(f.options).run();assert.deepEqual(f.calls.map(([name])=>name),sequence);});
}
test('existing nonempty ordinary 043 is reused without wake/create/prompt',async t=>{const f=await fixture(t,{existing:[{sessionId:'existing',events:[{type:'user/message'},{type:'assistant/message'},{type:'turn/end'},{type:'session/title'}]}]});assert.equal((await f.launcher.run()).stage,'reused_existing');assert.equal(f.calls.length,0);});
test('existing archived or subagent matching title never creates replacement',async t=>{
 const a=await fixture(t,{existing:[{sessionId:'archived',archived:true}]});assert.equal((await a.launcher.run()).stage,'launch_failed');assert.equal(a.calls.length,0);
 const b=await fixture(t,{existing:[{sessionId:'child',values:{subagent:{parentSessionId:'p'}}}]});assert.equal((await b.launcher.run()).stage,'launch_failed');assert.equal(b.calls.length,0);
});
test('default integration creates cold visible conversation without any prompt',async t=>{const f=await fixture(t);const result=await createOrdinary043({...f.options,allowPrompt:false}).run();assert.equal(result.stage,'created');assert.equal(result.awaiting_parent_admission,true);assert.deepEqual(f.calls.map(([name])=>name),['create','rename']);});
test('single user event never qualifies as empty session',async t=>{const f=await fixture(t,{existing:[{sessionId:'used',events:[{type:'user/message'}]}]});assert.equal((await f.launcher.run()).stage,'reused_existing');assert.equal(f.calls.length,0);});
test('inbox missing queues never qualifies as empty',async t=>{const f=await fixture(t,{existing:[{sessionId:'unknown',values:{inbox:{}}}]});assert.equal((await f.launcher.run()).stage,'reused_existing');assert.equal(f.calls.length,0);});
test('capacity wait rechecks activity before prompt',async t=>{const f=await fixture(t,{capacity:4});const first=await f.launcher.run();f.rows.get(first.session_id).events.push({type:'user/message'});for(const row of f.rows.values())row.status='idle';const second=await f.launcher.run();assert.equal(second.stage,'launch_failed');assert.equal(f.calls.filter(([name])=>name==='prompt').length,0);});

test('concurrent triggers serialize once-only admission',async t=>{const f=await fixture(t);const results=await Promise.all([f.launcher.run(),f.launcher.run()]);assert(results.every(row=>row.stage==='accepted'));assert.equal(f.calls.filter(([name])=>name==='create').length,1);assert.equal(f.calls.filter(([name])=>name==='prompt').length,1);});
