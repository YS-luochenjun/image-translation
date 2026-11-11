import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {readOnlyArtifactKinds} from './publication-readonly.js';
import {createControl,createDispatchExecutor} from './control.js';
const digest=value=>createHash('sha256').update(value).digest('hex');
const id='11111111-1111-1111-1111-111111111111';
const sid='session-22222222-2222-2222-2222-222222222222';
async function setup(t) {
  const project=await mkdtemp(join(tmpdir(),'session-control-'));t.after(()=>rm(project,{recursive:true,force:true}));
  const dir=join(project,'work/fireplay-production/session-dispatch');await mkdir(dir,{recursive:true});
  const manifest=JSON.stringify({records:[{id,code:'SHE-001'}]});await writeFile(join(project,'manifest.json'),manifest);
  await writeFile(join(project,'control.md'),'Fixed reviewed control');
  const snapshot={requirement_id:id,code:'SHE-001',title:'SHE-001',manifest_sha256:digest(manifest)};
  const state={snapshot,snapshot_sha256:digest(JSON.stringify(snapshot)),session_id:sid,stage:'accepted'};
  await writeFile(join(dir,id+'.json'),JSON.stringify(state));
  const events=[],calls=[],registry={archivedSessionIds:[],list:()=>[{path:project,sessionIds:[sid]}]};
  const projection={kind:'sequenced',values:{title:'SHE-001',subagent:null,inbox:{'next-turn':[],'next-step':[]}}};
  const controller={inspect:async()=>({meta:{cwd:project},events}),projections:async()=>projection,
    prompt:async request=>{calls.push(request);events.push({type:'user/message',data:{source:{rpcId:request.requestId}}});return {accepted:true};}};
  let status='running';
  const options={project,manifest:'manifest.json',manifestSha256:digest(manifest),controlFile:'control.md',expectedCount:1,registry,controller,getStatus:()=>status};
  return {project,dir,state,projection,events,calls,registry,controller,options,control:createControl(options),setStatus:s=>status=s};
}
const steer=async f=>{const preview=await f.control.execute({action:'preview'});return f.control.execute({action:'steer',control_sha256:preview.control_sha256});};
test('active queue uses fixed text and durable identity, never repeats after restart',async t=>{
  const f=await setup(t);const result=await steer(f);assert.equal(result.sent,1);assert.equal(result.items[0].durable_control_observed,true);
  assert.equal(f.calls[0].mode,'queue');assert.equal(f.calls[0].content[0].text,'Fixed reviewed control');
  const path=join(f.project,'work/fireplay-production/session-control',digest('Fixed reviewed control'),id+'.json');
  const receipt=JSON.parse(await readFile(path));assert.equal(receipt.stage,'accepted');
  await createControl(f.options).execute({action:'steer',control_sha256:digest('Fixed reviewed control')});assert.equal(f.calls.length,1);
});
test('inactive is pending, never resumed, pending can send once active',async t=>{
  const f=await setup(t);f.setStatus('inactive');const result=await steer(f);assert.equal(result.sent,0);assert.equal(result.items[0].control.reason,'inactive_not_woken');
  f.setStatus('running');assert.equal((await steer(f)).sent,1);
});
test('unknown prompt result never retries, inspect reports durable evidence read-only',async t=>{
  const f=await setup(t);f.controller.prompt=async request=>{f.calls.push(request);f.events.push({type:'user/message',data:{source:{rpcId:request.requestId}}});throw Error('lost response');};
  assert.equal((await steer(f)).items[0].control.stage,'unknown');await steer(f);assert.equal(f.calls.length,1);
  const result=await f.control.execute({action:'inspect',control_sha256:digest('Fixed reviewed control')});assert.equal(result.items[0].durable_control_observed,true);assert.equal(result.items[0].control.stage,'unknown');
});
test('archived, nonordinary, title/project mismatch and nonaccepted never prompt',async t=>{
  const f=await setup(t);f.registry.archivedSessionIds=[sid];assert.equal((await steer(f)).items[0].reason,'archived');
  f.registry.archivedSessionIds=[];f.projection.values.subagent={mode:'continuable'};assert.match((await steer(f)).items[0].error,/identity/);
  f.projection.values.subagent=null;f.projection.values.title='wrong';assert.match((await steer(f)).items[0].error,/identity/);
  f.projection.values.title='SHE-001';f.registry.list=()=>[{path:f.project,sessionIds:[]}];assert.match((await steer(f)).items[0].error,/member/);
  f.state.stage='titled';await writeFile(join(f.dir,id+'.json'),JSON.stringify(f.state));assert.equal((await steer(f)).items[0].reason,'dispatch_not_accepted');assert.equal(f.calls.length,0);
});
test('manifest, fixed control review, arbitrary args and foreign UUID rejected',async t=>{
  const f=await setup(t);
  await assert.rejects(f.control.execute({action:'steer',control_sha256:'0'.repeat(64)}),/hash/);
  await assert.rejects(f.control.execute({action:'preview',prompt:'override'}),/arguments/);
  await assert.rejects(f.control.execute({action:'preview',requirement_ids:['33333333-3333-3333-3333-333333333333']}),/outside/);
  await writeFile(join(f.project,'manifest.json'),'{}');await assert.rejects(f.control.execute({action:'inspect'}),/manifest/);assert.equal(f.calls.length,0);
});
test('bridge preserves original dispatch and checks original snapshot plus reviewed active batch',async t=>{
  const f=await setup(t),originalCalls=[];
  const bridge=createDispatchExecutor({project:f.project,control:f.control,dispatcher:{execute:async args=>{originalCalls.push(args);return {original:true};}}});
  assert.equal((await bridge({action:'inspect',requirement_id:id})).original,true);
  await assert.rejects(bridge({action:'dispatch',requirement_id:id,snapshot_sha256:'bad'}),/snapshot/);
  const review=join(f.project,'work/fireplay-production/session-control-review.json');
  await writeFile(review,JSON.stringify({control_sha256:digest('Fixed reviewed control'),requirement_ids:[]}));
  await assert.rejects(bridge({action:'dispatch',requirement_id:id,snapshot_sha256:f.state.snapshot_sha256}),/batch/);
  await writeFile(review,JSON.stringify({control_sha256:digest('Fixed reviewed control'),requirement_ids:[id]}));
  const result=await bridge({action:'dispatch',requirement_id:id,snapshot_sha256:f.state.snapshot_sha256});assert.equal(result.sent,1);assert.equal(originalCalls.length,1);
});

