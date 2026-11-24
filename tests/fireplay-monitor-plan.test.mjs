import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {emptyMonitorState,monitorQuery,planMonitor,acceptMonitorScan,acknowledgeMonitorBrief,readJsonStable} from '../scripts/fireplay-monitor-plan.mjs';

const aiMode='fixture_image_translation',now='2026-11-11T10:00:00Z';
const id='f3e17b15-2e91-4a5c-b5a8-a04b457d02bb';
const row=(changes={})=>({id,code:'SHE-20261111-001',deptId:'fixture',assetType:'image',aiMode,status:'in_progress',updatedAt:'2026-11-11T09:00:00Z',...changes});
function receipt(mode,items=[],ids=[]){
 const query=monitorQuery(mode,aiMode,ids);
 return {query,summary_pages:[{arguments:query,response:{scope:'all',deptId:null,view:'summary',items,next_cursor:null,serverTime:now,
  query_meta:{pagination:'live_keyset',snapshot:false,source_limit_reached:false,filters_applied:{...query,dept_id:null}}}}]};
}
const accept=(state,mode,items=[],extra={})=>acceptMonitorScan({state,mode,aiMode,now,receipt:receipt(mode,items,extra.ids),evidence:'fixture-receipt.json',...extra});
const plan=(state,requirements={},extra={})=>planMonitor({state,assignments:{requirements},now,aiMode,...extra});

test('empty and closed ownerless ledgers never poll chats; full reconciliation has its own 24h clock',()=>{
 let state=emptyMonitorState();assert.equal(plan(state).full_due,true);
 state=accept(state,'full',[row({status:'done'})]);
 const current=plan(state,{[id]:{state:'excluded_closed',platform_status:'done',responsible_thread_id:null}});
 assert.equal(current.full_due,false);assert.equal(current.discovery_query.limit,40);
 assert.deepEqual(current.tracked_queries,[]);assert.deepEqual(current.chat_targets,[]);assert.equal(current.skip_chat_poll,true);
 state=accept(state,'active',[]);assert.equal(state.checkpoints.full,now);
 assert.equal(plan(state,{}, {now:'2026-11-12T09:59:59Z'}).full_due,false);
 assert.equal(plan(state,{}, {now:'2026-11-12T10:00:00Z'}).full_due,true);
});

test('new rows, same-timestamp metadata edits and old in_progress rows schedule exact brief reads',()=>{
 let state=accept(emptyMonitorState(),'active',[row()]);
 assert.deepEqual(plan(state).brief_candidates,[id]);
 const firstHash=state.pending_briefs[id].summary_hash;
 state=acknowledgeMonitorBrief({state,id,now,evidence:'fixture-review.json',receipt:{summary_hash:firstHash,...Object.fromEntries(['blind_brief','delivery'].map(view=>[view,{view,scope:'all',serverTime:now,items:[row()]}]))}});
 assert.deepEqual(plan(state).brief_candidates,[]);
 assert.deepEqual(plan(state).tracked_queries[0].requirement_ids,[id]);
 state=accept(state,'active',[row()]);
 assert.deepEqual(plan(state).brief_candidates,[]);
 state=accept(state,'active',[row({languages:['ja','hi'],attachments:['extra-source']})]);
 assert.notEqual(state.pending_briefs[id].summary_hash,firstHash);
 assert.deepEqual(plan(state).brief_candidates,[id]);
});

test('unchanged scan never clears unread brief work; full finds other open statuses',()=>{
 const state=accept(emptyMonitorState(),'full',[row({status:'pending_upload'})]);
 assert.deepEqual(plan(accept(state,'active',[])).brief_candidates,[id]);
 assert.deepEqual(plan(accept(state,'full',[row({status:'pending_upload'})])).brief_candidates,[id]);
});

test('local owners and unknown operations survive done, changed type/mode and missing rows',()=>{
 const requirements={[id]:{state:'unknown',platform_status:'done',responsible_thread_id:'owner-chat',wait_cursor:'cursor',host_id:'local'}};
 const planned=plan(emptyMonitorState(),requirements);
 assert.deepEqual(planned.tracked_queries[0],monitorQuery('tracked',aiMode,[id]));
 assert.equal(Object.hasOwn(planned.tracked_queries[0],'ai_mode'),false);
 assert.equal(Object.hasOwn(planned.tracked_queries[0],'status'),false);
 assert.deepEqual(planned.chat_targets,[{threadId:'owner-chat',hostId:'local',afterCursor:'cursor'}]);
 const state=accept(emptyMonitorState(),'tracked',[row({status:'done',aiMode:'changed',assetType:'video'})],{ids:[id]});
 assert.deepEqual(plan(state,requirements).brief_candidates,[id]);
 const missing=accept(state,'tracked',[],{ids:[id]});
 assert.deepEqual(Object.values(missing.tracked_missing),[[id]]);
 assert.deepEqual(plan(missing,requirements).tracked_queries[0].requirement_ids,[id]);
});

