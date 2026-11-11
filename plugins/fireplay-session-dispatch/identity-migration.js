import {createHash, randomUUID} from 'node:crypto';
import {readFile, realpath, mkdir, open, rename, unlink, readdir} from 'node:fs/promises';
import {resolve, relative, isAbsolute, sep, dirname} from 'node:path';
import {PROJECT, MANIFEST, MANIFEST_SHA256} from './dispatch.js';
const hash=value=>createHash('sha256').update(value).digest('hex');
const freeze=value=>hash(JSON.stringify(value));
const uuidPattern=/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const fail=(ok,message)=>{if(!ok)throw Error(message);};
export const identityParameters={type:'object',properties:{action:{type:'string',enum:['preview','migrate','inspect']},requirement_id:{type:'string',pattern:uuidPattern.source},review_sha256:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['action','requirement_id'],additionalProperties:false};

// No prompt/create/resume methods are referenced. Existing dispatch bytes remain immutable.
export function createIdentityMigration({controller,registry,getStatus,project=PROJECT,manifest=MANIFEST,manifestSha256=MANIFEST_SHA256,expectedCount=37}) {
 const root='work/fireplay-production';
 async function inside(path){const requested=resolve(project,path),base=await realpath(project),full=await realpath(requested),rel=relative(base,full);fail(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith(`..${sep}`),'Identity path leaves project');fail(resolve(full).toLowerCase()===requested.toLowerCase(),'Identity path link/alias forbidden');return full;}
 async function read(path){const bytes=await readFile(await inside(path));return {bytes,value:JSON.parse(bytes)};}
 async function optional(path){try{return await read(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
 async function output(path){const full=resolve(project,path);let ancestor=dirname(full);for(;;){try{await inside(ancestor);break;}catch(e){if(e.code!=='ENOENT')throw e;const parent=dirname(ancestor);fail(parent!==ancestor,'No verified output ancestor');ancestor=parent;}}await mkdir(dirname(full),{recursive:true});await inside(dirname(full));return full;}
 async function persist(path,value,{exclusive=false}={}){const target=await output(path),temp=exclusive?target:`${target}.${randomUUID()}.tmp`;const h=await open(temp,'wx');try{await h.writeFile(JSON.stringify(value,null,2)+'\n');await h.sync();}finally{await h.close();}if(!exclusive)await rename(temp,target);}
 async function original(id){
  const m=await read(manifest);fail(hash(m.bytes)===manifestSha256,'Reviewed manifest changed');
  const records=m.value.records;fail(Array.isArray(records)&&records.length===expectedCount&&new Set(records.map(r=>r.id)).size===expectedCount,'Invalid fixed manifest');
  const row=records.find(r=>r.id===id);fail(row,'Requirement outside fixed manifest');
  const loaded=await read(`${root}/session-dispatch/${id}.json`),j=loaded.value,s=j.snapshot;
  fail(j.stage==='accepted'&&s?.requirement_id===row.id&&s.code===row.code&&s.manifest_sha256===manifestSha256&&freeze(s)===j.snapshot_sha256&&/^session-[0-9a-f-]{36}$/i.test(j.session_id)&&uuidPattern.test(j.request_id),'Legacy journal identity/hash/acceptance mismatch');
  fail(s.title===row.title||s.title===row.code,'Legacy title is outside frozen identity');
  return {row,j,bytes:loaded.bytes,journal_sha256:hash(loaded.bytes)};
 }
 async function publication(row){
  const folder=`${root}/${row.code}`,names=(await readdir(await inside(folder))).filter(n=>/^publication.*\.(json|jsonl)$/.test(n)).sort(),inventory={};
  for(const name of names){const data=await readFile(await inside(`${folder}/${name}`));inventory[name]=hash(data);const entries=name.endsWith('.jsonl')?data.toString('utf8').split(/\r?\n/).filter(v=>v.trim()).map(v=>JSON.parse(v)):[JSON.parse(data)];const latest=new Map();
   for(const entry of entries){fail((!entry.requirement_id||entry.requirement_id===row.id)&&(!entry.code||entry.code===row.code),'Publication identity mismatch');fail(!name.endsWith('.jsonl')||entry.operation_id,'Publication operation identity missing');latest.set(entry.operation_id??name,entry);}
   for(const entry of latest.values()){const state=entry.state??entry.stage;fail(entry.inflight_unknown!==true&&(state?['received','completed','verified','failed','reconciled','not_started'].includes(state):entry.remote_write_attempted===false&&!name.endsWith('.jsonl')),'Publication outcome unknown');}
  }
  return inventory;
 }
 async function observe(o){
  fail(Array.isArray(registry.archivedSessionIds),'Archive observer unavailable');const archived=registry.archivedSessionIds.includes(o.j.session_id);
  const same=(a,b)=>typeof a==='string'&&resolve(a).toLowerCase()===resolve(b).toLowerCase();
  const workspaces=registry.list().filter(w=>same(w.path,project));fail(workspaces.length===1&&workspaces[0].sessionIds?.includes(o.j.session_id),'Original session not exact project member');
  const observation=await controller.inspect(o.j.session_id),p=await controller.projections({sessionId:o.j.session_id},new AbortController().signal);
  fail(same(observation?.meta?.cwd,project)&&!observation.meta.parentSessionId&&p?.kind==='sequenced'&&Object.hasOwn(p.values??{},'subagent')&&p.values.subagent===null,'Original session is not ordinary same-project');
  const status=getStatus(o.j.session_id);fail(['idle','inactive'].includes(status),'Original session must be positively idle/inactive');
  fail(p.values.inbox?.['next-turn']?.length===0&&p.values.inbox?.['next-step']?.length===0,'Original inbox not positively empty');
  const events=observation.events??[],titleEvent=events.filter(e=>e.type==='session/title').at(-1);
  fail(titleEvent&&Number.isFinite(titleEvent.seq)&&typeof titleEvent.data?.title==='string'&&titleEvent.data.title===p.values.title,'Title sequence evidence unavailable');
  fail([o.row.title,o.row.code].includes(p.values.title),'Observed title is outside legacy/code identity');
  fail(events.some(e=>e.type==='user/message'&&e.data?.source?.rpcId===o.j.request_id),'Original accepted rpcId evidence unavailable');
  return {session_id:o.j.session_id,title:p.values.title,title_seq:titleEvent.seq,status,archived,inbox_next_turn:0,inbox_next_step:0,ordinary:true};
 }
 async function plan(o){const observation=await observe(o),inventory=await publication(o.row);return {requirement_id:o.row.id,code:o.row.code,original_session_id:o.j.session_id,original_request_id:o.j.request_id,original_journal_sha256:o.journal_sha256,original_snapshot_sha256:o.j.snapshot_sha256,manifest_sha256:manifestSha256,observation,publication_journals:inventory,target_title:o.row.code,effective_snapshot_sha256:freeze({...o.j.snapshot,title:o.row.code})};}
 function reviewValid(review,p,args){fail(args.review_sha256===hash(review.bytes),'Identity review hash mismatch');const r=review.value;fail(r.reviewed_by==='parent'&&typeof r.evidence_note==='string'&&r.evidence_note.trim()&&Number.isFinite(Date.parse(r.reviewed_at))&&r.temporary_worker_occupies_slot===false&&r.inflight_unknown===false&&r.plan_sha256===freeze(r.plan)&&freeze(r.plan)===freeze(p),'Fresh parent identity review mismatch');return r;}
 function snapshot(o,p,seq){const normalized={...o.j.snapshot,title:o.row.code};return {kind:'original-session-title-migration',stage:'verified',requirement_id:o.row.id,code:o.row.code,session_id:o.j.session_id,original_request_id:o.j.request_id,original_journal_sha256:o.journal_sha256,original_snapshot_sha256:o.j.snapshot_sha256,manifest_sha256:manifestSha256,plan_sha256:freeze(p),original_archive_state:p.observation.archived,verified_title_seq:seq,effective_snapshot:normalized,effective_snapshot_sha256:freeze(normalized)};}
 async function unchanged(o){const fresh=await original(o.row.id);fail(fresh.journal_sha256===o.journal_sha256,'Original journal changed during migration');}
 async function execute(args){
  fail(args&&['preview','migrate','inspect'].includes(args.action)&&Object.keys(args).every(k=>['action','requirement_id','review_sha256'].includes(k))&&uuidPattern.test(args.requirement_id),'Unsupported identity migration arguments');
  if(args.review_sha256!==undefined)fail(/^[0-9a-f]{64}$/.test(args.review_sha256),'Invalid identity review SHA');
  const o=await original(args.requirement_id),operationPath=`${root}/session-identity-migration/${o.row.id}.json`,sidecarPath=`${root}/session-identity-migration/verified/${o.row.id}.json`;
  if(args.action==='preview'){const p=await plan(o);return {action:'preview',plan:p,plan_sha256:freeze(p),review_path:`${root}/session-identity-review/${o.row.id}.json`,rename_required:p.observation.title!==o.row.code,production_slots_added:0};}
  if(args.action==='inspect'){
   const stored=await optional(operationPath);if(!stored)return {action:'inspect',stage:'not_started',requirement_id:o.row.id};
   const lockPath=await output(`${root}/session-identity-migration/${o.row.id}.lock`);let lock;try{lock=await open(lockPath,'wx');}catch(e){if(e.code==='EEXIST')return {action:'inspect',stage:stored.value.stage,lock_present:true,reconciliation_committed:false,reason:'Lock retained; parent must verify dead owner before any separate lock recovery',fresh_observation:await plan(o)};throw e;}
   try{
   const current=await read(operationPath),op=current.value;fail(op.original_journal_sha256===o.journal_sha256&&op.original_snapshot_sha256===o.j.snapshot_sha256&&op.session_id===o.j.session_id&&op.original_request_id===o.j.request_id&&hash(Buffer.from(op.original_journal_base64??'','base64'))===o.journal_sha256,'Migration belongs to another original identity');
   const reviewed=await read(`${root}/session-identity-review/${o.row.id}.json`);reviewValid(reviewed,op.plan,{review_sha256:op.review_sha256});fail(op.plan_sha256===freeze(op.plan)&&op.plan.original_journal_sha256===o.journal_sha256&&op.plan.original_snapshot_sha256===o.j.snapshot_sha256&&op.plan.original_session_id===o.j.session_id&&op.plan.original_request_id===o.j.request_id&&op.plan.requirement_id===o.row.id&&op.plan.code===o.row.code&&op.plan.target_title===o.row.code,'Migration frozen plan mismatch');
   if(['renaming','rename_unknown','renamed_verified'].includes(op.stage)){
    const now=await plan(o),before=op.plan;fail(now.observation.archived===before.observation.archived&&freeze(now.publication_journals)===freeze(before.publication_journals),'Migration reconciliation evidence changed');
    if(now.observation.title===o.row.code&&now.observation.title_seq>=before.observation.title_seq&&(before.observation.title===o.row.code||now.observation.title_seq>before.observation.title_seq)){
     await unchanged(o);const effective=snapshot(o,before,now.observation.title_seq);effective.review_sha256=op.review_sha256;await persist(sidecarPath,effective);op.stage='effective';op.reconciled=true;await persist(operationPath,op);
    }
   }
   return {action:'inspect',...op};
   }finally{await lock.close();await unlink(lockPath);}
  }
  fail(args.review_sha256,'migrate requires parent review SHA');
  const lockPath=await output(`${root}/session-identity-migration/${o.row.id}.lock`);let lock;
  try{lock=await open(lockPath,'wx');}catch(e){if(e.code==='EEXIST')throw Error('Identity migration lock exists; inspect only');throw e;}
  try{
   const existing=await optional(operationPath);if(existing)return {action:'migrate',skipped:true,reason:'migration_already_attempted_inspect_only',stage:existing.value.stage};
   const p=await plan(o),review=await read(`${root}/session-identity-review/${o.row.id}.json`);reviewValid(review,p,args);await output(sidecarPath);
   const op={kind:'original-session-title-migration',requirement_id:o.row.id,code:o.row.code,session_id:o.j.session_id,original_request_id:o.j.request_id,original_journal_sha256:o.journal_sha256,original_snapshot_sha256:o.j.snapshot_sha256,original_journal_base64:o.bytes.toString('base64'),review_sha256:args.review_sha256,plan:p,plan_sha256:freeze(p),stage:'reviewed',updated_at:new Date().toISOString()};
   await persist(operationPath,op,{exclusive:true});
   await unchanged(o);fail(freeze(await plan(o))===freeze(p),'Original observation changed before rename');
   op.stage='renaming';await persist(operationPath,op);
   try{
    if(p.observation.title!==o.row.code){const answer=await controller.rename({sessionId:o.j.session_id,title:o.row.code});fail(answer?.title===o.row.code&&Number.isFinite(answer.seq),'Official rename exact result unavailable');op.rename_seq=answer.seq;}
    const after=await plan(o);fail(after.observation.title===o.row.code&&after.observation.archived===p.observation.archived&&freeze(after.publication_journals)===freeze(p.publication_journals)&&(p.observation.title===o.row.code||after.observation.title_seq>p.observation.title_seq)&&(!op.rename_seq||after.observation.title_seq===op.rename_seq),'Rename postcondition not verified');
    await unchanged(o);op.stage='renamed_verified';await persist(operationPath,op);
    const effective=snapshot(o,p,after.observation.title_seq);effective.review_sha256=args.review_sha256;await persist(sidecarPath,effective);op.stage='effective';op.updated_at=new Date().toISOString();await persist(operationPath,op);
   }catch(e){op.stage='rename_unknown';op.error=e.message;await persist(operationPath,op);}
   return {action:'migrate',stage:op.stage,requirement_id:o.row.id,session_id:o.j.session_id,production_slots_added:0};
  }finally{await lock.close();await unlink(lockPath);}
 }
 return {execute};
}

// Control's legacy-title-only branch calls this after fresh official
// ordinary/project/code-title/archive observations; original bytes stay intact.
export function verifyEffectiveIdentity({row,journal,journalBytes,sidecar,operation,reviewBytes,manifestSha256,fresh}){
 const s=journal.snapshot,r=JSON.parse(reviewBytes);
 fail(journal.stage==='accepted'&&s.requirement_id===row.id&&s.code===row.code&&s.manifest_sha256===manifestSha256&&freeze(s)===journal.snapshot_sha256,'Original identity invalid');
 fail(sidecar?.stage==='verified'&&operation?.stage==='effective'&&sidecar.original_journal_sha256===hash(journalBytes)&&operation.original_journal_sha256===hash(journalBytes)&&sidecar.original_snapshot_sha256===journal.snapshot_sha256&&operation.original_snapshot_sha256===journal.snapshot_sha256,'Migration evidence integrity mismatch');
 fail(sidecar.requirement_id===row.id&&operation.requirement_id===row.id&&sidecar.session_id===journal.session_id&&operation.session_id===journal.session_id&&sidecar.original_request_id===journal.request_id&&operation.original_request_id===journal.request_id&&sidecar.code===row.code&&sidecar.manifest_sha256===manifestSha256,'Migration identity mismatch');
 fail(hash(reviewBytes)===sidecar.review_sha256&&operation.review_sha256===sidecar.review_sha256&&r.reviewed_by==='parent'&&r.temporary_worker_occupies_slot===false&&r.inflight_unknown===false&&r.evidence_note?.trim()&&Number.isFinite(Date.parse(r.reviewed_at))&&freeze(r.plan)===r.plan_sha256&&r.plan_sha256===sidecar.plan_sha256&&r.plan_sha256===operation.plan_sha256&&freeze(operation.plan)===r.plan_sha256,'Migration parent review chain invalid');
 const p=r.plan;fail(p.requirement_id===row.id&&p.code===row.code&&p.original_session_id===journal.session_id&&p.original_request_id===journal.request_id&&p.original_journal_sha256===hash(journalBytes)&&p.original_snapshot_sha256===journal.snapshot_sha256&&p.manifest_sha256===manifestSha256&&p.target_title===row.code&&p.effective_snapshot_sha256===sidecar.effective_snapshot_sha256&&p.observation?.session_id===journal.session_id&&[row.title,row.code].includes(p.observation.title)&&p.observation.archived===sidecar.original_archive_state&&hash(Buffer.from(operation.original_journal_base64??'','base64'))===hash(journalBytes)&&Number.isFinite(sidecar.verified_title_seq)&&sidecar.verified_title_seq>=p.observation.title_seq&&(p.observation.title===row.code||sidecar.verified_title_seq>p.observation.title_seq),'Reviewed plan subject/sequence mismatch');
 const expected={...s,title:row.code};fail(freeze(expected)===sidecar.effective_snapshot_sha256&&freeze(sidecar.effective_snapshot)===freeze(expected),'Normalized identity mismatch');
 fail(fresh?.ordinary===true&&fresh.project_verified===true&&fresh.session_id===journal.session_id&&fresh.title===row.code&&fresh.archived===sidecar.original_archive_state&&fresh.title_seq>=sidecar.verified_title_seq,'Fresh ordinary code identity required');
 return expected;
}
