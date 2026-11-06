import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {discoverTranslations} from '../scripts/fireplay-discover-translations.mjs';

// Synthetic offline contracts: never use these schemas to authorize live calls.
const deptId='fixture-department';
const mode='fixture_image_translation';
const evidence={schema_version:'b'.repeat(64),response_excerpt:{
 deptId,optionsRevision:'fixture-options-v1',fixed:{
  aiModes:[{value:mode,label:'图片翻译',enabled:true}],
  assetTypes:[{value:'image',label:'长方竖图',enabled:true},{value:'button',label:'大按钮',enabled:true}],
 },
}};
const listSchema={type:'object',additionalProperties:false,
 properties:{scope:{type:'string',enum:['all']},dept_id:{type:'string',minLength:1},view:{type:'string',enum:['summary']},
  asset_type:{type:'array',items:{type:'string',enum:['image','button']}},ai_mode:{type:'string',minLength:1},
  pagination:{type:'string',enum:['cursor']},limit:{type:'integer',minimum:1,maximum:200},offset:{type:'integer',minimum:0},
  cursor:{type:'string',minLength:1}},
 required:['scope','dept_id','view','asset_type','ai_mode','pagination','limit','offset']};
const clone=value=>structuredClone(value);
const contracts={
 requirement_create_options:{name:'requirement_create_options',operation_class:'read',invoke_via:'tool_read',schema_version:evidence.schema_version,
  inputSchema:{type:'object',properties:{dept_id:{type:'string',minLength:1}},required:['dept_id'],additionalProperties:false}},
 requirement_list:{name:'requirement_list',operation_class:'read',invoke_via:'tool_read',schema_version:'a'.repeat(64),inputSchema:listSchema},
};
const row=(changes={})=>({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bb',code:'SHE-20261106-001',deptId,
 assetType:'image',aiMode:mode,status:'not_started',canBindMaterials:true,updatedAt:'2026-11-06T03:00:00Z',...changes});
function page(items=[],changes={}){
 return {scope:'all',deptId,view:'summary',items,has_more:false,next_cursor:null,serverTime:'2026-11-06T03:01:00Z',
  query_meta:{filters_applied:{scope:'all',dept_id:deptId,requester_ids:[],assignee_ids:[],requirement_ids:[],keyword:null,status:null,
   asset_type:['image','button'],ai_mode:[mode],date_from:null,date_to:null,only_with_materials:false},
   source_limit_reached:false,pagination:'live_keyset',snapshot:false,total_scope:'visible_matches'},...changes};
}
function livePage(items=[],cursor=null){
 const value=page(items,{next_cursor:cursor,next_offset:null,total:items.length,returned:items.length});
 delete value.has_more;
 Object.assign(value.query_meta,{total_exact:true,returned:items.length});
 return value;
}
function fixture({pages=[page([row()])],options=clone(evidence.response_excerpt),definitions=clone(contracts),wrapper='structured'}={}){
 const calls=[];let index=0;
 const wrap=value=>wrapper==='text'?{content:[{type:'text',text:JSON.stringify(value)}]}:{structuredContent:value};
 return {calls,transport:{call:async(name,args)=>{
  calls.push({name,args:clone(args)});
  if(name==='tool_catalog')return wrap(definitions[args.tool]);
  assert.equal(name,'tool_read','Discovery must never dispatch a write or guessed endpoint');
  assert.equal(args.schema_version,definitions[args.tool].schema_version);
  if(args.tool==='requirement_create_options')return wrap(options);
  assert.equal(args.tool,'requirement_list');
  assert.ok(index<pages.length,'Unexpected extra page');
  return wrap(pages[index++]);
 }}};
}
const run=(f,extra={})=>discoverTranslations({transport:f.transport,deptId,...extra});