test('tracked small pages keep the original UUID batch and require the full cursor chain',()=>{
 const ids=Array.from({length:14},(_,index)=>`f3e17b15-2e91-4a5c-b5a8-${index.toString(16).padStart(12,'0')}`);
 const items=ids.map((requirementId,index)=>row({id:requirementId,code:`REQ-${index}`}));
 const value=receipt('tracked',items.slice(0,7),ids);
 assert.equal(value.query.limit,7);
 assert.deepEqual(value.query.requirement_ids,ids);
 value.summary_pages[0].response.next_cursor='next-seven';
 const options={state:emptyMonitorState(),mode:'tracked',aiMode,now,ids,evidence:'tracked.json'};
 assert.throws(()=>acceptMonitorScan({...options,receipt:value}),/Partial scan/);
 const second=receipt('tracked',items.slice(7),ids).summary_pages[0];
 second.arguments={...value.query,cursor:'next-seven'};
 value.summary_pages.push(second);
 const state=acceptMonitorScan({...options,receipt:value});
 assert.equal(Object.keys(state.summaries).length,14);
 assert.deepEqual(Object.values(state.checkpoints.tracked)[0].ids,ids);
 second.arguments.requirement_ids=ids.slice(7);
 assert.throws(()=>acceptMonitorScan({...options,receipt:value}),/changed cursor chain/);
});

test('tracked checkpoints are per exact batch; tracked missing rows are never closure proof',()=>{
 const second='f3e17b15-2e91-4a5c-b5a8-a04b457d02bc';
 const state=accept(emptyMonitorState(),'tracked',[],{ids:[id]});
 const updated=accept(state,'tracked',[],{ids:[second]});
 assert.equal(Object.keys(updated.checkpoints.tracked).length,2);
 assert.equal(updated.checkpoints.active,null);assert.equal(updated.checkpoints.full,null);
});

test('partial, changed filters, duplicate rows/cursors, errors and extra pages cannot mutate checkpoints',()=>{
 const state=emptyMonitorState(),before=structuredClone(state);
 const variants=[
  value=>{value.summary_pages[0].response.next_cursor='remaining';},
  value=>{value.summary_pages[0].response.query_meta.filters_applied.status=['not_started'];},
  value=>{value.summary_pages[0].response.items.push(row());},
  value=>{value.summary_pages[0].response.isError=true;},
  value=>{value.summary_pages.push(structuredClone(value.summary_pages[0]));},
  value=>{value.summary_pages[0].response.query_meta.source_limit_reached=true;},
  value=>{value.summary_pages[0].response.next_cursor='loop';const second=structuredClone(value.summary_pages[0]);second.arguments={...value.query,cursor:'loop'};value.summary_pages.push(second);},
 ];
 for(const mutate of variants){
  const value=receipt('active',[row()]);mutate(value);
  assert.throws(()=>acceptMonitorScan({state,mode:'active',aiMode,now,receipt:value,evidence:'bad.json'}));
  assert.deepEqual(state,before);
 }
});

test('full cursor chain preserves filters and cache while rejecting reordered observations',()=>{
 assert.equal(monitorQuery('full',aiMode).limit,10);
 const value=receipt('full',[row()]);value.summary_pages[0].response.next_cursor='next';
 const second=receipt('full',[]).summary_pages[0];second.arguments={...value.query,cursor:'next'};value.summary_pages.push(second);
 const state=acceptMonitorScan({state:emptyMonitorState(),mode:'full',aiMode,now,receipt:value,evidence:'full.json'});
 assert.equal(state.checkpoints.full,now);
 const stale=receipt('full',[]);stale.summary_pages[0].response.serverTime='2026-11-10T10:00:00Z';
 assert.throws(()=>acceptMonitorScan({state,mode:'full',aiMode,now,receipt:stale,evidence:'stale.json'}),/window|Stale/);
 const review={summary_hash:state.pending_briefs[id].summary_hash,...Object.fromEntries(['blind_brief','delivery'].map(view=>[view,{view,scope:'all',serverTime:now,items:[row({updatedAt:now})]}]))};
 assert.throws(()=>acknowledgeMonitorBrief({state,id,now,evidence:'changed.json',receipt:review}),/changed/);
});

