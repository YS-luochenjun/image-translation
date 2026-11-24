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
  const entries=journal.trim().split('\n').map(JSON.parse);
  assert.deepEqual(entries.map(entry=>entry.state),['unknown','received','unknown','received','unknown','received']);
  assert.ok(entries.filter(entry=>entry.state==='received').every(entry=>entry.receipt_sha256&&!entry.response));
  const receipt=join(f.folder,`publication-${uploadId}.json`);
  assert.ok(JSON.parse(await readFile(receipt,'utf8')).response);
  await writeFile(receipt,'{}');
  await assert.rejects(run(f,f.upload),/receipt hash mismatch/);
});

test('a previously uploaded and imported reviewed image stays frozen',async t=>{
  const f=await fixture(t);
  const firstUpload=randomUUID();
  const secondUpload=randomUUID();
  await run(f,f.upload,firstUpload);
  await run(f,importRequest(firstUpload));
  await assert.rejects(run(f,f.upload,secondUpload), /frozen resource/);
  await assert.rejects(run(f,importRequest(firstUpload)), /already registered/);
  assert.equal(f.calls.filter(call=>call.name==='tool_write' && call.args.tool==='material_r2_upload_from_bytes').length,1);
  assert.equal(f.calls.filter(call=>call.name==='material_upload_from_url').length,1);
});