test('one batch queues at most four running current sessions',async t=>{
  const f=await setup(t);
  const records=Array.from({length:5},(_,i)=>({id:`${String(i+1).padStart(8,'0')}-1111-1111-1111-111111111111`,code:'SHE-001'}));
  const manifest=JSON.stringify({records});await writeFile(join(f.project,'manifest.json'),manifest);
  const sessionIds=[];
  for(const row of records){const sessionId=`session-${row.id}`;sessionIds.push(sessionId);const snapshot={requirement_id:row.id,code:row.code,title:row.code,manifest_sha256:digest(manifest)};await writeFile(join(f.dir,row.id+'.json'),JSON.stringify({snapshot,snapshot_sha256:digest(JSON.stringify(snapshot)),session_id:sessionId,stage:'accepted'}));}
  f.registry.list=()=>[{path:f.project,sessionIds}];
  const control=createControl({...f.options,manifestSha256:digest(manifest),expectedCount:5});
  const result=await control.execute({action:'steer',control_sha256:digest('Fixed reviewed control')});
  assert.equal(result.sent,4);assert.equal(f.calls.length,4);assert.equal(result.items[4].control.reason,'batch_limit');
});

test('entry registers original dispatch and scoped control tools',async()=>{
  const {apply}=await import('./index.js'),tools=[];
  apply({tools:{register:tool=>{tools.push(tool);return ()=>{};}},sessionController:{},workspaceRegistry:{},agents:new Map(),effect:fn=>fn()});
  assert.deepEqual(tools.map(tool=>tool.name),['fireplay_session_dispatch','fireplay_session_control','fireplay_session_identity']);
  assert.equal(tools[1].parameters.additionalProperties,false);
});