test('old/future reads and updatedAt after serverTime do not advance or acknowledge observations',()=>{
 const state=emptyMonitorState();
 for(const changes of [{serverTime:'2026-11-11T09:29:59Z'},{serverTime:'2026-11-11T10:00:01Z'},{items:[row({updatedAt:'2026-11-11T10:00:01Z'})]}]){
  const value=receipt('active',[row()]);Object.assign(value.summary_pages[0].response,changes);
  assert.throws(()=>acceptMonitorScan({state,mode:'active',aiMode,now,receipt:value,evidence:'invalid-time.json'}));
  assert.deepEqual(state,emptyMonitorState());
 }
 const pending=accept(state,'active',[row()]);
 const review={summary_hash:pending.pending_briefs[id].summary_hash,...Object.fromEntries(['blind_brief','delivery'].map(view=>[view,{view,scope:'all',serverTime:'2026-11-11T09:29:59Z',items:[row()]}]))};
 assert.throws(()=>acknowledgeMonitorBrief({state:pending,id,now,evidence:'stale-review.json',receipt:review}),/window/);
});

test('tracked groups are bounded and cache alone never substitutes for official delivery verification',()=>{
 const requirements=Object.fromEntries(Array.from({length:41},(_,index)=>[`f3e17b15-2e91-4a5c-b5a8-${index.toString(16).padStart(12,'0')}`,{state:'queued'}]));
 const current=plan(emptyMonitorState(),requirements);
 assert.deepEqual(current.tracked_queries.map(query=>query.requirement_ids.length),[40,1]);
 assert.deepEqual(current.chat_targets,[]);
 const verified=plan(emptyMonitorState(),{[id]:{state:'verified_complete',responsible_thread_id:'owner',delivery_verified_by_controller:true}});
 assert.deepEqual(verified.tracked_queries,[]);assert.deepEqual(verified.chat_targets,[]);
 const unknown=plan(emptyMonitorState(),{[id]:{state:'unknown',responsible_thread_id:'owner',delivery_verified_by_controller:true}});
 assert.equal(unknown.tracked_queries.length,1);
});

test('submitted material owners are tracked only for read-only reconciliation',()=>{
 const planned=plan(emptyMonitorState(),{
  [id]:{
   state:'delivered_pending_controller_review',
   responsible_thread_id:'owner',
   submitted_materials_frozen:true,
   delivery_verified_by_controller:false
  }
 });
 assert.deepEqual(planned.chat_targets,[{
  threadId:'owner',
  hostId:'local',
  mode:'read_only_reconciliation'
 }]);
});

test('fresh but older active observations cannot overwrite a newer full or tracked summary',()=>{
 const state=accept(emptyMonitorState(),'full',[row()]);
 const older=receipt('active',[row()]);older.summary_pages[0].response.serverTime='2026-11-11T09:59:59Z';
 assert.throws(()=>acceptMonitorScan({state,mode:'active',aiMode,now,receipt:older,evidence:'older.json'}),/Older observation/);
 assert.equal(state.checkpoints.active,null);
 assert.equal(state.summaries[id].observed_at,now);
 assert.throws(()=>accept(state,'tracked',[row({updatedAt:'2026-11-11T08:00:00Z'})],{ids:[id]}),/Older observation/);
});

test('CLI accepts valid receipts atomically and leaves saved state intact after invalid JSON or partial scans',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'image-monitor-'));
 try{
  const statePath=join(folder,'state.json'),assignmentsPath=join(folder,'assignments.json'),receiptPath=join(folder,'receipt.json');
  await writeFile(assignmentsPath,JSON.stringify({requirements:{}}));
  const args=['scripts/fireplay-monitor-plan.mjs','--state',statePath,'--assignments',assignmentsPath,'--now',now,'--ai-mode',aiMode,'--receipt',receiptPath,'--mode','active'];
  await writeFile(receiptPath,JSON.stringify(receipt('active',[])));
  assert.equal(spawnSync(process.execPath,args,{encoding:'utf8'}).status,0);
  const saved=await readFile(statePath,'utf8');
  for(const invalid of ['{',JSON.stringify({...receipt('active',[]),summary_pages:[]})]){
   await writeFile(receiptPath,invalid);
   assert.equal(spawnSync(process.execPath,args,{encoding:'utf8'}).status,1);
   assert.equal(await readFile(statePath,'utf8'),saved);
  }
 }finally{await rm(folder,{recursive:true,force:true});}
});

