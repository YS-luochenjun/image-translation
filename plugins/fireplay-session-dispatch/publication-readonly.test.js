import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {readOnlyArtifactKinds,validateReadOnlyDeclarations,verifyReadOnlyArtifact} from './publication-readonly.js';
const hash=b=>createHash('sha256').update(b).digest('hex');
const row={id:'01a1a029-042d-7377-b95e-633a7e0568e4',code:'SHE-20261103-060'};
const folder=new URL('./fixtures/readonly/',import.meta.url);
const files=Object.fromEntries(await Promise.all(Object.keys(readOnlyArtifactKinds).map(async name=>[name,await readFile(new URL(name,folder))])));
const inventory=Object.fromEntries(Object.entries(files).map(([name,bytes])=>[name,hash(bytes)]));
const declare=(name,bytes=files[name])=>({sha256:hash(bytes),kind:readOnlyArtifactKinds[name],evidence_note:'Parent inspected this exact read-only typed artifact; no operation outcome inferred.'});
const verify=(name,bytes=files[name],declaration=declare(name,bytes),inv={...inventory,[name]:hash(bytes)})=>verifyReadOnlyArtifact({name,bytes,declaration,inventory:inv,row,catalogBytes:files['publication-catalog-normalized-1845.json']});
test('all seven audited real data shapes are accepted only with exact declaration and complete inventory entry',()=>{
 for(const name of Object.keys(files)){assert.equal(verify(name),true,name);assert.equal(verifyReadOnlyArtifact({name,bytes:files[name],inventory,row}),false);assert.throws(()=>verify(name,files[name],declare(name),{}),/declaration/);}
});
test('wrong SHA, wrong kind, unknown filename and extra declaration fields reject',()=>{
 const name='publication-context-requests.json',d=declare(name);assert.throws(()=>verify(name,files[name],{...d,sha256:'0'.repeat(64)}),/declaration/);assert.throws(()=>verify(name,files[name],{...d,kind:'catalog_requests'}),/declaration/);assert.throws(()=>verify(name,files[name],{...d,skip_unknown:true}),/declaration/);assert.throws(()=>validateReadOnlyDeclarations({'publication-status.json':d},{'publication-status.json':d.sha256}),/declaration/);
});
test('actual or unknown operation disguised as catalog can never be declared read-only',()=>{
 const name='publication-catalog-normalized-1845.json',source=JSON.parse(files[name]);
 for(const patch of [{operation_id:'actual'},{state:'unknown'},{stage:'sending'},{status:'waiting'},{remote_write_attempted:true},{remote_write_attempted:false},{inflight_unknown:true},{unknown_inflight:true},{response:{success:true}}]){const value=structuredClone(source);Object.assign(value[0],patch);assert.throws(()=>verify(name,Buffer.from(JSON.stringify(value))),/actual operation\/status\/unknown write/);}
});
test('request arrays allow catalog discovery and three actual read tools only',()=>{
 const name='publication-context-requests.json',source=JSON.parse(files[name]);for(const tool of ['material_upload_from_url','requirement_bind_materials','made_up_read']){const value=structuredClone(source);value[0].arguments.tool=tool;assert.throws(()=>verify(name,Buffer.from(JSON.stringify(value))),/shape\/tool/);}const value=structuredClone(source);value[0].name='tool_write';assert.throws(()=>verify(name,Buffer.from(JSON.stringify(value))),/shape\/tool/);
});
test('catalog write-tool schema descriptions are data; invocation payload/extra roots are refused',()=>{
 const name='publication-catalog-normalized-1845.json',source=JSON.parse(files[name]);assert.ok(source.some(v=>v.operation_class==='write'));assert.equal(verify(name),true);const value=structuredClone(source);value[0].arguments={file_base64:'not-allowed'};assert.throws(()=>verify(name,Buffer.from(JSON.stringify(value))),/shape\/tool/);
});
test('context query may not persist defaults and contract review must reference exact catalog inventory SHA',()=>{
 const context='publication-context-normalized.json',data=JSON.parse(files[context]);data[1].defaults_persisted_by_query=true;assert.throws(()=>verify(context,Buffer.from(JSON.stringify(data))),/shape\/tool/);const review='publication-contract-review.json',r=JSON.parse(files[review]);r.contract_sha256='0'.repeat(64);assert.throws(()=>verify(review,Buffer.from(JSON.stringify(r))),/shape\/tool/);
});
