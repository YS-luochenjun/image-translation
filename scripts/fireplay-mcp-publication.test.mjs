import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { publish, validateSchema } from './fireplay-mcp-publication.mjs';

import { publicationDefinitions as definitions } from './fireplay-offline-test-contracts.mjs';
import { tmpdir } from 'node:os';
import { rm } from 'node:fs/promises';
const code='TEST-20000101-001';
const requirementId='11111111-1111-4111-8111-111111111111';
const resourceId='22222222-2222-4222-8222-222222222222';
const materialId='33333333-3333-4333-8333-333333333333';
const publicUrl=`https://example.invalid/materials/${resourceId}.png`;
const bytes=Buffer.from([137,80,78,71,13,10,26,10,1,2,3]);
const hash=createHash('sha256').update(bytes).digest('hex');
async function fixture(t) {
  const root=await mkdtemp(join(tmpdir(),'publication-test-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const folder=join(root,'work','fireplay-production',code);
  await mkdir(folder,{recursive:true});
  await mkdir(join(root,'work','fireplay-readonly'),{recursive:true});
  await writeFile(join(root,'work','fireplay-readonly','publication-full-definitions.json'),JSON.stringify(definitions));
  await writeFile(join(folder,'intake.json'),JSON.stringify({summary:{id:requirementId,code,assetType:'image',canBindMaterials:true,assigneeIds:['valid-user']},brief:{id:requirementId},delivery:{id:requirementId}}));
  await writeFile(join(folder,'image.png'),bytes);
  await writeFile(join(folder,'gate.json'),JSON.stringify({requirement_id:requirementId,passed:true,reviewed_by:'offline-test',evidence:'synthetic fixture, not production approval',outputs:[{path:'image.png',sha256:hash}]}));
  const calls=[];
  const transport={call:async(name,args)=>{
    calls.push({name,args});
    if(name==='tool_catalog')return {structuredContent:definitions.find(tool=>tool.name===args.tool)};
    if(name==='tool_write'&&args.tool==='material_r2_upload_from_bytes')return {structuredContent:{publicUrl,resourceId}};
    if(name==='material_upload_from_url')return {structuredContent:{items:[{material_id:materialId}]}};
    return {structuredContent:{ok:true}};
  }};
  const upload={tool:'material_r2_upload_from_bytes',schema_version:definitions[0].schema_version,gate_file:'gate.json',output_sha256:hash,arguments:{content_type:'image/png',ext:'png',file_name:'image.png'}};
  return {root,folder,calls,transport,upload};
}
const run=(f,request,operationId=randomUUID())=>publish({root:f.root,code,request,operationId,transport:f.transport});
const importRequest=(uploadId)=>({tool:'material_upload_from_url',schema_version:definitions[1].schema_version,gate_file:'gate.json',upload_operation_ids:[uploadId],arguments:{confirmed:true,items:[{url:publicUrl,resource_id:resourceId,already_in_r2:true,type:'image',source:'MCP',name:`${code}-source01-fr`,shared_dept_id:'researched-group'}]}});

test('reviewed image -> own import -> own bind; journals contain no base64',async t=>{
  const f=await fixture(t); const uploadId=randomUUID();
  await run(f,f.upload,uploadId);
  await run(f,importRequest(uploadId));
  await run(f,{tool:'requirement_bind_materials',schema_version:definitions[3].schema_version,arguments:{confirmed:true,items:[{requirement_id:requirementId,material_ids:[materialId]}]}});
  assert.equal(f.calls.filter(call=>call.name==='tool_catalog').length,3);
  assert.equal(f.calls.length,6);
  const journal=await readFile(join(f.folder,'publication-journal.jsonl'),'utf8');
  assert.ok(!journal.includes('file_base64'));
  assert.deepEqual(journal.trim().split('\n').map(line=>JSON.parse(line).state),['unknown','received','unknown','received','unknown','received']);
});

test('timeout leaves durable unknown and forbids fresh UUID replay',async t=>{
  const f=await fixture(t);
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='tool_catalog'?original(name,args):Promise.reject(new Error('fake timeout'));
  await assert.rejects(run(f,f.upload),/fake timeout/);
  const count=f.calls.length;
  await assert.rejects(run(f,f.upload),/Unknown operation/);
  assert.equal(f.calls.length,count);
  const events=(await readFile(join(f.folder,'publication-journal.jsonl'),'utf8')).trim().split('\n');
  assert.equal(events.length,1); assert.equal(JSON.parse(events[0]).state,'unknown');
});

test('rejects schema drift before writing journal or dispatching',async t=>{
  const f=await fixture(t);
  f.transport.call=async()=>({structuredContent:{...definitions[0],schema_version:'f'.repeat(64)}});
  await assert.rejects(run(f,f.upload),/contract drift/);
  await assert.rejects(readFile(join(f.folder,'publication-journal.jsonl')),/ENOENT/);
});

test('rejects forbidden tool, wrong demand bind and unproven material',async t=>{
  const f=await fixture(t);
  await assert.rejects(run(f,{tool:'requirement_update_status',arguments:{}}),/Tool forbidden/);
  await assert.rejects(run(f,{tool:'requirement_bind_materials',schema_version:definitions[3].schema_version,arguments:{confirmed:true,items:[{requirement_id:randomUUID(),material_ids:[materialId]}]}}),/Wrong requirement/);
  assert.equal(f.calls.length,0);
});

test('rejects changed reviewed bytes and unsupported video import fields',async t=>{
  const f=await fixture(t);
  await writeFile(join(f.folder,'image.png'),'changed');
  await assert.rejects(run(f,f.upload),/output changed/);
  const request=importRequest(randomUUID());request.arguments.VedioSouce='Other';
  await assert.rejects(run(f,request),/Only ordinary/);
  assert.equal(f.calls.length,0);
});

test('duplicate operations and duplicate imported content cannot replay',async t=>{
  const f=await fixture(t); const uploadId=randomUUID();const importId=randomUUID();
  await run(f,f.upload,uploadId);await run(f,importRequest(uploadId),importId);
  await assert.rejects(run(f,importRequest(uploadId),importId),/already recorded/);
  await assert.rejects(run(f,importRequest(uploadId)),/already imported/);
});

test('excluded designer identity cannot be attributed',async t=>{
  const f=await fixture(t); const uploadId=randomUUID();
  await run(f,f.upload,uploadId); await run(f,importRequest(uploadId));
  await assert.rejects(run(f,{tool:'material_attribution_update',schema_version:definitions[2].schema_version,designer_members:[{feishuUserId:'valid-user',username:'罗晨钧'}],arguments:{material_ids:[materialId],designer_ids:['valid-user'],creation_type:'derivative',confirmed:false}}),/excluding/);
});

test('concurrent calls cannot race the per-demand journal',async t=>{
  const f=await fixture(t);
  await writeFile(join(f.folder,'publication.lock'),'existing lock');
  await assert.rejects(run(f,f.upload),/EEXIST/);
  assert.equal(f.calls.length,0);
});

test('schema validation rejects extra operator fields and wrong required value types',()=>{
  assert.throws(()=>validateSchema({material_ids:[materialId],confirmed:true,operator:'admin'},definitions[2].inputSchema),/unapproved field/);
  assert.throws(()=>validateSchema({material_ids:'wrong'},definitions[2].inputSchema),/expected array/);
});