test('stable JSON reads tolerate a concurrent atomic-writer window but still reject persistent corruption',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'image-monitor-stable-'));
 try{
  const path=join(folder,'assignments.json');
  await writeFile(path,'{');
  const rewrite=setTimeout(()=>writeFile(path,JSON.stringify({requirements:{}})),25);
  assert.deepEqual(await readJsonStable(path,{attempts:8,delayMs:10}),{requirements:{}});
  clearTimeout(rewrite);
  await writeFile(path,'{');
  await assert.rejects(readJsonStable(path,{attempts:2,delayMs:1}),SyntaxError);
 }finally{await rm(folder,{recursive:true,force:true});}
});

test('old and completed work is excluded while an incomplete done item remains tracked',()=>{
 const recent={code:'MOG-20261111-063',state:'partial',platform_status:'done',responsible_thread_id:'owner'};
 assert.equal(plan(emptyMonitorState(),{[id]:recent}).chat_targets.length,1);
 for(const entry of [{...recent,code:'MOG-20261107-063'}, {...recent,state:'verified_complete'}, {...recent,delivery_verified_by_controller:true}]){
  const state=accept(emptyMonitorState(),'tracked',[row({status:'done'})],{ids:[id]});
  const result=plan(state,{[id]:entry});
  assert.deepEqual(result.tracked_queries,[]);assert.deepEqual(result.chat_targets,[]);assert.deepEqual(result.brief_candidates,[]);
 }
 assert.equal(plan(emptyMonitorState(),{[id]:{...recent,code:'MOG-20261108-063'}}).chat_targets.length,1);
});

test('new discovery is unstarted only and local delivery alone needs controller followup',()=>{
 const state=accept(emptyMonitorState(),'full',[row({status:'not_started'})]);
 const planned=plan(state);
 assert.deepEqual(planned.discovery_query.status,['not_started']);
 assert.deepEqual(planned.new_candidates,[id]);
 assert.throws(()=>accept(state,'new',[row()]),/scan filters/);
 assert.deepEqual(plan(state,{[id]:{state:'delivered_local',responsible_thread_id:'owner'}}).new_candidates,[]);
 assert.equal(plan(state,{[id]:{state:'delivered_local',responsible_thread_id:'owner'}}).chat_targets.length,1);
});

test('admission counts unique reserved and active UUIDs and queues by priority',()=>{
 const urgent='f3e17b15-2e91-4a5c-b5a8-a04b457d02bc';
 const state=accept(emptyMonitorState(),'new',[
  row({status:'not_started',priority:'P1'}),
  row({id:urgent,code:'SHE-20261111-002',status:'not_started',priority:'P0'})
 ]);
 const requirements=Object.fromEntries(Array.from({length:19},(_,index)=>[
  `f3e17b15-2e91-4a5c-b5a8-${String(index).padStart(12,'0')}`,
  {code:`SHE-20261111-${String(index+10).padStart(3,'0')}`,state:'dispatched'}
 ]));
 const duplicate=Object.keys(requirements)[0];
 const admissions={requirements:{[duplicate]:{state:'reserved'}}};
 const result=plan(state,requirements,{admissions});
 assert.deepEqual(result.capacity,{limit:20,active:19,available:1,queued:1});
 assert.deepEqual(result.new_candidates,[urgent]);
 assert.deepEqual(result.queued_candidates,[id]);
 admissions.requirements[urgent]={state:'reserved'};
 assert.deepEqual(plan(state,requirements,{admissions}).new_candidates,[urgent]);
 assert.equal(plan(state,requirements,{admissions}).capacity.available,0);
 admissions.requirements[duplicate]={state:'released'};
 assert.equal(plan(state,requirements,{admissions}).capacity.available,1);
 requirements[duplicate].unknown_operation='unresolved';
 assert.equal(plan(state,requirements,{admissions}).capacity.available,0);
});

test('reserved requirements resume dispatch after opening or losing discovery state',()=>{
 const admissions={requirements:{[id]:{state:'reserved',code:'SHE-20261111-001'}}};
 const opened=accept(emptyMonitorState(),'full',[row()]);
 for(const state of [opened,emptyMonitorState()]){
  const output=plan(state,{},{admissions});
  assert.deepEqual(output.continuation_candidates,[id]);
  assert.deepEqual(output.tracked_queries[0].requirement_ids,[id]);
  assert.deepEqual(output.new_candidates,[]);
 }
 const assigned=plan(opened,{[id]:{state:'dispatched',responsible_thread_id:'thread'}},{admissions});
 assert.deepEqual(assigned.continuation_candidates,[]);
 assert.equal(assigned.capacity.active,1);
});
