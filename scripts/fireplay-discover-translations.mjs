#!/usr/bin/env node
/** Read-only discovery. Current connector contracts/options define the filter. */
import {connectProxy,decodeResult,validateSchema} from './fireplay-mcp-publication.mjs';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';

const fail=message=>{throw new Error(message);};
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH=/^[0-9a-f]{64}$/;
async function definition(transport,name){
 const raw=await transport.call('tool_catalog',{tool:name});
 const result=decodeResult(raw);
 if(raw?.isError||result?.name!==name||result.operation_class!=='read'||!HASH.test(result.schema_version||'')
    ||!['tool_read',name].includes(result.invoke_via)||result.inputSchema?.type!=='object'
    ||!result.inputSchema.properties||!Object.keys(result.inputSchema.properties).length)fail(`Read contract unavailable: ${name}`);
 return result;
}
async function read(transport,contract,args){
 validateSchema(args,contract.inputSchema);
 const parameters=contract.invoke_via==='tool_read'?{tool:contract.name,schema_version:contract.schema_version,arguments:args}:args;
 const raw=await transport.call(contract.invoke_via,parameters);
 if(raw?.isError)fail(`Read rejected: ${contract.name}`);
 const value=decodeResult(raw);
 if(value?.error)fail(`Read failed: ${contract.name}`);
 return value;
}
function nextCursor(response){
 const own=Object.hasOwn(response,'next_cursor');
 const cursor=response.next_cursor;
 const validCursor=typeof cursor==='string'&&cursor.trim().length>0;
 if(Object.hasOwn(response,'has_more')){
  if(typeof response.has_more!=='boolean')fail('Missing explicit pagination evidence');
  if(response.has_more){if(!own||!validCursor)fail('Incomplete pagination needs next_cursor');return cursor;}
  if(own&&cursor!==null)fail('Conflicting terminal pagination cursor');
  return null;
 }
 if(!own||response.query_meta?.pagination!=='live_keyset'||(cursor!==null&&!validCursor))fail('Missing explicit live_keyset pagination cursor');
 return cursor;
}
function verifyPage(response,query){
 const meta=response?.query_meta, filters=meta?.filters_applied;
 const list=value=>value===null||value===undefined?[]:Array.isArray(value)?value:[value];
 const same=(left,right)=>JSON.stringify([...list(left)].sort())===JSON.stringify([...list(right)].sort());
 if(!Array.isArray(response?.items))fail('Missing summary items');
 nextCursor(response);
 if(response.view!=='summary'||response.scope!==query.scope||response.deptId!==query.dept_id
    ||!filters||filters.scope!==query.scope||filters.dept_id!==query.dept_id
    ||!same(filters.asset_type,query.asset_type)||!same(filters.ai_mode,query.ai_mode))fail('Response filters do not match discovery query');
 for(const field of ['requester_ids','assignee_ids','requirement_ids','keyword','status','date_from','date_to']){
  if(!same(filters[field],query[field]))fail(`Response filter mismatch: ${field}`);
 }
 if(filters.only_with_materials===true||meta.snapshot===true||meta.source_limit_reached===true)fail('Incomplete or unsupported discovery coverage evidence');
 if(typeof response.serverTime!=='string'||!Number.isFinite(Date.parse(response.serverTime)))fail('Missing server time evidence');
}

export async function discoverTranslations({transport,deptId,maxPages=100}){
 if(typeof deptId!=='string'||!deptId.trim())fail('A visible department ID is required to resolve current AI mode options');
 if(!Number.isInteger(maxPages)||maxPages<1||maxPages>1000)fail('Invalid page limit');
 const optionsContract=await definition(transport,'requirement_create_options');
 const listContract=await definition(transport,'requirement_list');
 const options=await read(transport,optionsContract,{dept_id:deptId});
 if(options.deptId!==deptId)fail('Department option identity mismatch');
 const modes=Array.isArray(options.fixed?.aiModes)?options.fixed.aiModes.filter(item=>item.label==='图片翻译'&&item.enabled===true):[];
 if(modes.length!==1||typeof modes[0].value!=='string'||!/^[a-z][a-z0-9_]{0,47}$/.test(modes[0].value))fail('Exactly one enabled 图片翻译 option is required; no guessed slug');
 const assetTypes=options.fixed?.assetTypes;
 for(const [value,label] of [['image','长方竖图'],['button','大按钮']]){
  const matches=Array.isArray(assetTypes)?assetTypes.filter(item=>item.value===value&&item.label===label):[];
  if(matches.length!==1||matches[0].enabled===false)fail(`Current type option missing or ambiguous: ${label}`);
 }
 const query={scope:'all',dept_id:deptId,view:'summary',asset_type:['image','button'],ai_mode:modes[0].value,pagination:'cursor',limit:200,offset:0};
 const summaryPages=[],seenCursors=new Set(),seenIds=new Set(),seenCodes=new Set(),rows=[];
 let cursor;
 for(let page=0;page<maxPages;page++){
  const args={...query,...(cursor?{cursor}:{})};
  const response=await read(transport,listContract,args);
  verifyPage(response,query);
  summaryPages.push({arguments:args,response});
  for(const row of response.items){
   if(typeof row.id!=='string'||!UUID.test(row.id)||typeof row.code!=='string'||!row.code.trim())fail('Missing or invalid requirement identity');
   const identity=row.id.toLowerCase();
   if(seenIds.has(identity)||seenCodes.has(row.code))fail('Live pagination moved or duplicated rows; refresh before production');
   seenIds.add(identity);seenCodes.add(row.code);
   if(row.deptId!==deptId||row.aiMode!==modes[0].value||!['image','button'].includes(row.assetType))fail('Response does not match exact AI/type/department filters');
   rows.push(row);
  }
  cursor=nextCursor(response);
  if(cursor===null)break;
  if(typeof cursor!=='string'||!cursor||seenCursors.has(cursor))fail('Invalid or repeated pagination cursor');
  seenCursors.add(cursor);
 }
 const pending=rows.filter(row=>!['done','cancelled','canceled'].includes(row.status));
 return {schema_status:'live_contract_verified',external_write:false,complete:cursor===null,
  state:cursor?'partial':(pending.length?'found':'empty'),query,summary_pages:summaryPages,next_cursor:cursor,
  option_evidence:{dept_id:deptId,options_revision:options.optionsRevision,ai_mode:modes[0],asset_types:assetTypes,contract_schema:optionsContract.schema_version},
  list_schema_version:listContract.schema_version,required_ai_modes:[modes[0].value],
  candidates:pending.map(row=>({id:row.id,code:row.code,asset_type:row.assetType,status:row.status,can_bind:row.canBindMaterials,updated_at:row.updatedAt,next_action:'read_exact_blind_brief'})),
  note:'Live visible scope only. Read exact brief, resolve source/language/member matrix and recheck permission before production; summary count is not delivered image count.'};
}

async function main(){
 const [flag,deptId]=process.argv.slice(2);
 if(flag!=='--department'||!deptId||process.argv.length!==4)fail('Usage: node scripts/fireplay-discover-translations.mjs --department DEPT_ID');
 const transport=await connectProxy();
 try{console.log(JSON.stringify(await discoverTranslations({transport,deptId}),null,2));}finally{transport.close();}
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)main().catch(error=>{console.error(JSON.stringify({state:'discovery_failed',message:error.message,external_write:false}));process.exitCode=1;});