async function approveWake(f,flags={worker_stopped:true,inflight_unknown:false}) {
  const review={control_sha256:digest('Fixed reviewed control'),requirement_ids:[id],wake_approved_ids:[id],reserved_active_requirement_ids:[]};
  await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify(review));
  const folder=join(f.project,'work/fireplay-production/SHE-001');await mkdir(folder,{recursive:true});
  await writeFile(join(folder,'handoff-to-original-session.json'),JSON.stringify(flags));
  return review;
}

test('explicit idle wake queues fixed text only after stopped/known handoff and shows reservation in preview',async t=>{
  const f=await setup(t);f.setStatus('idle');await approveWake(f);
  const preview=await f.control.execute({action:'preview'});assert.deepEqual(preview.wake_approved_ids,[id]);assert.equal(preview.handoff_readiness[0].ready,true);assert.equal(preview.reserved_slot_count,1);
  const result=await steer(f);assert.equal(result.sent,1);assert.equal(f.calls[0].mode,'queue');assert.equal(result.items[0].control.wake_authorized,true);assert.equal(result.items[0].control.before_agent_status,'idle');
  await steer(f);assert.equal(f.calls.length,1);
});
test('cold null remains pending even with wake approval',async t=>{
  const f=await setup(t);f.setStatus(null);await approveWake(f);const result=await steer(f);assert.equal(result.sent,0);assert.equal(result.items[0].control.stage,'pending');assert.equal(f.calls.length,0);
});
test('wake pending receipt may upgrade only when worker stopped and no unknown inflight',async t=>{
  const f=await setup(t);f.setStatus('inactive');const pending=await steer(f);const requestId=pending.items[0].control.request_id;
  await approveWake(f,{worker_stopped:false,inflight_unknown:false});assert.match((await steer(f)).items[0].error,/handoff not ready/);
  await approveWake(f,{worker_stopped:true,inflight_unknown:true});assert.match((await steer(f)).items[0].error,/handoff not ready/);
  await approveWake(f);const result=await steer(f);assert.equal(result.sent,1);assert.equal(f.calls[0].requestId,requestId);
});
test('archive and review membership fence still reject approved wake',async t=>{
  const f=await setup(t);f.setStatus('idle');await approveWake(f);f.registry.archivedSessionIds=[sid];assert.equal((await steer(f)).items[0].reason,'archived');
  const review={control_sha256:digest('Fixed reviewed control'),requirement_ids:[id],wake_approved_ids:['33333333-3333-3333-3333-333333333333']};
  await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify(review));await assert.rejects(steer(f),/reviewed control IDs/);assert.equal(f.calls.length,0);
});
test('review or control mutation after guard read refuses Host admission',async t=>{
  const f=await setup(t);f.setStatus('idle');const review=await approveWake(f);
  f.controller.inspect=async()=>{await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify(review,null,2));return {meta:{cwd:f.project},events:f.events};};
  assert.match((await steer(f)).items[0].error,/review changed/);assert.equal(f.calls.length,0);
  await approveWake(f);f.controller.inspect=async()=>{await writeFile(join(f.project,'control.md'),'Changed control');return {meta:{cwd:f.project},events:f.events};};
  assert.match((await f.control.execute({action:'steer',control_sha256:digest('Fixed reviewed control')})).items[0].error,/document changed/);assert.equal(f.calls.length,0);
});
test('unknown idle admission is never replayed across restart',async t=>{
  const f=await setup(t);f.setStatus('idle');await approveWake(f);f.controller.prompt=async request=>{f.calls.push(request);throw Error('unknown delivery');};
  assert.equal((await steer(f)).items[0].control.stage,'unknown');
  const restarted=createControl(f.options);await restarted.execute({action:'steer',control_sha256:digest('Fixed reviewed control')});assert.equal(f.calls.length,1);
});
test('wake approvals reserve slots together with all current running requirements, never over four',async t=>{
  const f=await setup(t);
  const records=Array.from({length:5},(_,i)=>({id:`${String(i+1).padStart(8,'0')}-1111-1111-1111-111111111111`,code:'SHE-001'}));
  const manifest=JSON.stringify({records});await writeFile(join(f.project,'manifest.json'),manifest);const sessionIds=[];
  for(const row of records){const sessionId=`session-${row.id}`;sessionIds.push(sessionId);const snapshot={requirement_id:row.id,code:row.code,title:row.code,manifest_sha256:digest(manifest)};await writeFile(join(f.dir,row.id+'.json'),JSON.stringify({snapshot,snapshot_sha256:digest(JSON.stringify(snapshot)),session_id:sessionId,stage:'accepted'}));}
  f.registry.list=()=>[{path:f.project,sessionIds}];
  const review={control_sha256:digest('Fixed reviewed control'),requirement_ids:records.slice(0,4).map(r=>r.id),wake_approved_ids:[records[0].id],reserved_active_requirement_ids:[]};
  await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify(review));
  const control=createControl({...f.options,manifestSha256:digest(manifest),expectedCount:5,getStatus:session=>session===sessionIds[0]?'idle':'running'});
  const preview=await control.execute({action:'preview'});assert.equal(preview.reserved_slot_count,5);assert.equal(preview.capacity_available,false);
  const result=await control.execute({action:'steer',requirement_ids:[records[0].id],control_sha256:review.control_sha256});assert.match(result.items[0].error,/slots exceeded/);assert.equal(f.calls.length,0);
});