test('one upload ignores unrelated candidates and freezes the member across path and byte changes',async t=>{
  const f=await fixture(t);
  const identity={source_id:'source',language:'ja',member_id:'square-1'};
  const gateFile=join(f.folder,'gate.json');
  const gate=JSON.parse(await readFile(gateFile,'utf8'));
  gate.outputs=[{path:'image.png',sha256:hash,...identity},{path:'unrelated-missing.png',sha256:'a'.repeat(64)}];
  await writeFile(gateFile,JSON.stringify(gate));
  const uploadId=randomUUID();
  await run(f,f.upload,uploadId);
  await rm(join(f.folder,'image.png'));
  const revised=Buffer.concat([bytes,Buffer.from([4])]);
  const revisedHash=createHash('sha256').update(revised).digest('hex');
  gate.outputs=[{path:'revised.png',sha256:revisedHash,...identity}];
  await writeFile(gateFile,JSON.stringify(gate));
  await writeFile(join(f.folder,'revised.png'),revised);
  await assert.rejects(run(f,{...f.upload,output_sha256:revisedHash}),/frozen resource/);
  const request=importRequest(uploadId);
  delete request.gate_file;
  await run(f,request);
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

test('upload isolation retains the original operation and permits disjoint upload, import and bind', async t => {
  const fixtureData = await fixture(t);
  const originalCall = fixtureData.transport.call;
  const blockedId = randomUUID();
  fixtureData.transport.call = async (name, args) => {
    if (name === 'tool_catalog') return originalCall(name, args);
    throw new Error('fake timeout');
  };
  await assert.rejects(run(fixtureData, fixtureData.upload, blockedId), /fake timeout/);
  fixtureData.transport.call = originalCall;
  const other = Buffer.concat([bytes, Buffer.from([4])]);
  const otherHash = createHash('sha256').update(other).digest('hex');
  await writeFile(join(fixtureData.folder, 'other.png'), other);
  await writeFile(join(fixtureData.folder, 'alias.png'), bytes);
  const gatePath = join(fixtureData.folder, 'gate.json');
  const gate = JSON.parse(await readFile(gatePath, 'utf8'));
  gate.outputs.push({path: 'other.png', sha256: otherHash}, {path: 'alias.png', sha256: hash});
  await writeFile(gatePath, JSON.stringify(gate));
  const isolated = { ...fixtureData.upload, isolate_unknown_uploads: true };
  await assert.rejects(run(fixtureData, isolated, blockedId), /replay forbidden/);
  await assert.rejects(run(fixtureData, { ...isolated, output_path: 'alias.png' }), /overlaps/);
  const otherId = randomUUID();
  await run(fixtureData, { ...isolated, output_path: 'other.png', output_sha256: otherHash }, otherId);
  await run(fixtureData, { ...importRequest(otherId), isolate_unknown_uploads: true });
  await run(fixtureData, {tool:'requirement_bind_materials', schema_version:definitions[3].schema_version,
    isolate_unknown_uploads: true, arguments:{confirmed:true,items:[{requirement_id:requirementId,material_ids:[materialId]}]}});
  const entries = (await readFile(join(fixtureData.folder, 'publication-journal.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.deepEqual(entries.filter(entry => entry.operation_id === blockedId).map(entry => entry.state), ['unknown']);
  assert.ok(entries.slice(1).every(entry => entry.isolated_unknown_upload_ids.includes(blockedId)));
});

test('upload isolation does not lift a requirement-wide hold for an unknown import', async t => {
  const fixtureData = await fixture(t);
  const uploadId = randomUUID();
  await run(fixtureData, fixtureData.upload, uploadId);
  const originalCall = fixtureData.transport.call;
  fixtureData.transport.call = async (name, args) => {
    if (name === 'tool_catalog') return originalCall(name, args);
    throw new Error('fake timeout');
  };
  await assert.rejects(run(fixtureData, importRequest(uploadId)), /fake timeout/);
  fixtureData.transport.call = originalCall;
  await assert.rejects(run(fixtureData, { ...fixtureData.upload, isolate_unknown_uploads: true }), /Only attributable unknown R2 uploads/);
});

test('explicit user reupload authorization preserves original unknown and scopes replacement operations',async t=>{
  const fixtureData=await fixture(t);
  const originalCall=fixtureData.transport.call;
  const originalId=randomUUID();
  fixtureData.transport.call=async(name,args)=>name==='tool_catalog'?originalCall(name,args):Promise.reject(new Error('fake timeout'));
  await assert.rejects(run(fixtureData,fixtureData.upload,originalId),/fake timeout/);
  fixtureData.transport.call=originalCall;
  const original=JSON.parse((await readFile(join(fixtureData.folder,'publication-journal.jsonl'),'utf8')).trim());
  const replacementId=randomUUID();
  const importId=randomUUID();
  const authorization={authorization_source:'explicit_current_user_message',user_request:'不管历史，重新上传。绑定需求',code,requirement_id:requirementId,historical_unknown_operations:[{operation_id:originalId,request_sha256:original.request_sha256}],new_operations:{[replacementId]:'material_r2_upload_from_bytes',[importId]:'material_upload_from_url'}};
  await writeFile(join(fixtureData.folder,'authorization.json'),JSON.stringify(authorization));
  const authorizedUpload={...fixtureData.upload,reupload_authorization_file:'authorization.json'};
  await assert.rejects(run(fixtureData,authorizedUpload),/Operation not included/);
  await run(fixtureData,authorizedUpload,replacementId);
  await run(fixtureData,{...importRequest(replacementId),reupload_authorization_file:'authorization.json'},importId);
  const journal=(await readFile(join(fixtureData.folder,'publication-journal.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
  assert.equal(journal.filter(entry=>entry.operation_id===originalId).length,1);
  assert.equal(journal[0].state,'unknown');
  assert.ok(journal.slice(1).every(entry=>entry.reupload_authorization.sha256));
  authorization.requirement_id=randomUUID();
  const deniedId=randomUUID();
  authorization.new_operations[deniedId]='material_r2_upload_from_bytes';
  await writeFile(join(fixtureData.folder,'authorization.json'),JSON.stringify(authorization));
  await assert.rejects(run(fixtureData,authorizedUpload,deniedId),/same-requirement/);
  authorization.requirement_id=requirementId;
  authorization.historical_unknown_operations[0].request_sha256='0'.repeat(64);
  await writeFile(join(fixtureData.folder,'authorization.json'),JSON.stringify(authorization));
  await assert.rejects(run(fixtureData,authorizedUpload,deniedId),/Unapproved unknown/);
  authorization.historical_unknown_operations[0].request_sha256=original.request_sha256;
  await writeFile(join(fixtureData.folder,'authorization.json'),JSON.stringify(authorization));
  const otherBytes=Buffer.concat([bytes,Buffer.from([5])]);
  const otherHash=createHash('sha256').update(otherBytes).digest('hex');
  await writeFile(join(fixtureData.folder,'other.png'),otherBytes);
  const gateFile=join(fixtureData.folder,'gate.json');
  const gate=JSON.parse(await readFile(gateFile,'utf8'));
  gate.outputs.push({path:'other.png',sha256:otherHash});
  await writeFile(gateFile,JSON.stringify(gate));
  const otherUpload={...authorizedUpload,output_sha256:otherHash};
  fixtureData.transport.call=async(name,args)=>name==='tool_catalog'?originalCall(name,args):Promise.reject(new Error('new timeout'));
  await assert.rejects(run(fixtureData,otherUpload,deniedId),/new timeout/);
  fixtureData.transport.call=originalCall;
  const nextId=randomUUID();
  authorization.new_operations[nextId]='material_r2_upload_from_bytes';
  await writeFile(join(fixtureData.folder,'authorization.json'),JSON.stringify(authorization));
  await assert.rejects(run(fixtureData,authorizedUpload,nextId),/Unapproved unknown/);
});

test('rejects schema drift before writing journal or dispatching',async t=>{
  const f=await fixture(t);
  f.transport.call=async()=>({structuredContent:{...definitions[0],schema_version:'f'.repeat(64)}});
  await assert.rejects(run(f,f.upload),/contract drift/);
  await assert.rejects(readFile(join(f.folder,'publication-journal.jsonl')),/ENOENT/);
});

test('reviewed per-demand contract overrides stale global cache and is journaled',async t=>{
  const f=await fixture(t);
  const definition={...definitions[0],schema_version:'a'.repeat(64)};
  const contractPath=join(f.folder,`contract-${definition.name}.json`);
  const snapshot=JSON.stringify(definition);
  await writeFile(contractPath,snapshot);
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='tool_catalog'
    ? {structuredContent:definition} : original(name,args);
  await run(f,{...f.upload,schema_version:definition.schema_version});
  const entry=JSON.parse((await readFile(join(f.folder,'publication-journal.jsonl'),'utf8')).trim().split('\n')[0]);
  assert.ok(entry.contract.path.endsWith(`contract-${definition.name}.json`));
  assert.equal(entry.contract.sha256,createHash('sha256').update(snapshot).digest('hex'));
});

test('per-demand contract needs only the selected tool and no global catalog',async t=>{
  const f=await fixture(t);
  await writeFile(join(f.folder,`contract-${f.upload.tool}.json`),JSON.stringify(definitions[0]));
  await rm(join(f.root,'work','fireplay-readonly','publication-full-definitions.json'));
  await run(f,f.upload);
  assert.equal(f.calls.length,2);
});

test('invalid per-demand contract cannot silently fall back to old cache',async t=>{
  const f=await fixture(t);
  const contractPath=join(f.folder,`contract-${f.upload.tool}.json`);
  await writeFile(contractPath,'not json');
  await assert.rejects(run(f,f.upload),SyntaxError);
  await writeFile(contractPath,JSON.stringify(definitions[1]));
  await assert.rejects(run(f,f.upload),/tool mismatch/);
  await writeFile(contractPath,JSON.stringify({...definitions[0],schema_version:'a'.repeat(64)}));
  await assert.rejects(run(f,f.upload),/pin cached schema/);
  assert.equal(f.calls.length,0);
  await assert.rejects(readFile(join(f.folder,'publication-journal.jsonl')),/ENOENT/);
});

test('live drift from scoped contract still blocks all business writes',async t=>{
  const f=await fixture(t);
  await writeFile(join(f.folder,`contract-${f.upload.tool}.json`),JSON.stringify(definitions[0]));
  f.transport.call=async()=>({structuredContent:{...definitions[0],invoke_via:'other_write'}});
  await assert.rejects(run(f,f.upload),/contract drift/);
  await assert.rejects(readFile(join(f.folder,'publication-journal.jsonl')),/ENOENT/);
});

for(const style of ['snake','both']) {
  test(`accepts ${style} upload receipt aliases through own import and bind`,async t=>{
    const f=await fixture(t);
    const original=f.transport.call;
    f.transport.call=async(name,args)=>{
      if(name==='tool_write'&&args.tool===f.upload.tool) {
        return {structuredContent:{success:true,public_url:publicUrl,resource_id:resourceId,
          ...(style==='both'?{publicUrl,resourceId}:{})}};
      }
      return original(name,args);
    };
    const uploadId=randomUUID();
    await run(f,f.upload,uploadId);
    await run(f,importRequest(uploadId));
    await run(f,{tool:'requirement_bind_materials',schema_version:definitions[3].schema_version,
      arguments:{confirmed:true,items:[{requirement_id:requirementId,material_ids:[materialId]}]}});
  });
}

test('ambiguous, invalid or unsuccessful upload receipts cannot authorize import',async t=>{
  for(const extra of [
    {public_url:'https://example.invalid/different.png'},
    {resource_id:randomUUID()},
    {success:false},
    {resourceId:null},
  ]) {
    const f=await fixture(t);
    const original=f.transport.call;
    f.transport.call=async(name,args)=>name==='tool_write'&&args.tool===f.upload.tool
      ? {structuredContent:{publicUrl,resourceId,...extra}} : original(name,args);
    const uploadId=randomUUID();
    await run(f,f.upload,uploadId);
    f.calls.length=0;
    await assert.rejects(run(f,importRequest(uploadId)),/does not prove one reviewed resource/);
    assert.equal(f.calls.length,0);
  }
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

test('operation IDs and already registered resources cannot replay',async t=>{
  const f=await fixture(t); const uploadId=randomUUID();const importId=randomUUID();
  await run(f,f.upload,uploadId);await run(f,importRequest(uploadId),importId);
  await assert.rejects(run(f,importRequest(uploadId),importId),/already recorded/);
  await assert.rejects(run(f,importRequest(uploadId)),/already registered/);
});

test('fully rejected validation import permits a corrected new operation using the original upload',async t=>{
  const f=await fixture(t);
  const uploadId=randomUUID();const rejectedId=randomUUID();
  await run(f,f.upload,uploadId);
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='material_upload_from_url'
    ? {structuredContent:{results:[{item_index:0,success:false,error:'MATERIAL_UPLOAD_VALIDATION_FAILED: image 素材缺少方图'}]}}
    : original(name,args);
  await run(f,importRequest(uploadId),rejectedId);
  const journalBefore=await readFile(join(f.folder,'publication-journal.jsonl'),'utf8');
  await assert.rejects(run(f,importRequest(uploadId),rejectedId),/already recorded/);
  f.transport.call=original;
  const corrected=importRequest(uploadId);corrected.arguments.items[0].name+= '-corrected';
  await run(f,corrected);
  const journalAfter=await readFile(join(f.folder,'publication-journal.jsonl'),'utf8');
  assert.ok(journalAfter.startsWith(journalBefore));
  assert.equal(f.calls.filter(call=>call.name==='tool_write'&&call.args.tool==='material_r2_upload_from_bytes').length,1);
});

test('partial import permits correction of only the rejected group',async t=>{
  const f=await fixture(t);
  const otherId=randomUUID(), otherResource=randomUUID();
  const otherUrl=`https://example.invalid/materials/${otherResource}.png`;
  const otherBytes=Buffer.concat([bytes,Buffer.from([19])]);
  const otherHash=createHash('sha256').update(otherBytes).digest('hex');
  const gateFile=join(f.folder,'gate.json');
  const gate=JSON.parse(await readFile(gateFile,'utf8'));
  gate.outputs.push({path:'other.png',sha256:otherHash});
  await writeFile(gateFile,JSON.stringify(gate));
  await writeFile(join(f.folder,'other.png'),otherBytes);
  const uploadId=randomUUID();
  await run(f,f.upload,uploadId);
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='tool_write'&&args.tool===f.upload.tool
    ? {structuredContent:{publicUrl:otherUrl,resourceId:otherResource}} : original(name,args);
  await run(f,{...f.upload,output_sha256:otherHash},otherId);
  const batch=importRequest(uploadId);
  const rejected={...batch.arguments.items[0],url:otherUrl,resource_id:otherResource};
  batch.upload_operation_ids.push(otherId);
  batch.arguments.items.push(rejected);
  f.transport.call=async(name,args)=>name==='material_upload_from_url'
    ? {structuredContent:{results:[{item_index:0,success:true,material_id:materialId},{item_index:1,success:false}]}}
    : original(name,args);
  await run(f,batch);
  await assert.rejects(run(f,importRequest(uploadId)),/already registered/);
  f.transport.call=original;
  const correction=importRequest(otherId);
  correction.arguments.items=[rejected];
  await run(f,correction);
});

test('append blocks previously imported content and requires reviewed provenance and own material',async t=>{
  const f=await fixture(t);const uploadId=randomUUID();
  await run(f,f.upload,uploadId);await run(f,importRequest(uploadId));
  const contract={name:'material_append_images',operation_class:'write',invoke_via:'tool_write',schema_version:'offline-append',inputSchema:{type:'object',properties:{confirmed:{type:'boolean'},material_id:{type:'string'},images:{type:'array',items:{type:'object',properties:{file_url:{type:'string'}},required:['file_url']}}},required:['confirmed','material_id','images']}};
  await writeFile(join(f.folder,'contract-material_append_images.json'),JSON.stringify(contract));
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='tool_catalog'&&args.tool===contract.name
    ? {structuredContent:contract} : original(name,args);
  const request={tool:contract.name,schema_version:contract.schema_version,gate_file:'gate.json',upload_operation_ids:[uploadId],arguments:{confirmed:true,material_id:materialId,images:[{file_url:publicUrl}]}};
  await assert.rejects(run(f,request),/already registered/);
  request.arguments.material_id=randomUUID();
  await assert.rejects(run(f,request),/same-demand imported material/);
  request.arguments.material_id=materialId;request.arguments.images[0].file_url='https://example.invalid/unproven';
  await assert.rejects(run(f,request),/not proven/);
});

test('unknown import cannot be resubmitted with a new operation ID',async t=>{
  const f=await fixture(t);const uploadId=randomUUID();
  await run(f,f.upload,uploadId);
  const original=f.transport.call;
  f.transport.call=async(name,args)=>name==='material_upload_from_url'
    ? Promise.reject(new Error('fake import timeout')) : original(name,args);
  await assert.rejects(run(f,importRequest(uploadId)),/fake import timeout/);
  f.calls.length=0;
  await assert.rejects(run(f,importRequest(uploadId)),/Unknown operation/);
  assert.equal(f.calls.length,0);
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
