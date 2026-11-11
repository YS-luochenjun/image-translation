import {readFile,writeFile} from 'node:fs/promises';
import {connectProxy,decodeResult,validateSchema} from './fireplay-mcp-publication.mjs';
const discovery=JSON.parse(await readFile('work/fireplay-readonly/size-history-discovery-20261104.json','utf8'));
const catalog=JSON.parse(await readFile('work/fireplay-readonly/size-contract-catalog-20261104.json','utf8'));
const definition=catalog.find(r=>r.result.name==='material_detail').result;
const candidates=discovery.flatMap(r=>(r.result.items??[]).flatMap(requirement=>(requirement.materials??[]).map(material=>({requirement_id:requirement.id,code:requirement.code,title:requirement.title,description:requirement.description,requestDate:requirement.requestDate,assetType:requirement.assetType,material}))));
const selected=[];
const add=item=>{if(item&&!selected.some(r=>r.material.id===item.material.id)&&item.material.createdAt<'2026-11-01')selected.push(item);};
for(const dimension of ['300x250','336x280','300x600'])add(candidates.find(r=>r.assetType==='button'&&r.material.name?.toLowerCase().includes(dimension)));
for(const type of ['button','image','tk_carousel','fb_image'])for(const item of candidates.filter(r=>r.assetType===type).slice(0,type==='button'?2:2))add(item);
const connection=await connectProxy(),receipts=[];
try {
 for(const source of selected) {
   const args={material_id:source.material.id,scope:'team'};validateSchema(args,definition.inputSchema);
   const result=decodeResult(await connection.call('material_detail',args));
   receipts.push({source,tool:'material_detail',arguments:args,observedAt:new Date().toISOString(),result});
 }
 const output='work/fireplay-readonly/size-history-material-details-20261104.json';await writeFile(output,JSON.stringify(receipts,null,2),{flag:'wx'});
 console.log(JSON.stringify({output,items:receipts.map(r=>({code:r.source.code,title:r.source.title,assetType:r.source.assetType,material:r.source.material.name,id:r.source.material.id,createdAt:r.source.material.createdAt,designer:r.source.material.designerIds,resultKeys:Object.keys(r.result??{}),materialKeys:r.result.material?Object.keys(r.result.material):[],resources:r.result.resources??r.result.material?.resources??r.result.data?.resources}))},null,2));
}finally{connection.close();}
