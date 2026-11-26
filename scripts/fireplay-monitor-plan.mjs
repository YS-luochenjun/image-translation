#!/usr/bin/env node
import {createHash,randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {readFile,writeFile,rename,open,unlink} from 'node:fs/promises';
import {isDeepStrictEqual} from 'node:util';
import {dirname,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {decodeResult} from './fireplay-mcp-publication.mjs';
import {nextCursor,verifyPage} from './fireplay-discover-translations.mjs';

const fail=message=>{throw new Error(message);};
const sleep=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
const closed=new Set(['done','cancelled','canceled']);
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?
 Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const digest=value=>createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const timestamp=value=>{const result=Date.parse(value);if(typeof value!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(result))fail('Invalid observation time');return result;};
const fresh=(value,now)=>{const age=timestamp(now)-timestamp(value);if(age<0||age>30*60*1000)fail('Receipt serverTime must be within the current 30-minute observation window, never in the future');};
const summaryHash=row=>digest(Object.fromEntries(Object.entries(row).filter(([key])=>!['observedAt','serverTime'].includes(key))));
const policy=JSON.parse(readFileSync(new URL('../config/fireplay-monitor.json',import.meta.url),'utf8'));

function activeRequirements(assignments, admissions){
 const active=new Set();
 const identities=new Set([...Object.keys(assignments),...Object.keys(admissions)]);
 for(const id of identities){
  const assignment=assignments[id]||{},admission=admissions[id]||{};
  const unresolved=assignment.unknown_operation||assignment.pending_operation||
   ['unknown','dispatch_unknown'].includes(assignment.state);
  const ended=admission.state==='released'||assignment.delivery_verified_by_controller||
   policy.capacity_terminal_states.includes(assignment.state);
  const started=admission.state==='reserved'||assignment.responsible_thread_id||
   policy.capacity_active_states.includes(assignment.state);
  if(unresolved||(!ended&&started))active.add(id);
 }
 return active;
}

export function emptyMonitorState(){return {schema_version:1,checkpoints:{new:null,active:null,full:null,tracked:{}},summaries:{},pending_briefs:{}};}

export function monitorQuery(mode,aiMode,ids=[]){
 if(!['new','active','full','tracked'].includes(mode))fail('Invalid scan mode');
 const query={scope:'all',view:'summary',pagination:'cursor',limit:mode==='tracked'?7:mode==='full'?10:40,offset:0};
 if(mode==='tracked'){
  if(!ids.length||ids.length>40||new Set(ids).size!==ids.length||ids.some(id=>!uuid.test(id)))fail('Tracked query needs 1..40 exact unique UUIDs');
  query.requirement_ids=ids;
 }else{
  if(typeof aiMode!=='string'||!/^[a-z][a-z0-9_]{0,47}$/.test(aiMode))fail('Resolve the current official AI mode before planning queries');
  Object.assign(query,{asset_type:['image','button'],ai_mode:aiMode});
  if(mode==='active')query.status=['not_started','in_progress'];
  if(mode==='new')query.status=['not_started'];
 }
 return query;
}

export function planMonitor({state,assignments,admissions={requirements:{}},now,aiMode}){
 const current=timestamp(now),previous=state.checkpoints.full;
 const fullDue=!previous||current-timestamp(previous)>=24*60*60*1000;
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(current));
 const cutoff=Date.parse(`${today}T00:00:00Z`)-3*86400000;
 const completeStates=new Set(['verified_complete','delivery_verified']);
 const excluded=id=>{
  const entry=assignments.requirements[id]||{},pending=state.pending_briefs[id]||{},updates=state.pending_brief_updates?.[id]||[];
  if(entry.state==='excluded_user_instruction')return true;
  const unresolved=entry.unknown_operation||entry.pending_operation||entry.state==='unknown';
  if(!unresolved&&(entry.delivery_verified_by_controller===true||completeStates.has(entry.state)))return true;
  const raw=entry.requestDate||entry.request_date||entry.code||pending.code||updates[0]?.code||
   state.summaries[id]?.code||admissions.requirements[id]?.code||'';
  const match=String(raw).match(/(20\d{2})-?(\d{2})-?(\d{2})/);
  if(!match)return false;
  const calendarDate=`${match[1]}-${match[2]}-${match[3]}`;
  const date=Date.parse(`${calendarDate}T00:00:00Z`);
  return Number.isFinite(date)&&new Date(date).toISOString().startsWith(calendarDate)&&date<cutoff;
 };
 const active=activeRequirements(assignments.requirements,admissions.requirements);
 const tracked=new Set([...Object.keys(state.pending_briefs),...Object.keys(state.pending_brief_updates||{}),
  ...[...active].filter(id=>admissions.requirements[id]?.state==='reserved')].filter(id=>!excluded(id)));
 const chats=new Map();
 const ownerCloseouts=[];
 for(const [id,summary] of Object.entries(state.summaries)){
  if(excluded(id))continue;
  const entry=assignments.requirements[id];
  if(!closed.has(summary.status)&&(!entry||(!entry.delivery_verified_by_controller&&!['excluded_closed','excluded_out_of_scope','verified_complete'].includes(entry.state))))tracked.add(id);
 }
 for(const [id,entry] of Object.entries(assignments.requirements)){
  if(excluded(id))continue;
  const owner=entry.responsible_thread_id;
  const unknown=entry.unknown_operation||entry.pending_operation||entry.state==='unknown';
  const verified=entry.delivery_verified_by_controller===true&&!unknown;
  if(!verified&&(owner||unknown||!['excluded_closed','excluded_out_of_scope','verified_complete'].includes(entry.state)))tracked.add(id);
  if(owner&&!verified)chats.set(owner,{
   threadId:owner,
   hostId:entry.host_id||'local',
   ...(entry.wait_cursor?{afterCursor:entry.wait_cursor}:{}),
   ...(entry.submitted_materials_frozen?{mode:'read_only_reconciliation'}:{})
  });
  const summaryStatus=state.summaries[id]?.status??entry.platform_status;
  if(entry.state==='awaiting_owner_closeout'&&summaryStatus==='in_progress'&&owner&&!unknown&&
     ['idle','error','notLoaded'].includes(entry.last_chat_status)&&entry.last_turn_status&&
     entry.last_turn_status!=='inProgress'&&!entry.owner_closeout_handoff){
   ownerCloseouts.push({
    requirement_id:id,
    code:entry.code,
    threadId:owner,
    hostId:entry.host_id||'local',
    signal:`owner-closeout:${id}:${owner}`
   });
  }
 }
 const ids=[...tracked].sort();
 if(ids.some(id=>!uuid.test(id)))fail('Invalid tracked requirement identity');
 const available=Math.max(0,policy.max_active_requirements-active.size);
 const candidates=ids.filter(id=>{
  const entry=assignments.requirements[id]||{};
  const summary=state.summaries[id];
  return summary?.status==='not_started'&&summary.ai_mode===aiMode&&['image','button'].includes(summary.asset_type)&&
    !entry.responsible_thread_id&&
    !entry.unknown_operation&&!entry.pending_operation&&
    !['unknown','identity_pending','dispatched','dispatch_unknown','excluded_closed','excluded_out_of_scope'].includes(entry.state);
 });
 const priority={P0:0,P1:1,P2:2,P3:3};
 candidates.sort((left,right)=>{
  const before=state.summaries[left],after=state.summaries[right];
  return (priority[before.priority]??4)-(priority[after.priority]??4)||
   String(before.created_at||before.updated_at).localeCompare(String(after.created_at||after.updated_at))||
   before.code.localeCompare(after.code);
 });
 const unreserved=candidates.filter(id=>!active.has(id));
 const eligible=new Set(unreserved.slice(0,available));
 const candidateIds=new Set(candidates);
 const continuation=ids.filter(id=>admissions.requirements[id]?.state==='reserved'&&active.has(id)&&
  !assignments.requirements[id]?.responsible_thread_id&&!candidateIds.has(id));
 return {full_due:fullDue,discovery_mode:fullDue?'full':'new',discovery_query:monitorQuery(fullDue?'full':'new',aiMode),
  new_candidates:candidates.filter(id=>active.has(id)||eligible.has(id)),
  continuation_candidates:continuation,
  owner_closeout_candidates:ownerCloseouts,
  capacity:{limit:policy.max_active_requirements,active:active.size,available,queued:Math.max(0,unreserved.length-available)},
  queued_candidates:unreserved.slice(available,available+8),
  tracked_queries:Array.from({length:Math.ceil(ids.length/40)},(_,index)=>monitorQuery('tracked',aiMode,ids.slice(index*40,index*40+40))),
  brief_candidates:[...new Set([...Object.keys(state.pending_briefs),...Object.keys(state.pending_brief_updates||{})])].filter(id=>!excluded(id)),
  pending_brief_updates:Object.fromEntries(Object.entries(structuredClone(state.pending_brief_updates||{})).filter(([id])=>!excluded(id))),chat_targets:[...chats.values()],
  skip_chat_poll:chats.size===0,followup_automation:'PAUSED',live_pagination_is_snapshot:false};
}

export function acceptMonitorScan({state,receipt,mode,aiMode,ids=[],evidence,now=new Date().toISOString()}){
 if(typeof evidence!=='string'||!evidence.trim())fail('Keep the original receipt path');
 const query=monitorQuery(mode,aiMode,ids),pages=receipt.summary_pages;
 if(!isDeepStrictEqual(receipt.query,query)||!Array.isArray(pages)||!pages.length)fail('Receipt query does not match the planned scan');
 const rows=[],seenIds=new Set(),seenCodes=new Set(),cursors=new Set();
 let cursor,serverTime;
 for(const [index,page] of pages.entries()){
  if(index&&cursor===null)fail('Unexpected page after terminal cursor');
  if(!isDeepStrictEqual(page.arguments,{...query,...(cursor?{cursor}:{})}))fail('Incomplete or changed cursor chain');
  if(page.response?.isError)fail('Failed read cannot advance a checkpoint');
  const response=decodeResult(page.response);
  verifyPage(response,query);
  fresh(response.serverTime,now);
  if(response.items.length>query.limit||(response.query_meta.limit!==undefined&&response.query_meta.limit!==query.limit))fail('Response page limit does not match query');
  const next=nextCursor(response);
  if(next!==null&&cursors.has(next))fail('Repeated cursor');
  cursors.add(next);cursor=next;
  if(serverTime&&timestamp(response.serverTime)<timestamp(serverTime))fail('Observation times moved backwards');
  serverTime=response.serverTime;
  for(const row of response.items){
   if(!uuid.test(row.id)||typeof row.code!=='string'||!row.code.trim()||seenIds.has(row.id.toLowerCase())||seenCodes.has(row.code))fail('Invalid or duplicate requirement identity');
   if(mode==='tracked'?!ids.includes(row.id):row.aiMode!==aiMode||!query.asset_type.includes(row.assetType)||(query.status&&!query.status.includes(row.status)))fail('Row does not match scan filters');
   if(timestamp(row.updatedAt)>timestamp(response.serverTime))fail('updatedAt cannot be later than serverTime');
   seenIds.add(row.id.toLowerCase());seenCodes.add(row.code);rows.push(row);
  }
 }
 if(cursor!==null)fail('Partial scan cannot advance a checkpoint');
 const batch=mode==='tracked'?digest([...ids].sort()):null;
 const checkpoint=batch?state.checkpoints.tracked[batch]?.at:state.checkpoints[mode];
 if(checkpoint&&timestamp(serverTime)<timestamp(checkpoint))fail('Stale receipt cannot rewind a checkpoint');
 const updated=structuredClone(state);
 for(const row of rows){
  const hash=summaryHash(row),previous=updated.summaries[row.id];
  if(previous&&(timestamp(row.updatedAt)<timestamp(previous.updated_at)||(previous.observed_at&&timestamp(serverTime)<timestamp(previous.observed_at))))fail('Older observation cannot replace a newer summary from another scan mode');
  if((!closed.has(row.status)||mode==='tracked'||updated.pending_briefs[row.id]||updated.pending_brief_updates?.[row.id]?.length)&&(!previous||previous.hash!==hash)){
   const candidate={code:row.code,summary_hash:hash,updated_at:row.updatedAt,evidence},pending=updated.pending_briefs[row.id];
   if(!pending)updated.pending_briefs[row.id]=candidate;
   else if(pending.summary_hash!==hash){
    updated.pending_brief_updates??={};
    const versions=updated.pending_brief_updates[row.id]??=[];
    if(!versions.some(item=>item.summary_hash===hash))versions.push(candidate);
   }
  }
  updated.summaries[row.id]={...previous,code:row.code,requestDate:row.requestDate,hash,updated_at:row.updatedAt,
   priority:row.priority,created_at:row.createdAt,
   status:row.status,ai_mode:row.aiMode,asset_type:row.assetType,evidence,observed_at:serverTime};
 }
 if(batch)updated.checkpoints.tracked[batch]={at:serverTime,ids,evidence};
 else updated.checkpoints[mode]=serverTime;
 updated.last_receipts={...updated.last_receipts,[mode]:evidence};
 if(mode==='tracked')updated.tracked_missing={...updated.tracked_missing,[batch]:ids.filter(id=>!seenIds.has(id.toLowerCase()))};
 return updated;
}

export function acknowledgeMonitorBrief({state,id,evidence,receipt,now=new Date().toISOString()}){
 const primary=state.pending_briefs[id],versions=state.pending_brief_updates?.[id]||[];
 const pending=primary?.summary_hash===receipt.summary_hash?primary:versions.find(item=>item.summary_hash===receipt.summary_hash);
 if(!pending||!evidence)fail('Brief evidence must match the pending summary version');
 for(const view of ['blind_brief','delivery']){
  if(receipt[view]?.isError)fail('Failed brief/delivery receipt');
  const response=decodeResult(receipt[view]);
  fresh(response.serverTime,now);
  if(response.view!==view||response.scope!=='all'||!Array.isArray(response.items)||response.items.length!==1||response.items[0].id!==id||response.items[0].code!==pending.code)fail('Need exact UUID brief and delivery receipts');
  if(response.items[0].updatedAt!==pending.updated_at)fail('Requirement changed while reading brief/delivery; refresh summary');
  if(timestamp(response.items[0].updatedAt)>timestamp(response.serverTime))fail('updatedAt cannot be later than serverTime');
 }
 const updated=structuredClone(state);
 if(updated.summaries[id]?.hash===pending.summary_hash)updated.summaries[id].review_evidence=evidence;
 else{
  updated.brief_review_history??=[];
  updated.brief_review_history.push({id,summary_hash:pending.summary_hash,evidence});
 }
 if(primary===pending)delete updated.pending_briefs[id];
 else{
  updated.pending_brief_updates[id]=versions.filter(item=>item.summary_hash!==pending.summary_hash);
  if(!updated.pending_brief_updates[id].length)delete updated.pending_brief_updates[id];
 }
 return updated;
}

export async function readJsonStable(path,{attempts=8,delayMs=15}={}){
 let lastError;
 for(let attempt=0;attempt<attempts;attempt++){
  try{return JSON.parse(await readFile(path,'utf8'));}
  catch(error){
   if(error.code==='ENOENT')throw error;
   lastError=error;
   if(error instanceof SyntaxError&&attempt<attempts-1){await sleep(delayMs);continue;}
   throw error;
  }
 }
 throw lastError;
}

async function main(){
 const flags=new Map(),input=process.argv.slice(2);
 for(let index=0;index<input.length;index+=2){
  if(!['--state','--assignments','--now','--ai-mode','--receipt','--mode','--ids','--acknowledge','--review'].includes(input[index])||!input[index+1]||flags.has(input[index]))fail('Invalid monitor arguments');
  flags.set(input[index],input[index+1]);
 }
 const statePath=flags.get('--state'),assignmentsPath=flags.get('--assignments');
 if(!statePath||!assignmentsPath)fail('Use --state FILE --assignments FILE --ai-mode CURRENT_VALUE [--now ISO] [--receipt FILE --mode new|active|full|tracked --ids UUID,...] or [--acknowledge UUID --review FILE]');
 const mutation=flags.has('--receipt')||flags.has('--acknowledge');
 if(flags.has('--receipt')&&flags.has('--acknowledge'))fail('Accept one observation at a time');
 const lock=mutation?await open(`${statePath}.lock`,'wx'):null;
 try{
  let state;
  try{state=await readJsonStable(statePath);}catch(error){if(error.code!=='ENOENT')throw error;state=emptyMonitorState();}
  const assignments=await readJsonStable(assignmentsPath);
  let admissions;
  try{admissions=await readJsonStable(resolve(dirname(assignmentsPath),'admissions.json'));}
  catch(error){if(error.code!=='ENOENT')throw error;}
  const now=flags.get('--now')||new Date().toISOString();
  if(flags.has('--receipt'))state=acceptMonitorScan({state,receipt:await readJsonStable(flags.get('--receipt')),mode:flags.get('--mode'),aiMode:flags.get('--ai-mode'),ids:flags.get('--ids')?.split(',')||[],evidence:flags.get('--receipt'),now});
  if(flags.has('--acknowledge'))state=acknowledgeMonitorBrief({state,id:flags.get('--acknowledge'),evidence:flags.get('--review'),receipt:await readJsonStable(flags.get('--review')),now});
  const plan=planMonitor({state,assignments,admissions,now,aiMode:flags.get('--ai-mode')});
  if(mutation){
   const temporary=`${statePath}.${randomUUID()}.tmp`;
   try{await writeFile(temporary,`${JSON.stringify(state,null,2)}\n`,{flag:'wx'});await rename(temporary,statePath);}finally{await unlink(temporary).catch(error=>{if(error.code!=='ENOENT')throw error;});}
  }
  console.log(JSON.stringify(plan,null,2));
 }finally{if(lock){await lock.close();await unlink(`${statePath}.lock`);}}
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)main().catch(error=>{console.error(JSON.stringify({state:'monitor_failed',message:error.message}));process.exitCode=1;});