test('capacity ignores idle legacy journal but fails closed with code when that journal runs',async t=>{
  const f=await setup(t);await approveWake(f);
  const other='33333333-3333-3333-3333-333333333333',otherSession='session-44444444-4444-4444-4444-444444444444';
  const manifest=JSON.stringify({records:[{id,code:'SHE-001'},{id:other,code:'SHE-002'}]});await writeFile(join(f.project,'manifest.json'),manifest);
  f.state.snapshot.manifest_sha256=digest(manifest);f.state.snapshot_sha256=digest(JSON.stringify(f.state.snapshot));await writeFile(join(f.dir,id+'.json'),JSON.stringify(f.state));
  const legacy={requirement_id:other,code:'SHE-002',title:'old business title',manifest_sha256:digest(manifest)};
  await writeFile(join(f.dir,other+'.json'),JSON.stringify({snapshot:legacy,snapshot_sha256:digest(JSON.stringify(legacy)),session_id:otherSession,stage:'accepted'}));
  let otherStatus='running';f.registry.list=()=>[{path:f.project,sessionIds:[sid,otherSession]}];
  const control=createControl({...f.options,expectedCount:2,manifestSha256:digest(manifest),getStatus:session=>session===otherSession?otherStatus:'idle'});
  await assert.rejects(control.execute({action:'preview'}),/SHE-002: Current dispatch journal identity mismatch/);
  otherStatus='idle';const preview=await control.execute({action:'preview'});assert.equal(preview.reserved_slot_count,1);assert.equal(preview.capacity_available,true);
});

