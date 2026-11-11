import {createHash, randomUUID} from 'node:crypto';
import {open, readFile, realpath, rename} from 'node:fs/promises';
import {watch} from 'node:fs';
import {resolve, relative, isAbsolute, sep} from 'node:path';

// Separate one-requirement outbox. The historical fixed37 tools are unchanged.
export const CODE = 'SHE-20261105-043';
export const REQUIREMENT = '01a1ab5e-81f0-7b33-b7c8-a2d958e79b36';
export const PLAN_SHA256 = '4499e1874dd1ef14aa994f117a3b5c78b902a4766fba051123bc0ccff89420c9';
const PROJECT = 'C:/Users/Echo/Desktop/单图片翻译';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const equalPath = (a,b) => typeof a==='string' && typeof b==='string' && resolve(a).toLowerCase()===resolve(b).toLowerCase();
const assert = (condition,message) => {if(!condition)throw Error(message);};
const optional = async path => {try{return JSON.parse(await readFile(path,'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}};

export function createOrdinary043({controller,registry,getStatus=()=>null,project=PROJECT,expectedPlanSha=PLAN_SHA256,uuid=randomUUID,allowPrompt=false,admission=operation=>operation()}) {
 const folder=resolve(project,`work/fireplay-production/${CODE}`);
 const journal=resolve(folder,'ordinary-session-journal.json');
 const output=resolve(folder,'ordinary-session-result.json');
 const signal=()=>new AbortController().signal;
 async function persist(path,value){
  const temp=`${path}.${uuid()}.tmp`,fd=await open(temp,'wx');
  try{await fd.writeFile(JSON.stringify(value,null,2)+'\n','utf8');await fd.sync();}finally{await fd.close();}
  await rename(temp,path);
 }
 async function observe(){
  const matches=registry.list().filter(row=>equalPath(row.path,project));
  assert(matches.length===1,'Exact existing project is not unique');
  const workspace=matches[0];
  assert(typeof (workspace.workspaceId??workspace.id)==='string'&&Array.isArray(workspace.sessionIds)&&Array.isArray(registry.archivedSessionIds),'Workspace/archive observer unavailable');
  const rows=[];
  for(const sessionId of workspace.sessionIds){
   const projection=await controller.projections({sessionId},signal());
   assert(projection?.kind==='sequenced'&&projection.values,'Sequenced session identity unavailable');
   rows.push({sessionId,values:projection.values,archived:registry.archivedSessionIds.includes(sessionId),status:getStatus(sessionId)});
  }
  return {workspace,rows};
 }
 function ordinary(row){return Object.hasOwn(row.values,'subagent')&&row.values.subagent===null;}
 function inboxEmpty(values){return ['next-turn','next-step'].every(key=>Array.isArray(values.inbox?.[key])&&values.inbox[key].length===0);}
 async function verifyTarget(sessionId){
  const observation=await observe();
  const row=observation.rows.find(row=>row.sessionId===sessionId);
  assert(row&&!row.archived&&ordinary(row)&&row.values.title===CODE,'Target is not the exact ordinary nonarchived code-title session');
  const snapshot=await controller.inspect(sessionId);
  assert(equalPath(snapshot?.meta?.cwd,project)&&!snapshot.meta.parentSessionId,'Target directory/parent identity mismatch');
  assert(Array.isArray(snapshot.events),'Session event evidence unavailable');
  return {...observation,row,snapshot};
 }
 async function capacity(observation){
  const slots=new Set();
  for(const row of observation.rows){
   if(row.archived||!ordinary(row)||typeof row.values.title!=='string'||!/^[A-Z]+-\d{8}-\d+$/.test(row.values.title))continue;
   if(row.status==='running'||!inboxEmpty(row.values))slots.add(row.values.title);
  }
  const review=await optional(resolve(project,'work/fireplay-production/session-control-review.json'));
  if(review){
   const manifest=await optional(resolve(project,'work/fireplay-production/intake-index-20261103.json'));
   assert(Array.isArray(manifest?.records),'Historical reservation identity unavailable');
   for(const key of ['reserved_active_requirement_ids','wake_approved_ids','cold_dispatch_ids']){
    const ids=review[key]??[];assert(Array.isArray(ids),'Invalid historical reservation list');
    for(const id of ids){const record=manifest.records.find(row=>row.id===id);assert(record?.code,'Unknown historical reservation');slots.add(record.code);}
   }
  }
  return {occupied_codes:[...slots].sort(),available:slots.has(CODE)?slots.size<=4:slots.size<4};
 }
 async function execute(){
  const base=await realpath(project),actual=await realpath(folder),rel=relative(base,actual);
  assert(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith(`..${sep}`),'Outbox escapes project');
  const bytes=await readFile(resolve(folder,'ordinary-session-plan.json'));
  assert(hash(bytes)===expectedPlanSha,'Frozen 043 launch plan changed');
  const plan=JSON.parse(bytes);
  assert(plan.version===1&&plan.approved===true&&plan.requirement_id===REQUIREMENT&&plan.code===CODE&&plan.worker_stopped===true&&plan.inflight_unknown===false,'Invalid exact approved launch scope');
  assert(typeof plan.prompt==='string'&&plan.prompt.includes(REQUIREMENT)&&plan.prompt.includes(CODE),'Missing frozen production prompt');
  const handoff=await realpath(resolve(project,plan.handoff));
  assert(equalPath(handoff,resolve(folder,'HANDOFF.md'))&&hash(await readFile(handoff))===plan.handoff_sha256,'Stopped worker handoff changed');
  let state=await optional(journal);
  if(state){
   assert(state.plan_sha256===expectedPlanSha&&state.requirement_id===REQUIREMENT&&state.code===CODE,'Journal scope changed');
   if(['accepted','creating','create_unknown','renaming','rename_unknown','dispatching','dispatch_unknown','reused_existing'].includes(state.stage))return {...state,replayed:false};
   assert(state.stage==='created','Unsupported existing journal stage');
  }else{
   const observation=await observe();
   const existing=observation.rows.filter(row=>row.values.title===CODE);
   if(existing.length){
    assert(existing.length===1&&!existing[0].archived&&ordinary(existing[0]),'Duplicate/archived 043 session requires explicit review');
    const checked=await verifyTarget(existing[0].sessionId);
    const empty=checked.snapshot.events.every(event=>['session/title','session/meta'].includes(event.type))&&inboxEmpty(checked.row.values)&&checked.row.status!=='running';
    state={requirement_id:REQUIREMENT,code:CODE,plan_sha256:expectedPlanSha,session_id:existing[0].sessionId,request_id:uuid(),stage:empty?'created':'reused_existing',reused:true};
    await persist(journal,state);
    if(!empty)return {...state,replayed:false,note:'Existing nonempty session was not woken or prompted again.'};
   }else{
    state={requirement_id:REQUIREMENT,code:CODE,plan_sha256:expectedPlanSha,session_id:`session-${uuid()}`,request_id:uuid(),workspace_id:observation.workspace.workspaceId??observation.workspace.id,stage:'creating'};
    await persist(journal,state);
    try{const created=await controller.create({workspaceId:state.workspace_id,sessionId:state.session_id});assert(created?.sessionId===state.session_id,'Creation returned a different session identity');state.stage='renaming';await persist(journal,state);}
    catch(error){state.stage='create_unknown';state.error=String(error.message);await persist(journal,state);return state;}
    try{const renamed=await controller.rename({sessionId:state.session_id,title:CODE});assert(renamed?.title===CODE&&Number.isFinite(renamed.seq),'Exact title not confirmed');state.stage='created';state.rename_seq=renamed.seq;await persist(journal,state);}
    catch(error){state.stage='rename_unknown';state.error=String(error.message);await persist(journal,state);return state;}
   }
  }
  const target=await verifyTarget(state.session_id);
  if(!allowPrompt)return {...state,ordinary:true,title:CODE,workspace_member:true,awaiting_parent_admission:true,event_types:target.snapshot.events.map(event=>event.type)};
  assert(!target.snapshot.events.some(event=>['user/message','assistant/message'].includes(event.type)||event.type?.startsWith('turn/')||event.type?.startsWith('step/')),'Target already has execution; review before any new prompt');
  assert(inboxEmpty(target.row.values)&&target.row.status!=='running','Target inbox not empty or already running');
  const slots=await capacity(target);
  if(!slots.available)return {...state,ordinary:true,title:CODE,workspace_member:true,capacity:slots,waiting_for_capacity:true};
  state.stage='dispatching';state.prompt_sha256=hash(plan.prompt);await persist(journal,state);
  try{
   const sent=await controller.prompt({requestId:state.request_id,sessionId:state.session_id,mode:'queue',content:[{type:'text',text:plan.prompt}],clientTimeZone:'Asia/Shanghai'},signal());
   assert(sent?.accepted===true,'Prompt acceptance unavailable');state.stage='accepted';state.accepted_at=new Date().toISOString();await persist(journal,state);
  }catch(error){state.stage='dispatch_unknown';state.error=String(error.message);await persist(journal,state);}
  return {...state,ordinary:true,title:CODE,workspace_member:true,capacity:slots,agent_status:getStatus(state.session_id)};
 }
 let queue=Promise.resolve();
 return {run(){const call=queue.then(()=>admission(async()=>{
   let lock;
   try{lock=await open(resolve(folder,'ordinary-session-launch.lock'),'wx');}
   catch(error){if(error.code==='EEXIST')return {stage:'another_launcher_owns_lock'};throw error;}
   try{const result=await execute();await persist(output,result);return result;}
   catch(error){const result={stage:'launch_failed',requirement_id:REQUIREMENT,code:CODE,error:String(error.message)};await persist(output,result);return result;}
   finally{await lock.close();const {unlink}=await import('node:fs/promises');await unlink(resolve(folder,'ordinary-session-launch.lock'));}
  }));queue=call.catch(()=>{});return call;}};
}

// A one-requirement command outbox on the existing project Host. No HTTP route,
// credentials, profile editing, fixed37 tool widening, or application restart.
export function startOrdinary043(options){
 const launcher=createOrdinary043(options),folder=resolve(options.project??PROJECT,`work/fireplay-production/${CODE}`);
 const run=()=>launcher.run().catch(error=>options.onError?.(error));
 const watcher=watch(folder,{persistent:false},(_event,name)=>{if(['ordinary-session-plan.json','ordinary-session.trigger'].includes(String(name)))void run();});
 void run();
 return ()=>watcher.close();
}
