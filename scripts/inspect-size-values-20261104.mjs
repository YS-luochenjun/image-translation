import {readFile,writeFile} from 'node:fs/promises';
import {connectProxy,decodeResult} from './fireplay-mcp-publication.mjs';
const catalog=JSON.parse(await readFile('work/fireplay-readonly/size-contract-catalog-20261104.json','utf8'));
const intake=JSON.parse(await readFile('work/fireplay-production/intake-index-20261103.json','utf8'));
const requests=[['material_rules_list',{}],['requirement_create_options',{dept_id:'od-354b90f636b89e8d390a37e2946f9e17'}],['requirement_list',{scope:'all',view:'blind_brief',requirement_ids:intake.records.map(r=>r.id),limit:100}]];
const connection=await connectProxy(),receipts=[];
try {
 for(const [name,args] of requests) {
   const definition=catalog.find(r=>r.result.name===name)?.result;
   if(definition?.operation_class!=='read')throw Error('Not reviewed read-only contract');
   const callArgs=definition.invoke_via==='tool_read'?{tool:name,schema_version:definition.schema_version,arguments:args}:args;
   const result=decodeResult(await connection.call(definition.invoke_via,callArgs));
   receipts.push({name,arguments:args,observedAt:new Date().toISOString(),result});
   if(result?.isError)throw Error('Read failed; no retry');
 }
 const output='work/fireplay-readonly/size-contract-values-20261104.json';
 await writeFile(output,JSON.stringify(receipts,null,2),{flag:'wx'});
 console.log(JSON.stringify({output,items:receipts.map(r=>({name:r.name,keys:Object.keys(r.result??{})}))}));
}finally{connection.close();}
