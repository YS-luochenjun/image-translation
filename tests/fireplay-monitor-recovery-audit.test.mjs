import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {auditRecoveryState} from '../scripts/fireplay-monitor-recovery-audit.mjs';

const state=()=>({checkpoints:{full:'old-full',active:'new-active',tracked:{batch:{at:'old',ids:['a','b'],evidence:'old.json'}}},
 summaries:{a:{hash:'h',updated_at:'t',evidence:'old.json',review_evidence:'review.json'},b:{hash:'h2',updated_at:'t2',evidence:'other.json'}},
 pending_briefs:{a:{code:'A',summary_hash:'h',updated_at:'t',evidence:'other.json'},b:{code:'B',summary_hash:'wrong',updated_at:'t2',evidence:'old.json'},c:{code:'C',summary_hash:'x',updated_at:'t',evidence:'other.json'}},
 tracked_missing:{batch:['b']},last_receipts:{tracked:'old.json',full:'other.json'}});

test('inventories tainted cache, pending, tracked and missing without modifying state',()=>{
 const value=state(),before=structuredClone(value),result=auditRecoveryState(value,resolve('old.json'));
 assert.deepEqual(value,before);
 assert.deepEqual(result.affected_summaries.map(x=>x.id),['a']);
 assert.deepEqual(result.affected_pending.map(x=>x.id),['a','b']);
 assert.deepEqual(result.pending_mismatches.map(x=>x.id),['b','c']);
 assert.deepEqual(result.affected_tracked_checkpoints[0].missing,['b']);
 assert.deepEqual(result.affected_last_receipts,['tracked']);
 assert.equal(result.affected_summaries[0].review_evidence,'review.json');
});

test('CLI hashes actual files and performs no state or receipt writes',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'monitor-audit-'));
 try{
  const statePath=join(dir,'state.json'),receiptPath=join(dir,'old.json');
  const input=state();input.summaries.a.evidence=receiptPath;input.checkpoints.tracked.batch.evidence=receiptPath;
  await writeFile(statePath,JSON.stringify(input));await writeFile(receiptPath,'historical bytes');
  const before=await readFile(statePath),beforeReceipt=await readFile(receiptPath);
  const run=spawnSync(process.execPath,['scripts/fireplay-monitor-recovery-audit.mjs','--state',statePath,'--tainted-receipt',receiptPath],{encoding:'utf8'});
  assert.equal(run.status,0,run.stderr);
  const result=JSON.parse(run.stdout);
  assert.match(result.state_sha256,/^[a-f0-9]{64}$/);assert.match(result.tainted_receipt_sha256,/^[a-f0-9]{64}$/);
  assert.equal(result.affected_summaries.length,1);
  assert.deepEqual(await readFile(statePath),before);assert.deepEqual(await readFile(receiptPath),beforeReceipt);
 }finally{await rm(dir,{recursive:true,force:true});}
});
