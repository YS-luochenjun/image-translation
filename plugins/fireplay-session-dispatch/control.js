import {ordinary043Slot} from './ordinary-slot.js?fixed043-v1';
import {createHash, randomUUID} from 'node:crypto';
import {mkdir, open, readFile, readdir, realpath, rename} from 'node:fs/promises';
import {isAbsolute, relative, resolve, sep} from 'node:path';
import {validateReadOnlyDeclarations,verifyReadOnlyArtifact} from './publication-readonly.js?audited-artifacts-v5';
import {verifyEffectiveIdentity} from './identity-migration.js?verified-chain-v5';
import {historicalEvidenceNames,verifyHistoricalArtifact} from './publication-historical.js?reviewed-historical-v1';
import {PROJECT, MANIFEST, MANIFEST_SHA256} from './dispatch.js';
const hash = value => createHash('sha256').update(value).digest('hex');
const samePath = (a,b) => typeof a === 'string' && resolve(a).toLowerCase() === resolve(b).toLowerCase();
export const CONTROL_FILE = 'work/fireplay-production/unified-control.md';
export const controlParameters = {type:'object',properties:{action:{type:'string',enum:['preview','steer','inspect']},requirement_ids:{type:'array',items:{type:'string'},minItems:1,maxItems:37,uniqueItems:true},control_sha256:{type:'string',pattern:'^[0-9a-f]{64}$'}},required:['action'],additionalProperties:false};

export function createDispatchExecutor({dispatcher,control,project=PROJECT}) {
  return async args => {
    if(args.action!=='dispatch')return dispatcher.execute(args);
    if(typeof args.requirement_id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(args.requirement_id))throw Error('Invalid requirement identity');
    let state;
    try{state=JSON.parse(await readFile(resolve(project,'work/fireplay-production/session-dispatch',`${args.requirement_id}.json`),'utf8'));}
    catch(error){if(error.code!=='ENOENT')throw error;}
    if(state?.stage!=='accepted'){
      if(typeof control.dispatchCold!=='function')throw Error('Cold dispatch admission unavailable');
      return control.dispatchCold(args,()=>dispatcher.execute(args));
    }
    if(state.snapshot_sha256!==args.snapshot_sha256||hash(JSON.stringify(state.snapshot))!==state.snapshot_sha256)throw Error('Original dispatch snapshot hash mismatch');
    const review=JSON.parse(await readFile(resolve(project,'work/fireplay-production/session-control-review.json'),'utf8'));
    if(!Array.isArray(review.requirement_ids)||review.requirement_ids.length>4||new Set(review.requirement_ids).size!==review.requirement_ids.length||!review.requirement_ids.includes(args.requirement_id))throw Error('Requirement not in reviewed active control batch');
    return control.execute({action:'steer',requirement_ids:[args.requirement_id],control_sha256:review.control_sha256});
  };
}

