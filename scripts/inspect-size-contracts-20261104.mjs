import {writeFile} from 'node:fs/promises';
import {connectProxy,decodeResult} from './fireplay-mcp-publication.mjs';
const connection=await connectProxy();
const receipts=[];
try {
  for(const args of [{},{tool:'requirement_list'},{tool:'requirement_create_options'},{tool:'material_rules_list'},{tool:'material_detail'}]) {
    const result=decodeResult(await connection.call('tool_catalog',args));
    receipts.push({tool:'tool_catalog',arguments:args,observedAt:new Date().toISOString(),result});
  }
  const output='work/fireplay-readonly/size-contract-catalog-20261104.json';
  await writeFile(output,JSON.stringify(receipts,null,2),{flag:'wx'});
  console.log(JSON.stringify({output,items:receipts.map(r=>({request:r.arguments,name:r.result.name,category:r.result.categories,operation_class:r.result.operation_class,schema_version:r.result.schema_version,invoke_via:r.result.invoke_via,properties:Object.keys(r.result.inputSchema?.properties??{})}))}));
} finally {connection.close();}
