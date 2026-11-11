import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ordinary043Slot} from './ordinary-slot.js';
import {admit} from './admission.js';
const code='SHE-20261105-043',id='01a1ab5e-81f0-7b33-b7c8-a2d958e79b36';
async function fixture(t,{stage='accepted',status='running',title=code,inbox={'next-turn':[],'next-step':[]}}={}){
 const project=await mkdtemp(join(tmpdir(),'043slot-'));t.after(()=>rm(project,{recursive:true,force:true}));
 const folder=join(project,'work/fireplay-production',code);await mkdir(folder,{recursive:true});await writeFile(join(folder,'ordinary-session-journal.json'),JSON.stringify({requirement_id:id,code,session_id:'s',stage}));
 return {project,getStatus:()=>status,registry:{archivedSessionIds:[],list:()=>[{path:project,sessionIds:['s']}]},controller:{projections:async()=>({kind:'sequenced',values:{title,subagent:null,inbox}})}};
}
test('historical capacity includes ordinary043 running, queued and unknown dispatch',async t=>{
 for(const options of [{},{status:'idle',inbox:{'next-turn':[{}],'next-step':[]}},{status:'idle',stage:'dispatch_unknown'}])assert.deepEqual(await ordinary043Slot(await fixture(t,options)),[id]);
});
test('cold created and accepted idle empty043 do not reserve active slots',async t=>{
 for(const options of [{stage:'created',status:'idle'},{stage:'accepted',status:'idle'}])assert.deepEqual(await ordinary043Slot(await fixture(t,options)),[]);
});
test('running title/ordinary identity mismatch fails closed',async t=>{const options=await fixture(t,{title:'other'});await assert.rejects(()=>ordinary043Slot(options),/identity mismatch/);});
test('shared gate serializes ordinary and historical admission',async()=>{
 const trace=[];let release;const blocked=new Promise(resolve=>{release=resolve;});
 const first=admit(async()=>{trace.push('ordinary-start');await blocked;trace.push('ordinary-end');});
 const second=admit(async()=>{trace.push('historical');});
 await Promise.resolve();assert.deepEqual(trace,['ordinary-start']);release();await Promise.all([first,second]);assert.deepEqual(trace,['ordinary-start','ordinary-end','historical']);
});
