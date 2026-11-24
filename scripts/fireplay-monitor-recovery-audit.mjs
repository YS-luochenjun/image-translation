#!/usr/bin/env node
// Read-only inventory. This does not authorize or perform checkpoint recovery.
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const samePath=(a,b)=>typeof a==='string'&&resolve(a)===resolve(b);

export function auditRecoveryState(state,taintedEvidence){
 if(!state?.summaries||!state.pending_briefs||!state.checkpoints?.tracked||typeof taintedEvidence!=='string'||!taintedEvidence)throw new Error('Invalid state or evidence path');
 const isTainted=value=>samePath(value,taintedEvidence);
 const summaries=Object.entries(state.summaries).filter(([,entry])=>isTainted(entry.evidence)).map(([id,entry])=>({id,...entry}));
 const pending=Object.entries(state.pending_briefs).map(([id,entry])=>{
  const summary=state.summaries[id];
  return {id,code:entry.code,evidence:entry.evidence,tainted:isTainted(entry.evidence)||isTainted(summary?.evidence),
   matches_summary:!!summary&&entry.summary_hash===summary.hash&&entry.updated_at===summary.updated_at};
 });
 const tracked=Object.entries(state.checkpoints.tracked).filter(([,entry])=>isTainted(entry.evidence)).map(([batch,entry])=>({batch,...entry,missing:state.tracked_missing?.[batch]??null}));
 const lastReceipts=Object.entries(state.last_receipts||{}).filter(([,value])=>isTainted(value)).map(([mode])=>mode);
 return {tainted_evidence:taintedEvidence,summary_count:Object.keys(state.summaries).length,affected_summaries:summaries,
  pending_count:pending.length,affected_pending:pending.filter(entry=>entry.tainted),pending_mismatches:pending.filter(entry=>!entry.matches_summary),
  affected_tracked_checkpoints:tracked,affected_last_receipts:lastReceipts,full_checkpoint:state.checkpoints.full,active_checkpoint:state.checkpoints.active,
  limitations:['Evidence path matching does not establish receipt authenticity or discover other contaminated receipts.',
   'Matching pending hashes and timestamps does not verify brief, delivery, or receipt contents.',
   'No state was changed; a fresh, complete official scan and separately reviewed recovery procedure are required.']};
}

async function main(){
 const args=process.argv.slice(2);
 if(args.length!==4||args[0]!=='--state'||args[2]!=='--tainted-receipt'||!args[1]||!args[3])throw new Error('Use --state FILE --tainted-receipt FILE');
 const [stateBytes,receiptBytes]=await Promise.all([readFile(args[1]),readFile(args[3])]);
 const result=auditRecoveryState(JSON.parse(stateBytes),args[3]);
 console.log(JSON.stringify({state_path:args[1],state_sha256:sha256(stateBytes),tainted_receipt_sha256:sha256(receiptBytes),...result},null,2));
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url)main().catch(error=>{console.error(error.message);process.exitCode=1;});