test('official option label resolves exact mode, image/button summary query, read-only receipt shape',async()=>{
 const f=fixture({pages:[page([row(),row({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bc',code:'SHE-20261106-002',assetType:'button'})])]});
 const result=await run(f);
 assert.equal(result.complete,true);assert.equal(result.state,'found');assert.equal(result.external_write,false);
 assert.equal(result.candidates.length,2);assert.deepEqual(result.required_ai_modes,[mode]);
 assert.deepEqual(result.query.asset_type,['image','button']);assert.equal(result.query.ai_mode,mode);assert.equal(result.query.view,'summary');
 assert.equal(result.option_evidence.options_revision,evidence.response_excerpt.optionsRevision);
 assert.deepEqual(result.summary_pages[0].arguments,result.query);
 assert.deepEqual(Object.keys(result.summary_pages[0]).sort(),['arguments','response']);
 assert.ok(f.calls.every(call=>['tool_catalog','tool_read'].includes(call.name)));
 assert.equal(f.calls.filter(call=>call.name==='tool_read').length,2);
 assert.ok(f.calls.every(call=>!JSON.stringify(call.args).includes('confirmed')));
});

test('mode is obtained by current option label rather than hard-coded slug',async()=>{
 const options=clone(evidence.response_excerpt);options.fixed.aiModes[0].value='image_translation_v2';
 const response=page([row({aiMode:'image_translation_v2'})]);response.query_meta.filters_applied.ai_mode='image_translation_v2';
 const result=await run(fixture({options,pages:[response],wrapper:'text'}));
 assert.equal(result.query.ai_mode,'image_translation_v2');
});

test('pagination preserves exact filters and returns terminal cursor evidence',async()=>{
 const first=page([row()],{has_more:true,next_cursor:'cursor-two'});
 const second=page([row({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bc',code:'SHE-20261106-002',assetType:'button'})]);
 const f=fixture({pages:[first,second]});const result=await run(f);
 assert.equal(result.complete,true);assert.equal(result.next_cursor,null);assert.equal(result.summary_pages.length,2);
 assert.deepEqual(result.summary_pages[1].arguments,{...result.query,cursor:'cursor-two'});
});

test('maxPages retains partial proof and resumable next cursor; empty is complete',async()=>{
 const result=await run(fixture({pages:[page([row()],{has_more:true,next_cursor:'remaining'})]}),{maxPages:1});
 assert.equal(result.complete,false);assert.equal(result.state,'partial');assert.equal(result.next_cursor,'remaining');
 assert.equal(result.summary_pages.length,1);
 const empty=await run(fixture({pages:[page()]}));assert.equal(empty.complete,true);assert.equal(empty.state,'empty');
 assert.deepEqual(empty.candidates,[]);
});

test('completed rows remain evidence but are not actionable candidates',async()=>{
 const result=await run(fixture({pages:[page([row({status:'done'})])]}));
 assert.equal(result.state,'empty');assert.equal(result.summary_pages[0].response.items.length,1);
});

test('option ambiguity, disabled/missing mode, invalid slug and wrong department reject',async()=>{
 const variants=[
  value=>{value.fixed.aiModes.push(clone(value.fixed.aiModes[0]));},
  value=>{value.fixed.aiModes[0].enabled=false;},
  value=>{value.fixed.aiModes=[];},
  value=>{value.fixed.aiModes[0].value='图片翻译';},
  value=>{value.deptId='wrong';},
  value=>{value.fixed.assetTypes=value.fixed.assetTypes.filter(item=>item.value!=='button');},
  value=>{value.fixed.assetTypes[1].enabled=false;},
 ];
 for(const mutate of variants){const options=clone(evidence.response_excerpt);mutate(options);await assert.rejects(run(fixture({options})));}
});

test('filter echo is required while optional coverage metadata may be absent',async()=>{
 const response=page([row()]);
 delete response.query_meta.snapshot;delete response.query_meta.pagination;delete response.query_meta.source_limit_reached;
 const result=await run(fixture({pages:[response]}));assert.equal(result.complete,true);
});

test('wrong server filter echo, view, department and truncated coverage reject',async()=>{
 const variants=[
  value=>{value.query_meta.filters_applied.ai_mode='video_translate';},
  value=>{value.query_meta.filters_applied.asset_type=['image'];},
  value=>{value.query_meta.filters_applied.scope='department';},
  value=>{value.query_meta.filters_applied.assignee_ids=['unexpected-person'];},
  value=>{value.query_meta.filters_applied.keyword='hidden-filter';},
  value=>{value.query_meta.source_limit_reached=true;},
  value=>{value.query_meta.snapshot=true;},
  value=>{value.view='delivery';},
  value=>{value.deptId='wrong';},
  value=>{delete value.query_meta;},
 ];
 for(const mutate of variants){const response=page([row()]);mutate(response);await assert.rejects(run(fixture({pages:[response]})));}
});

test('duplicate cursor, missing cursor, missing has_more and UUID case duplicates reject',async()=>{
 const secondRow=row({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bc',code:'SHE-20261106-002'});
 await assert.rejects(run(fixture({pages:[page([row()],{has_more:true,next_cursor:'same'}),page([secondRow],{has_more:true,next_cursor:'same'})]})),/cursor/);
 await assert.rejects(run(fixture({pages:[page([row()],{has_more:true,next_cursor:null})]})),/cursor/);
 const missing=page();delete missing.has_more;delete missing.next_cursor;await assert.rejects(run(fixture({pages:[missing]})),/pagination/);
 await assert.rejects(run(fixture({pages:[page([row()],{has_more:true,next_cursor:'second'}),page([row({id:row().id.toUpperCase(),code:'SHE-20261106-002'})])]})),/duplicated/);
 await assert.rejects(run(fixture({pages:[page([row({id:'not-uuid'})])]})),/identity/);
 await assert.rejects(run(fixture({pages:[page([row({assetType:'video'})])]})),/filters/);
 await assert.rejects(run(fixture({pages:[page([row({aiMode:null})])]})),/filters/);
});

test('live_keyset without has_more supports terminal, continuous and partial raw receipts',async()=>{
 const terminal=livePage([row()]);
 const result=await run(fixture({pages:[terminal]}));
 assert.equal(result.complete,true);assert.equal(result.next_cursor,null);
 assert.deepEqual(result.summary_pages[0].response,terminal);
 assert.equal(Object.hasOwn(result.summary_pages[0].response,'has_more'),false);
 const second=livePage([row({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bc',code:'SHE-20261106-002'})]);
 const full=await run(fixture({pages:[livePage([row()],'next'),second]}));
 assert.equal(full.complete,true);assert.equal(full.candidates.length,2);
 const partial=await run(fixture({pages:[livePage([row()],'next')]}),{maxPages:1});
 assert.equal(partial.complete,false);assert.equal(partial.next_cursor,'next');
 for(const bad of [page([],{has_more:false,next_cursor:'conflict'}),livePage([],''),livePage([],42),
                  {...livePage([]),next_cursor:undefined}, {...livePage([]),has_more:null}]){
  await assert.rejects(run(fixture({pages:[bad]})),/pagination|cursor/);
 }
 const noEvidence=livePage([]);delete noEvidence.query_meta.pagination;
 await assert.rejects(run(fixture({pages:[noEvidence]})),/pagination/);
});

test('write-class contracts, write invocation and malformed schemas never dispatch',async()=>{
 for(const mutate of [value=>{value.operation_class='write';},value=>{value.invoke_via='tool_write';},value=>{value.inputSchema={};}]){
  const definitions=clone(contracts);mutate(definitions.requirement_list);const f=fixture({definitions});
  await assert.rejects(run(f),/contract unavailable/);assert.ok(f.calls.every(call=>call.name==='tool_catalog'));
 }
 const f=fixture();await assert.rejects(run(f,{maxPages:0}),/page limit/);assert.equal(f.calls.length,0);
});

test('generated complete and partial receipts are consumable by Python discover without reshaping',async t=>{
 const python=process.env.TEST_PYTHON;
 if(!python){t.skip('Set TEST_PYTHON to exercise cross-language handoff');return;}
 const cases=[
  {pages:[page([row()])],complete:true,count:1},
  {pages:[page([row()],{has_more:true,next_cursor:'next'})],complete:false,count:1},
  {pages:[livePage([row()])],complete:true,count:1},
  {pages:[livePage([row()],'next')],complete:false,count:1},
  {pages:[livePage([row()],'next'),livePage([row({id:'f3e17b15-2e91-4a5c-b5a8-a04b457d02bc',code:'SHE-20261106-002'})])],complete:true,count:2},
 ];
 for(const item of cases){
  const result=await run(fixture({pages:item.pages}),{maxPages:item.pages.length});
  const child=spawnSync(python,['-c',"import json,sys;sys.path.insert(0,'src');from image_localizer.fireplay import discover;v=discover(json.load(sys.stdin));print(json.dumps({'complete':v['complete'],'selected':len(v['selected'])}))"],
   {input:JSON.stringify(result),encoding:'utf8',cwd:new URL('..',import.meta.url)});
  assert.equal(child.status,0,child.stderr);assert.deepEqual(JSON.parse(child.stdout),{complete:item.complete,selected:item.count});
 }
});
