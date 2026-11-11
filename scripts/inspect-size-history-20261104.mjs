import {readFile,writeFile} from 'node:fs/promises';
import {connectProxy,decodeResult} from './fireplay-mcp-publication.mjs';
const catalog=JSON.parse(await readFile('work/fireplay-readonly/size-contract-catalog-20261104.json','utf8'));
const definition=catalog.find(r=>r.result.name==='requirement_list').result;
const allowed=definition.inputSchema.properties.asset_type.anyOf[0].enum;
const cases=['button','image','tk_carousel','fb_image'].map(asset_type=>({asset_type}));
const connection=await connectProxy(),receipts=[];
try {
 for(const filters of cases) {
   if(filters.asset_type&&!allowed.includes(filters.asset_type))throw Error('Unpublished type');
   const args={scope:'all',view:'delivery',status:'done',date_to:'2026-10-31',only_with_materials:true,limit:6,...filters};
   for(const k of Object.keys(args))if(!definition.inputSchema.properties[k])throw Error('Unknown field');
   const result=decodeResult(await connection.call('requirement_list',args));
   receipts.push({name:'requirement_list',arguments:args,observedAt:new Date().toISOString(),result});
 }
 const output='work/fireplay-readonly/size-history-discovery-20261104.json';await writeFile(output,JSON.stringify(receipts,null,2),{flag:'wx'});
 console.log(JSON.stringify({output,results:receipts.map(r=>({filter:r.arguments,total:r.result.total,next_offset:r.result.next_offset,items:r.result.items?.map(i=>({id:i.id,code:i.code,title:i.title,assetType:i.assetType,keys:Object.keys(i),materialCount:i.materials?.length,materials:i.materials?.slice(0,2)}))}))},null,2));
}finally{connection.close();}
