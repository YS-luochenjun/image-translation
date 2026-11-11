import {createHash} from 'node:crypto';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const object=v=>!!v&&typeof v==='object'&&!Array.isArray(v);
const text=v=>typeof v==='string'&&v.trim().length>0;
const hex=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const strings=v=>Array.isArray(v)&&v.every(x=>typeof x==='string');
const keys=(v,required,optional=[])=>object(v)&&required.every(k=>Object.hasOwn(v,k))&&Object.keys(v).every(k=>required.includes(k)||optional.includes(k));
const fail=(ok,message)=>{if(!ok)throw Error(message);};
const catalogTools=['material_r2_upload_from_bytes','material_upload_from_url','material_attribution_update','material_sharing_departments','requirement_field_options_query','material_rules_list','requirement_bind_materials'];
const readTools=['material_sharing_departments','requirement_field_options_query','material_rules_list'];
export const readOnlyArtifactKinds=Object.freeze({
 'publication-catalog-requests-1845.json':'tool_catalog_requests',
 'publication-catalog-normalized-1845.json':'tool_catalog_definitions',
 'publication-context-requests.json':'readonly_tool_requests',
 'publication-context-normalized.json':'readonly_context_results',
 'publication-contract-diff.json':'contract_diff',
 'publication-contract-review.json':'contract_review',
 'publication-gate.json':'review_gate',
});
// These are declarations of observed data, never a substitute for operation outcomes.
// Schema documents can describe write tools: only actual top-level record fields
// represent an operation here, not strings or schema property names nested inside.
export function validateReadOnlyDeclarations(declarations,inventory){
 if(declarations===undefined)return;
 fail(object(declarations),'Read-only artifact declarations must be a map');
 for(const [name,value]of Object.entries(declarations)){
  fail(Object.hasOwn(readOnlyArtifactKinds,name)&&Object.hasOwn(inventory,name)&&keys(value,['sha256','kind','evidence_note'])&&value.kind===readOnlyArtifactKinds[name]&&hex(value.sha256)&&value.sha256===inventory[name]&&text(value.evidence_note),'Invalid parent read-only artifact declaration');
 }
}
function topRecords(value){return Array.isArray(value)?value:[value];}
function noOperation(value){
 for(const row of topRecords(value)){
  fail(object(row),'Read-only artifact record must be an object');
  fail(!['operation_id','state','stage','status','request_sha256','response','remote_write_attempted','inflight_unknown','unknown_inflight'].some(k=>Object.hasOwn(row,k)),'Read-only declaration cannot override an actual operation/status/unknown write');
 }
}
function request(row,mode){
 if(!keys(row,['name','arguments'])||row.name!==mode)return false;
 const a=row.arguments;
 if(mode==='tool_catalog')return keys(a,['tool'])&&catalogTools.includes(a.tool);
 if(!keys(a,['tool','schema_version','arguments'])||!hex(a.schema_version)||!readTools.includes(a.tool))return false;
 if(a.tool==='material_sharing_departments')return keys(a.arguments,[]);
 if(a.tool==='material_rules_list')return keys(a.arguments,['platform','materialType'])&&a.arguments.platform==='google'&&a.arguments.materialType==='image';
 return keys(a.arguments,['dept_id','include_members','member_ids'])&&a.arguments.dept_id==='od-354b90f636b89e8d390a37e2946f9e17'&&a.arguments.include_members===true&&strings(a.arguments.member_ids)&&a.arguments.member_ids.length>0;
}
function catalog(value){
 const fields=['catalog_version','name','title','summary','category','operation_class','invoke_via','inputSchema','schema_version','description','annotations','authorization'];
 return Array.isArray(value)&&value.length===catalogTools.length&&new Set(value.map(v=>v.name)).size===catalogTools.length&&value.every(v=>keys(v,fields)&&catalogTools.includes(v.name)&&hex(v.catalog_version)&&hex(v.schema_version)&&['title','summary','category','description','authorization'].every(k=>text(v[k]))&&['read','write'].includes(v.operation_class)&&v.invoke_via===({material_upload_from_url:'material_upload_from_url',requirement_bind_materials:'requirement_bind_materials'}[v.name]??(v.operation_class==='read'?'tool_read':'tool_write'))&&v.operation_class===(readTools.includes(v.name)?'read':'write')&&object(v.inputSchema)&&v.inputSchema.type==='object'&&object(v.inputSchema.properties)&&(v.inputSchema.required===undefined||strings(v.inputSchema.required))&&object(v.annotations));
}
function context(value){
 if(!Array.isArray(value)||value.length!==3)return false;
 const [departments,options,rules]=value;
 if(!keys(departments,['departments'])||!Array.isArray(departments.departments)||!departments.departments.every(d=>keys(d,['deptId','deptName'])&&typeof d.deptId==='string'&&typeof d.deptName==='string'))return false;
 if(!keys(options,['deptId','revision','fields','defaults_persisted_by_query','member_directory'])||options.deptId!=='od-354b90f636b89e8d390a37e2946f9e17'||!text(options.revision)||options.defaults_persisted_by_query!==false||!keys(options.fields,['status','tags','bizLine','category','locales','channels']))return false;
 for(const [key,field]of Object.entries(options.fields))if(!keys(field,['fieldKey','options','nonDeletableOptions','optionStyles','updatedAt'])||field.fieldKey!==key||!strings(field.options)||!strings(field.nonDeletableOptions)||!object(field.optionStyles)||!text(field.updatedAt))return false;
 const directory=options.member_directory;
 if(!keys(directory,['deptId','deptName','members','query_meta'])||directory.deptId!==options.deptId||directory.deptName!=='SHE'||!Array.isArray(directory.members)||directory.members.length!==0||!object(directory.query_meta)||directory.query_meta.total!==0||directory.query_meta.returned!==0||directory.query_meta.roles_inferred!==false)return false;
 if(!keys(rules,['filters','ruleCount','platforms'])||!keys(rules.filters,['platform','materialType','adType'])||rules.filters.platform!=='google'||rules.filters.materialType!=='image'||rules.filters.adType!==null||!Number.isInteger(rules.ruleCount)||!Array.isArray(rules.platforms)||rules.platforms.length!==1)return false;
 const platform=rules.platforms[0];if(!keys(platform,['id','name','aliases','materialTypes'])||platform.id!=='google'||!text(platform.name)||!strings(platform.aliases)||!Array.isArray(platform.materialTypes)||platform.materialTypes.length!==1)return false;
 const material=platform.materialTypes[0];return keys(material,['id','name','mediaKind','aliases','description','rules'])&&material.id==='image'&&material.mediaKind==='image'&&text(material.name)&&text(material.description)&&strings(material.aliases)&&Array.isArray(material.rules)&&material.rules.length===rules.ruleCount&&material.rules.every(rule=>keys(rule,['id','name','code','severity','message','fixSuggestion','phases','appliesTo','assertion','updatedAt'])&&['id','name','code','message','fixSuggestion','updatedAt'].every(k=>text(rule[k]))&&['error','warning'].includes(rule.severity)&&strings(rule.phases)&&object(rule.appliesTo)&&object(rule.assertion));
}
export function verifyReadOnlyArtifact({name,bytes,declaration,inventory,row,catalogBytes}){
 if(declaration===undefined)return false;
 validateReadOnlyDeclarations({[name]:declaration},inventory);
 fail(sha(bytes)===declaration.sha256,'Declared read-only artifact SHA changed');
 const value=JSON.parse(bytes);noOperation(value);
 let valid=false;
 switch(declaration.kind){
 case 'tool_catalog_requests':valid=Array.isArray(value)&&value.length===7&&new Set(value.map(v=>v.arguments?.tool)).size===7&&value.every(v=>request(v,'tool_catalog'));break;
 case 'readonly_tool_requests': {
  fail(catalogBytes&&sha(catalogBytes)===inventory['publication-catalog-normalized-1845.json'],'Read-only request catalog missing or changed');
  const definitions=JSON.parse(catalogBytes);noOperation(definitions);fail(catalog(definitions),'Read-only request catalog shape invalid');
  valid=Array.isArray(value)&&value.length===3&&new Set(value.map(v=>v.arguments?.tool)).size===3&&value.every(v=>{if(!request(v,'tool_read'))return false;const target=definitions.find(d=>d.name===v.arguments.tool);return target?.operation_class==='read'&&target.invoke_via==='tool_read'&&target.schema_version===v.arguments.schema_version;});break;
 }
 case 'tool_catalog_definitions':valid=catalog(value);break;
 case 'readonly_context_results':valid=context(value);break;
 case 'contract_diff':valid=Array.isArray(value)&&value.length===6&&new Set(value.map(v=>v.tool)).size===6&&value.every(v=>keys(v,['tool','different_fields'])&&catalogTools.includes(v.tool)&&object(v.different_fields)&&Object.keys(v.different_fields).length>0&&Object.entries(v.different_fields).every(([k,d])=>['observedAt','description','catalog_version','schema_version'].includes(k)&&keys(d,['old','new'])&&(d.old===null||typeof d.old==='string')&&(d.new===null||typeof d.new==='string')));break;
 case 'contract_review':valid=keys(value,['original_caller_sha256','local_caller_sha256','contract_file','contract_sha256','reviewer','review','authorization','additional_review'])&&['original_caller_sha256','local_caller_sha256','contract_sha256'].every(k=>hex(value[k]))&&value.contract_file==='publication-catalog-normalized-1845.json'&&value.contract_sha256===inventory[value.contract_file]&&['reviewer','review','authorization','additional_review'].every(k=>text(value[k]));break;
 case 'review_gate':valid=keys(value,['requirement_id','passed','reviewed_by','evidence','outputs'])&&value.requirement_id===row.id&&value.passed===true&&text(value.reviewed_by)&&text(value.evidence)&&Array.isArray(value.outputs)&&value.outputs.length>0&&value.outputs.every(v=>keys(v,['path','sha256'])&&text(v.path)&&!v.path.includes('..')&&!/^[A-Za-z]:|^[/\\]/.test(v.path)&&hex(v.sha256));break;
 }
 fail(valid,'Read-only artifact does not match its audited kind/shape/tool allowlist');
 return true;
}