export function createControl({controller,registry,getStatus,project=PROJECT,manifest=MANIFEST,
  manifestSha256=MANIFEST_SHA256,controlFile=CONTROL_FILE,expectedCount=37,uuid=randomUUID}) {
  let queue=Promise.resolve();
  const signal=()=>new AbortController().signal;
  async function inside(path) {
    const base=await realpath(project),target=await realpath(resolve(project,path));
    const rel=relative(base,target);
    if(isAbsolute(rel)||rel==='..'||rel.startsWith(`..${sep}`))throw Error('Path leaves project');
    return target;
  }
  async function json(path) {return JSON.parse(await readFile(await inside(path),'utf8'));}
  async function optional(path) {try{return await json(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
  async function save(path,value) {
    const target=resolve(path);let ancestor=resolve(target,'..');
    for(;;){try{const actual=await inside(ancestor);if(resolve(actual).toLowerCase()!==ancestor.toLowerCase())throw Error('Control output path link/alias forbidden');break;}catch(error){if(error.code!=='ENOENT')throw error;const parent=resolve(ancestor,'..');if(parent===ancestor)throw Error('Control output ancestor unavailable');ancestor=parent;}}
    await mkdir(resolve(target,'..'),{recursive:true});
    const parent=await inside(resolve(target,'..'));if(resolve(parent).toLowerCase()!==resolve(target,'..').toLowerCase())throw Error('Control output path link/alias forbidden');
    const temporary=`${target}.${uuid()}.tmp`,file=await open(temporary,'wx');
    try{await file.writeFile(JSON.stringify(value,null,2)+'\n');await file.sync();}finally{await file.close();}
    await rename(temporary,path);
  }
  function seen(events,projections,id) {
    if(!id)return false;
    const match=item=>item?.source?.rpcId===id||item?.message?.source?.rpcId===id;
    return events.some(e=>e.type==='user/message'&&match(e.data))||Object.values(projections?.values?.inbox??{}).some(items=>Array.isArray(items)&&items.some(match));
  }
  function executionEvidence(events,projections,id) {
    const admitted=events.find(e=>e.type==='user/message'&&e.data?.source?.rpcId===id);
    const seq=admitted?.seq;
    const after=typeof seq==='number'?events.filter(e=>e.seq>seq):[];
    return {accepted_in_durable_state:seen(events,projections,id),admitted_message_seq:seq??null,
      assistant_after_admission_seq:after.filter(e=>e.type==='assistant/message').at(-1)?.seq??null,
      turn_end_after_admission_seq:after.filter(e=>e.type==='turn/end').at(-1)?.seq??null};
  }
  function assertCurrent(row,state) {
    if(state.snapshot?.requirement_id!==row.id||state.snapshot.code!==row.code||state.snapshot.title!==row.code||state.snapshot.manifest_sha256!==manifestSha256||hash(JSON.stringify(state.snapshot))!==state.snapshot_sha256||!/^session-[0-9a-f-]{36}$/i.test(state.session_id))throw Error('Current dispatch journal identity mismatch');
  }
  function legacyTitleOnly(row,state) {
    return state.stage==='accepted'&&typeof row.title==='string'&&row.title.trim()&&row.title!==row.code&&state.snapshot?.title===row.title&&state.snapshot.requirement_id===row.id&&state.snapshot.code===row.code&&state.snapshot.manifest_sha256===manifestSha256&&hash(JSON.stringify(state.snapshot))===state.snapshot_sha256&&/^session-[0-9a-f-]{36}$/i.test(state.session_id)&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(state.request_id);
  }
  async function migrationProof(row,state) {
    if(!legacyTitleOnly(row,state))throw Error('Current dispatch journal identity mismatch');
    async function bytes(path) {const full=await inside(path);if(resolve(full).toLowerCase()!==resolve(project,path).toLowerCase())throw Error('Identity path link/alias forbidden');return readFile(full);}
    try {
      const journalBytes=await bytes(`work/fireplay-production/session-dispatch/${row.id}.json`);
      if(JSON.stringify(JSON.parse(journalBytes))!==JSON.stringify(state))throw Error('Original journal changed before identity resolution');
      const sidecarBytes=await bytes(`work/fireplay-production/session-identity-migration/verified/${row.id}.json`);
      const operationBytes=await bytes(`work/fireplay-production/session-identity-migration/${row.id}.json`);
      const reviewBytes=await bytes(`work/fireplay-production/session-identity-review/${row.id}.json`);
      return {journalBytes,sidecarBytes,sidecar:JSON.parse(sidecarBytes),operation:JSON.parse(operationBytes),reviewBytes};
    }catch(error){if(error.code==='ENOENT')throw Error('Current dispatch journal identity mismatch');throw error;}
  }
  async function readReview(records) {
    const path='work/fireplay-production/session-control-review.json';
    let bytes;
    try{bytes=await readFile(await inside(path));}catch(error){if(error.code==='ENOENT')return null;throw error;}
    const value=JSON.parse(bytes);
    if(Object.keys(value).some(k=>!['control_sha256','requirement_ids','wake_approved_ids','reserved_active_requirement_ids','original_idle_resume_ids','identity_migrations','cold_dispatch_ids','cold_snapshot_sha256'].includes(k))||!/^[0-9a-f]{64}$/.test(value.control_sha256))throw Error('Invalid control review');
    const valid=new Set(records.map(r=>r.id));
    for(const key of ['requirement_ids','wake_approved_ids','reserved_active_requirement_ids','original_idle_resume_ids','cold_dispatch_ids']) {
      const ids=value[key]??[];
      if(!Array.isArray(ids)||ids.length>4||new Set(ids).size!==ids.length||ids.some(id=>!valid.has(id)))throw Error('Invalid reviewed control IDs');
      value[key]=ids;
    }
    if(!value.requirement_ids.length||[...value.wake_approved_ids,...value.reserved_active_requirement_ids].some(id=>!value.requirement_ids.includes(id)))throw Error('Wake/reserved IDs must belong to reviewed batch');
    if(value.original_idle_resume_ids.some(id=>!value.wake_approved_ids.includes(id)))throw Error('Original idle resume IDs must be a subset of wake approvals');
    if(value.identity_migrations!==undefined){const map=value.identity_migrations;if(!map||Array.isArray(map)||typeof map!=='object'||Object.keys(map).length>4||Object.entries(map).some(([id,sha])=>!valid.has(id)||!value.requirement_ids.includes(id)||typeof sha!=='string'||!/^[0-9a-f]{64}$/.test(sha)))throw Error('Invalid reviewed identity migrations');}
    if(value.cold_dispatch_ids.some(id=>!value.requirement_ids.includes(id)))throw Error('Cold dispatch IDs must belong to reviewed batch');
    if(value.cold_dispatch_ids.length||value.cold_snapshot_sha256!==undefined){const map=value.cold_snapshot_sha256;if(!map||Array.isArray(map)||typeof map!=='object'||JSON.stringify(Object.keys(map).sort())!==JSON.stringify([...value.cold_dispatch_ids].sort())||Object.values(map).some(sha=>typeof sha!=='string'||!/^[0-9a-f]{64}$/.test(sha)))throw Error('Invalid reviewed cold snapshot hashes');}
    return {...value,review_sha256:hash(bytes)};
  }
  async function capacity(records,review,{coldAdmission=false}={}) {
    const active=[];
    if(!Array.isArray(registry.archivedSessionIds))throw Error('Archive observer unavailable');
    for(const row of records) {
      const current=await optional(`work/fireplay-production/session-dispatch/${row.id}.json`);
      if(!current||registry.archivedSessionIds.includes(current.session_id)||getStatus(current.session_id)!=='running')continue;
      if(current.stage!=='accepted'){if(coldAdmission)throw Error(`${row.code}: Running current dispatch outcome is not accepted; inspect before new admission`);continue;}
      try{assertCurrent(row,current);}catch(error){try{if(!legacyTitleOnly(row,current))throw error;const fresh=await observe(row);if(!fresh.eligible||fresh.agent_status!=='running')throw Error('Migrated running session changed during capacity scan');}catch(failure){throw Error(`${row.code}: ${failure.message}`);}}
      active.push(row.id);
    }
    const external=await ordinary043Slot({project,controller,registry,getStatus});
    const slots=[...new Set([...active,...external,...(review?.reserved_active_requirement_ids??[]),...(review?.wake_approved_ids??[]),...(review?.cold_dispatch_ids??[])])];
    return {active_requirement_ids:active,reserved_slot_requirement_ids:slots,reserved_slot_count:slots.length,capacity_available:slots.length<=4};
  }
  async function handoff(row) {
    const path=`work/fireplay-production/${row.code}/handoff-to-original-session.json`;
    const bytes=await readFile(await inside(path)),value=JSON.parse(bytes);
    if(value.worker_stopped!==true||value.inflight_unknown!==false)throw Error('Original-session handoff not ready: worker_stopped=true and inflight_unknown=false required');
    return {path,sha256:hash(bytes),worker_stopped:true,inflight_unknown:false};
  }
  async function originalIdleResume(row,observation) {
    if(!observation.eligible||!['idle','inactive'].includes(observation.agent_status)||observation.inbox_next_turn!==0||observation.inbox_next_step!==0)throw Error('Original idle resume requires idle ordinary session and empty observed inbox');
    const path=`work/fireplay-production/${row.code}/original-idle-resume-review.json`;
    const bytes=await readFile(await inside(path)),value=JSON.parse(bytes);
    const current=await json(`work/fireplay-production/session-dispatch/${row.id}.json`);
    let migrated=null;
    try{assertCurrent(row,current);}catch(error){if(!legacyTitleOnly(row,current))throw error;migrated=await observe(row);if(!migrated.eligible||migrated.session_id!==observation.session_id||migrated.identity_migration_sha256!==observation.identity_migration_sha256||!['idle','inactive'].includes(migrated.agent_status)||migrated.inbox_next_turn!==0||migrated.inbox_next_step!==0)throw Error('Migrated original changed before idle resume');}
    if(migrated&&(value.identity_migration_sha256!==migrated.identity_migration_sha256||value.effective_snapshot_sha256!==migrated.effective_snapshot_sha256||value.control_sha256!==hash(await readFile(await inside(controlFile)))))throw Error('Migrated original requires new parent identity/control review');
    if(current.stage!=='accepted'||current.session_id!==observation.session_id||value.requirement_id!==row.id||value.code!==row.code||value.session_id!==observation.session_id||value.snapshot_sha256!==current.snapshot_sha256||value.temporary_worker_occupies_slot!==false||value.inflight_unknown!==false||value.reviewed_by!=='parent'||!value.reviewed_at||!Number.isFinite(Date.parse(value.reviewed_at))||typeof value.evidence_note!=='string'||!value.evidence_note.trim())throw Error('Original idle resume parent evidence identity/stopped/inflight mismatch');
    const folder=`work/fireplay-production/${row.code}`;
    const names=(await readdir(await inside(folder))).filter(name=>/^publication.*\.(json|jsonl)$/.test(name)).sort();
    const inventory=value.publication_journals;
    if(!inventory||Array.isArray(inventory)||typeof inventory!=='object'||JSON.stringify(Object.keys(inventory).sort())!==JSON.stringify(names))throw Error('Original idle resume publication inventory incomplete');
    validateReadOnlyDeclarations(value.read_only_artifacts,inventory);
    const historical=value.historical_artifacts??{};
    if(!historical||Array.isArray(historical)||typeof historical!=='object'||Object.keys(historical).some(name=>!names.includes(name)||value.read_only_artifacts?.[name]))throw Error('Invalid/overlapping historical artifact declaration');
    const historicalRow={...row,session_id:current.session_id,snapshot_sha256:current.snapshot_sha256};
    const historicalContractBytes=Object.keys(historical).length?await readFile(await inside('plugins/fireplay-session-dispatch/reviewed-historical-artifact-contract-v1.json')):null;
    const historicalEvidence={};
    if(historicalContractBytes)for(const name of historicalEvidenceNames({contractBytes:historicalContractBytes,row:historicalRow})) {
      const full=await inside(`${folder}/${name}`);
      if(resolve(full).toLowerCase()!==resolve(project,folder,name).toLowerCase())throw Error('Historical evidence path alias forbidden');
      historicalEvidence[name]=await readFile(full);
    }

    for(const name of names) {
      const data=await readFile(await inside(`${folder}/${name}`));
      if(inventory[name]!==hash(data))throw Error('Original idle resume publication journal changed');
      const catalogBytes=value.read_only_artifacts?.[name]?.kind==='readonly_tool_requests'?await readFile(await inside(`${folder}/publication-catalog-normalized-1845.json`)):undefined;
      if(verifyReadOnlyArtifact({name,bytes:data,declaration:value.read_only_artifacts?.[name],inventory,row,catalogBytes}))continue;
      if(verifyHistoricalArtifact({name,bytes:data,declaration:historical[name],inventory,row:historicalRow,contractBytes:historicalContractBytes,evidence:historicalEvidence}))continue;
      const rows=name.endsWith('.jsonl')?data.toString('utf8').split(/\r?\n/).filter(line=>line.trim()).map(line=>JSON.parse(line)):[JSON.parse(data)];
      const latest=new Map();
      for(const entry of rows) {
        if(entry.requirement_id&&entry.requirement_id!==row.id||entry.code&&entry.code!==row.code)throw Error('Publication journal identity mismatch');
        if(name.endsWith('.jsonl')&&!entry.operation_id)throw Error('Publication journal operation identity missing');
        latest.set(entry.operation_id??name,entry);
      }
      for(const entry of latest.values()) {
        const state=entry.state??entry.stage;
        if(entry.inflight_unknown===true||entry.unknown_inflight===true||!state&&(name.endsWith('.jsonl')||entry.operation_id||entry.remote_write_attempted!==false)||state&&!['received','completed','verified','failed','reconciled','not_started'].includes(state))throw Error('Original idle resume publication outcome unknown');
      }
    }
    return {path,sha256:hash(bytes),mode:'original_idle_resume',temporary_worker_occupies_slot:false,inflight_unknown:false,publication_journals:inventory};
  }
  async function wakeReadiness(row,review,observation) {
    const current=await optional(`work/fireplay-production/session-dispatch/${row.id}.json`);
    if(legacyTitleOnly(row,current??{})) {
      if(!review.original_idle_resume_ids.includes(row.id))throw Error('Migrated original requires new original-idle-resume parent review');
      return originalIdleResume(row,observation??await observe(row));
    }
    if(review.original_idle_resume_ids.includes(row.id))return originalIdleResume(row,observation??await observe(row));
    return handoff(row);
  }
  async function observe(row) {
    const state=await optional(`work/fireplay-production/session-dispatch/${row.id}.json`);
    const base={requirement_id:row.id,code:row.code};
    if(!state)return {...base,eligible:false,reason:'no_current_session'};
    if(state.stage!=='accepted')return {...base,eligible:false,reason:'dispatch_not_accepted'};
    let migration=null;
    try{assertCurrent(row,state);}catch(error){if(!legacyTitleOnly(row,state))throw error;migration=await migrationProof(row,state);}
    const sid=state.session_id;
    if(!Array.isArray(registry.archivedSessionIds))throw Error('Archive observer unavailable');
    const archived=registry.archivedSessionIds.includes(sid);
    if(archived&&!migration)return {...base,session_id:sid,eligible:false,reason:'archived'};
    const workspaces=registry.list().filter(w=>samePath(w.path,project));
    if(workspaces.length!==1||!workspaces[0].sessionIds?.includes(sid))throw Error('Session not member of exact project');
    const observation=await controller.inspect(sid),projections=await controller.projections({sessionId:sid},signal());
    if(!samePath(observation?.meta?.cwd,project)||projections?.kind!=='sequenced'||projections.values?.title!==row.code||!Object.hasOwn(projections.values,'subagent')||projections.values.subagent!==null||observation.meta.parentSessionId)throw Error('Session project/title/ordinary identity mismatch');
    const events=observation.events??[],last=events.at(-1);
    let identity={};
    if(migration) {
      const title=events.filter(e=>e.type==='session/title').at(-1);
      if(!title||title.data?.title!==row.code||!Number.isFinite(title.seq)||!events.some(e=>e.type==='user/message'&&e.data?.source?.rpcId===state.request_id))throw Error('Migrated original title/rpcId evidence unavailable');
      const snapshot=verifyEffectiveIdentity({row,journal:state,journalBytes:migration.journalBytes,sidecar:migration.sidecar,operation:migration.operation,reviewBytes:migration.reviewBytes,manifestSha256,fresh:{ordinary:true,project_verified:true,session_id:sid,title:projections.values.title,title_seq:title.seq,archived}});
      assertCurrent(row,{...state,snapshot,snapshot_sha256:migration.sidecar.effective_snapshot_sha256});
      identity={identity_migration_sha256:hash(migration.sidecarBytes),effective_snapshot_sha256:migration.sidecar.effective_snapshot_sha256,original_snapshot_sha256:state.snapshot_sha256};
      if(archived)return {...base,session_id:sid,eligible:false,reason:'archived',...identity};
    }
    return {...base,...identity,session_id:sid,eligible:true,agent_status:getStatus(sid),event_count:events.length,
      last_event:last?{type:last.type,seq:last.seq,time:last.time}:null,
      inbox_next_turn:projections.values.inbox?.['next-turn']?.length??null,
      inbox_next_step:projections.values.inbox?.['next-step']?.length??null,
      _events:events,_projections:projections};
  }
  const publicView=({_events,_projections,...item})=>item;
  async function checkColdDispatch(args) {
    if(!args||args.action!=='dispatch'||Object.keys(args).some(k=>!['action','requirement_id','snapshot_sha256'].includes(k))||typeof args.requirement_id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(args.requirement_id)||typeof args.snapshot_sha256!=='string'||!/^[0-9a-f]{64}$/.test(args.snapshot_sha256))throw Error('Invalid cold dispatch arguments');
    async function exactBytes(path){const actual=await inside(path);if(resolve(actual).toLowerCase()!==resolve(project,path).toLowerCase())throw Error('Cold dispatch input path link/alias forbidden');return readFile(actual);}
    const manifestBytes=await exactBytes(manifest);if(hash(manifestBytes)!==manifestSha256)throw Error('Reviewed intake manifest changed');
    const records=JSON.parse(manifestBytes).records;
    if(!Array.isArray(records)||records.length!==expectedCount||new Set(records.map(r=>r.id)).size!==expectedCount)throw Error('Fixed requirement set changed');
    const row=records.find(r=>r.id===args.requirement_id);if(!row)throw Error('Requirement outside fixed intake');
    const journalPath=`work/fireplay-production/session-dispatch/${row.id}.json`,journalBytes=await exactBytes(journalPath),state=JSON.parse(journalBytes);
    if(state.stage!=='titled')throw Error('Cold dispatch requires confirmed titled journal; inspect unknown or previously attempted dispatch without replay');
    assertCurrent(row,state);
    if(state.snapshot_sha256!==args.snapshot_sha256||typeof state.request_id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(state.request_id))throw Error('Cold dispatch snapshot/request identity mismatch');
    const controlBytes=await exactBytes(controlFile),revision=hash(controlBytes),text=controlBytes.toString('utf8');
    if(!text.trim()||state.snapshot.intake_version!=='fixed37-code-v1'||state.snapshot.control_sha256!==revision||typeof state.snapshot.prompt!=='string'||!state.snapshot.prompt.endsWith(text))throw Error('Cold dispatch snapshot must freeze current reviewed control scope');
    const review=await readReview(records);
    if(!review||review.control_sha256!==revision||!review.requirement_ids.includes(row.id)||!review.cold_dispatch_ids.includes(row.id)||review.cold_snapshot_sha256?.[row.id]!==args.snapshot_sha256)throw Error('Cold dispatch requires new parent reviewed membership and exact snapshot/control SHA');
    const sid=state.session_id;
    if(!Array.isArray(registry.archivedSessionIds)||registry.archivedSessionIds.includes(sid))throw Error('Cold dispatch archived/unknown archive identity refused');
    const workspaces=registry.list().filter(w=>samePath(w.path,project));if(workspaces.length!==1||!workspaces[0].sessionIds?.includes(sid))throw Error('Cold dispatch session is not exact project member');
    const observation=await controller.inspect(sid),projections=await controller.projections({sessionId:sid},signal());
    if(!samePath(observation?.meta?.cwd,project)||observation.meta.parentSessionId||projections?.kind!=='sequenced'||!Object.hasOwn(projections.values??{},'subagent')||projections.values.subagent!==null||projections.values.title!==row.code)throw Error('Cold dispatch title/ordinary/project identity mismatch');
    if(projections.values.inbox?.['next-turn']?.length!==0||projections.values.inbox?.['next-step']?.length!==0||![null,'idle','inactive'].includes(getStatus(sid)))throw Error('Cold dispatch requires nonrunning fresh session and empty inbox');
    const events=observation.events??[],title=events.filter(e=>e.type==='session/title').at(-1);
    if(!title||title.data?.title!==row.code||!Number.isFinite(title.seq)||events.some(e=>e.type==='user/message'))throw Error('Cold dispatch fresh title/no-previous-prompt evidence unavailable');
    const slots=await capacity(records,review,{coldAdmission:true});if(!slots.capacity_available)throw Error('Four active/reserved/wake/cold requirement slots exceeded');
    const freshReview=await readReview(records);
    if(freshReview?.review_sha256!==review.review_sha256||hash(await exactBytes(manifest))!==manifestSha256||hash(await exactBytes(controlFile))!==revision||!journalBytes.equals(await exactBytes(journalPath)))throw Error('Cold dispatch reviewed input changed before admission');
    return {requirement_id:row.id,session_id:sid,snapshot_sha256:state.snapshot_sha256,control_sha256:revision,review_sha256:review.review_sha256,title_seq:title.seq,...slots};
  }
  async function dispatchCold(args,dispatch) {
    if(typeof dispatch!=='function')throw Error('Cold dispatcher unavailable');
    const result=queue.then(async()=>{await checkColdDispatch(args);return dispatch();});queue=result.catch(()=>{});return result;
  }
  async function execute(args) {
    if(!['preview','steer','inspect'].includes(args.action)||Object.keys(args).some(k=>!['action','requirement_ids','control_sha256'].includes(k)))throw Error('Unsupported control arguments');
    const bytes=await readFile(await inside(manifest));
    if(hash(bytes)!==manifestSha256)throw Error('Reviewed intake manifest changed');
    const records=JSON.parse(bytes).records;
    if(!Array.isArray(records)||records.length!==expectedCount||new Set(records.map(r=>r.id)).size!==expectedCount)throw Error('Invalid fixed manifest');
    const ids=args.requirement_ids??records.map(r=>r.id);
    if(!Array.isArray(ids)||!ids.length||ids.length>expectedCount||new Set(ids).size!==ids.length||ids.some(id=>typeof id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)))throw Error('Invalid requirement IDs');
    const rows=ids.map(id=>{const row=records.find(r=>r.id===id);if(!row)throw Error('Requirement outside reviewed intake');return row;});
    let text,revision=args.control_sha256,review=null;
    if(args.action!=='inspect') {
      const control=await readFile(await inside(controlFile));text=control.toString('utf8');
      if(!text.trim())throw Error('Empty control document');
      const actual=hash(control);
      review=await readReview(records);
      if(args.action==='preview') {
        const readiness=[];
        for(const id of review?.wake_approved_ids??[]) {
          const row=records.find(r=>r.id===id);
          try{readiness.push({requirement_id:id,ready:true,...await wakeReadiness(row,review)});}
          catch(error){readiness.push({requirement_id:id,ready:false,reason:error.code==='ENOENT'?'handoff_missing':error.message});}
        }
        return {control_file:controlFile,control_sha256:actual,prompt:text,requirement_ids:ids,delivery_mode:'queue',active_only:!review?.wake_approved_ids.length,max_active:4,
          reviewed_requirement_ids:review?.requirement_ids??[],wake_approved_ids:review?.wake_approved_ids??[],original_idle_resume_ids:review?.original_idle_resume_ids??[],reserved_active_requirement_ids:review?.reserved_active_requirement_ids??[],review_sha256:review?.review_sha256??null,review_control_hash_matches:review?review.control_sha256===actual:null,handoff_readiness:readiness,...await capacity(records,review)};
      }
      if(revision!==actual||review&&review.control_sha256!==actual)throw Error('Reviewed control hash mismatch');
    }
    if(revision!==undefined&&!/^[0-9a-f]{64}$/.test(revision))throw Error('Invalid control hash');
    const items=[];let sent=0;
    for(const row of rows) {
      try {
        const observation=await observe(row),item=publicView(observation);
        const path=revision?resolve(project,'work/fireplay-production/session-control',revision,`${row.id}.json`):null;
        let receipt=null;
        if(path){try{const actual=await inside(path);if(resolve(actual).toLowerCase()!==resolve(path).toLowerCase())throw Error('Control receipt path link/alias forbidden');receipt=JSON.parse(await readFile(actual,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}}
        if(receipt&&receipt.session_id!==observation.session_id)throw Error('Control receipt belongs to previous session; rollover required');
        if(receipt&&(receipt.requirement_id!==row.id||receipt.code!==row.code||receipt.control_sha256!==revision||typeof receipt.request_id!=='string'||!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(receipt.request_id)||!['pending','sending','accepted','unknown'].includes(receipt.stage)))throw Error('Control receipt identity invalid');
        if(receipt){item.control={...receipt};item.durable_control_observed=seen(observation._events??[],observation._projections,receipt.request_id);item.execution_evidence=executionEvidence(observation._events??[],observation._projections,receipt.request_id);}
        if(args.action==='steer'&&observation.eligible) {
          if(receipt&&['sending','accepted','unknown'].includes(receipt.stage)){item.skipped=true;item.reason='control_already_attempted';}
          else {
            receipt??={requirement_id:row.id,code:row.code,session_id:observation.session_id,control_sha256:revision,request_id:uuid(),stage:'pending'};
            const approvedWake=review?.wake_approved_ids.includes(row.id)&&['idle','inactive'].includes(observation.agent_status);
            if(observation.agent_status!=='running'&&!approvedWake||sent>=4){receipt.stage='pending';receipt.reason=sent>=4?'batch_limit':'inactive_not_woken';await save(path,receipt);item.control={...receipt};}
            else {
              const fresh=await observe(row),freshReview=await readReview(records);
              if((freshReview?.review_sha256??null)!==(review?.review_sha256??null))throw Error('Control wake review changed before queue admission');
              if(fresh.identity_migration_sha256&&(!freshReview?.requirement_ids.includes(row.id)||freshReview.identity_migrations?.[row.id]!==fresh.identity_migration_sha256))throw Error('Migrated original requires new parent control review membership and migration SHA');
              if(hash(await readFile(await inside(controlFile)))!==revision)throw Error('Control document changed before queue admission');
              const waking=fresh.agent_status!=='running';
              const wakeApproved=freshReview?.wake_approved_ids.includes(row.id);
              if(!fresh.eligible||fresh.session_id!==observation.session_id||fresh.identity_migration_sha256!==observation.identity_migration_sha256||waking&&(!wakeApproved||!['idle','inactive'].includes(fresh.agent_status)))throw Error('Session changed before queue admission');
              let handoffReceipt=null,slots=null;
              if(freshReview?.wake_approved_ids.length) {
                slots=await capacity(records,freshReview);
                if(!slots.capacity_available)throw Error('Four active/reserved requirement slots exceeded');
                if(wakeApproved&&waking)handoffReceipt=await wakeReadiness(row,freshReview,fresh);
              }
              receipt.stage='sending';delete receipt.reason;receipt.updated_at=new Date().toISOString();
              receipt.wake_authorized=Boolean(waking&&wakeApproved);receipt.before_agent_status=fresh.agent_status;
              if(freshReview)receipt.review_sha256=freshReview.review_sha256;
              if(handoffReceipt)receipt.handoff=handoffReceipt;
              if(slots)receipt.reserved_slot_requirement_ids=slots.reserved_slot_requirement_ids;
              await save(path,receipt);sent++;
              try {
                const answer=await controller.prompt({requestId:receipt.request_id,sessionId:receipt.session_id,mode:'queue',content:[{type:'text',text}],clientTimeZone:'Asia/Shanghai'},signal());
                if(answer?.accepted!==true)throw Error('Host did not acknowledge control');
                receipt.stage='accepted';
              }catch(error){receipt.stage='unknown';receipt.error=error.message;}
              receipt.updated_at=new Date().toISOString();await save(path,receipt);item.control={...receipt};
              const after=await observe(row);item.durable_control_observed=seen(after._events??[],after._projections,receipt.request_id);item.execution_evidence=executionEvidence(after._events??[],after._projections,receipt.request_id);item.post_queue=publicView(after);
            }
          }
        }
        items.push(item);
      }catch(error){items.push({requirement_id:row.id,code:row.code,error:error.message});}
    }
    return {action:args.action,count:items.length,sent,items};
  }
  return {checkColdDispatch,dispatchCold,execute(args){const task=queue.then(()=>execute(args));queue=task.catch(()=>{});return task;}};
}