async function auditedHelpers(){const source=new URL('./fixtures/readonly/',import.meta.url),files={};for(const name of Object.keys(readOnlyArtifactKinds)){let bytes=await readFile(new URL(name,source),'utf8');if(name==='publication-gate.json'){const value=JSON.parse(bytes);value.requirement_id=id;bytes=JSON.stringify(value);}files[name]=bytes;}return files;}
function declarations(evidence){return Object.fromEntries(Object.entries(readOnlyArtifactKinds).map(([name,kind])=>[name,{sha256:evidence.publication_journals[name],kind,evidence_note:'Parent audited exact helper typed data and complete inventory; no operation skipped.'}]));}
test('explicit parent declarations admit actual seven readonly typed artifacts with terminal operations and aggregate',async t=>{const f=await setup(t);f.setStatus('idle');const files=await auditedHelpers();files['publication-operation.json']=JSON.stringify({operation_id:'real',requirement_id:id,state:'received'});files['publication-journal.jsonl']=JSON.stringify({operation_id:'real',requirement_id:id,state:'sending'})+'\n'+JSON.stringify({operation_id:'real',requirement_id:id,state:'received'})+'\n';files['publication-status.json']=JSON.stringify({requirement_id:id,state:'reconciled',remote_write_attempted:true,unknown_inflight:false});const a=await approveOriginal(f,files);a.evidence.read_only_artifacts=declarations(a.evidence);await writeFile(a.path,JSON.stringify(a.evidence));const preview=await f.control.execute({action:'preview'});assert.equal(preview.handoff_readiness[0].ready,true);assert.equal((await steer(f)).sent,1);assert.equal(f.calls.length,1);});
test('without readonly declarations original gate remains strict for helper arrays',async t=>{const f=await setup(t);f.setStatus('idle');await approveOriginal(f,await auditedHelpers());const result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/publication outcome unknown/);assert.equal(f.calls.length,0);});
test('readonly declaration cannot skip unknown operation disguised as catalog or actual write receipt',async t=>{const f=await setup(t);f.setStatus('idle');const files=await auditedHelpers(),name='publication-catalog-normalized-1845.json',catalog=JSON.parse(files[name]);catalog[0].operation_id='real-write';catalog[0].state='unknown';catalog[0].remote_write_attempted=true;files[name]=JSON.stringify(catalog);const a=await approveOriginal(f,files);a.evidence.read_only_artifacts=declarations(a.evidence);await writeFile(a.path,JSON.stringify(a.evidence));let result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/cannot override an actual operation/);const actual='publication-operation.json',text=JSON.stringify({operation_id:'real',state:'unknown',remote_write_attempted:true});await writeFile(join(a.folder,actual),text);a.evidence.publication_journals[actual]=digest(text);a.evidence.read_only_artifacts[actual]={sha256:digest(text),kind:'tool_catalog_definitions',evidence_note:'must refuse'};await writeFile(a.path,JSON.stringify(a.evidence));result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/Invalid parent read-only/);assert.equal(f.calls.length,0);});
test('readonly declaration requires exact inventory, declared SHA and readonly target schema',async t=>{const f=await setup(t);f.setStatus('idle');const a=await approveOriginal(f,await auditedHelpers());a.evidence.read_only_artifacts=declarations(a.evidence);const name='publication-context-requests.json';a.evidence.read_only_artifacts[name].sha256='0'.repeat(64);await writeFile(a.path,JSON.stringify(a.evidence));let result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/Invalid parent read-only/);a.evidence.read_only_artifacts=declarations(a.evidence);delete a.evidence.publication_journals[name];await writeFile(a.path,JSON.stringify(a.evidence));result=await steer(f);assert.match(result.items[0].error,/inventory incomplete/);const text=await readFile(join(a.folder,name),'utf8'),req=JSON.parse(text);req[0].arguments.schema_version='0'.repeat(64);const wrong=JSON.stringify(req);await writeFile(join(a.folder,name),wrong);a.evidence.publication_journals[name]=digest(wrong);a.evidence.read_only_artifacts=declarations(a.evidence);await writeFile(a.path,JSON.stringify(a.evidence));result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/shape\/tool/);assert.equal(f.calls.length,0);});
test('readonly declarations cannot hide unresolved actual aggregate with remote writes',async t=>{const f=await setup(t);f.setStatus('idle');const files=await auditedHelpers();files['publication-status.json']=JSON.stringify({requirement_id:id,status:'r2_uploaded_import_rejected',remote_write_attempted:true,unknown_inflight:false});const a=await approveOriginal(f,files);a.evidence.read_only_artifacts=declarations(a.evidence);await writeFile(a.path,JSON.stringify(a.evidence));const result=await steer(f);assert.equal(result.sent,0);assert.match(result.items[0].error,/publication outcome unknown/);assert.equal(f.calls.length,0);});
async function approveOriginal(f,journals={}) {
  const review={control_sha256:digest('Fixed reviewed control'),requirement_ids:[id],wake_approved_ids:[id],original_idle_resume_ids:[id]};
  await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify(review));
  const folder=join(f.project,'work/fireplay-production/SHE-001');await mkdir(folder,{recursive:true});
  const inventory={};for(const [name,text] of Object.entries(journals)){await writeFile(join(folder,name),text);inventory[name]=digest(text);}
  const evidence={requirement_id:id,code:'SHE-001',session_id:sid,snapshot_sha256:f.state.snapshot_sha256,reviewed_by:'parent',reviewed_at:new Date().toISOString(),evidence_note:'Parent checked original publication state; no temporary worker exists.',temporary_worker_occupies_slot:false,inflight_unknown:false,publication_journals:inventory};
  const path=join(folder,'original-idle-resume-review.json');await writeFile(path,JSON.stringify(evidence));
  return {review,evidence,path,folder};
}
test('reviewed original idle resume needs no fabricated temporary handoff and remains once-only',async t=>{
  const f=await setup(t);f.setStatus('idle');await approveOriginal(f);
  const preview=await f.control.execute({action:'preview'});assert.equal(preview.handoff_readiness[0].ready,true);assert.deepEqual(preview.original_idle_resume_ids,[id]);
  const result=await steer(f);assert.equal(result.sent,1);assert.equal(result.items[0].control.handoff.mode,'original_idle_resume');await steer(f);assert.equal(f.calls.length,1);
});
test('original resume rejects non-subset, missing parent evidence, occupied worker, unknown inflight and wrong session',async t=>{
  const f=await setup(t);f.setStatus('idle');const a=await approveOriginal(f);
  for(const patch of [{temporary_worker_occupies_slot:true},{inflight_unknown:true},{session_id:'wrong'},{snapshot_sha256:'wrong'},{reviewed_by:'other'}]){
    await writeFile(a.path,JSON.stringify({...a.evidence,...patch}));assert.match((await steer(f)).items[0].error,/parent evidence/);
  }
  await rm(a.path);assert.equal((await f.control.execute({action:'preview'})).handoff_readiness[0].ready,false);
  await writeFile(join(f.project,'work/fireplay-production/session-control-review.json'),JSON.stringify({...a.review,wake_approved_ids:[]}));await assert.rejects(steer(f),/subset/);assert.equal(f.calls.length,0);
});
test('original resume requires fresh title ordinary identity and both inboxes explicitly empty',async t=>{
  const f=await setup(t);f.setStatus('idle');await approveOriginal(f);
  f.projection.values.inbox['next-turn']=[{}];assert.match((await steer(f)).items[0].error,/empty observed inbox/);
  f.projection.values.inbox['next-turn']=[];delete f.projection.values.inbox['next-step'];assert.match((await steer(f)).items[0].error,/empty observed inbox/);
  f.projection.values.inbox['next-step']=[];let inspections=0;f.controller.inspect=async()=>{if(++inspections===2)f.projection.values.title='wrong';return {meta:{cwd:f.project},events:f.events};};assert.match((await steer(f)).items[0].error,/identity/);assert.equal(f.calls.length,0);
});
test('original resume checks exact publication inventory and latest per-operation outcome, never guesses unknown',async t=>{
  const f=await setup(t);f.setStatus('idle');
  const unknown=JSON.stringify({operation_id:'op',requirement_id:id,state:'unknown'});
  const a=await approveOriginal(f,{'publication-journal.jsonl':unknown+'\n'});assert.match((await steer(f)).items[0].error,/outcome unknown/);
  const missing=JSON.stringify({operation_id:'op',requirement_id:id})+'\n';
  await writeFile(join(a.folder,'publication-journal.jsonl'),missing);a.evidence.publication_journals['publication-journal.jsonl']=digest(missing);await writeFile(a.path,JSON.stringify(a.evidence));assert.match((await steer(f)).items[0].error,/outcome unknown/);
  const received=unknown+'\n'+JSON.stringify({operation_id:'op',requirement_id:id,state:'received'})+'\n';
  await writeFile(join(a.folder,'publication-journal.jsonl'),received);assert.match((await steer(f)).items[0].error,/journal changed/);
  a.evidence.publication_journals['publication-journal.jsonl']=digest(received);await writeFile(a.path,JSON.stringify(a.evidence));
  await writeFile(join(a.folder,'publication-extra.json'),'{}');assert.match((await steer(f)).items[0].error,/inventory incomplete/);
  await rm(join(a.folder,'publication-extra.json'));assert.equal((await steer(f)).sent,1);
});

test('journal exists in sending phase before Host prompt',async t=>{
  const f=await setup(t);f.controller.prompt=async request=>{const path=join(f.project,'work/fireplay-production/session-control',digest('Fixed reviewed control'),id+'.json');const state=JSON.parse(await readFile(path));assert.equal(state.stage,'sending');assert.equal(state.request_id,request.requestId);return {accepted:true};};await steer(f);
});
