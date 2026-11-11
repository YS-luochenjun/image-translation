import {connectProxy,decodeResult,validateSchema} from './fireplay-mcp-publication.mjs';
const transport=await connectProxy();
const unwrap=raw=>{if(raw.isError)throw new Error('Read rejected');return decodeResult(raw);};
try {
 const catalog=unwrap(await transport.call('tool_catalog',{tool:'requirement_list'}));
 if(catalog.operation_class!=='read'||catalog.invoke_via!=='requirement_list')throw new Error('Unexpected read contract');
 const query={scope:'all',view:'summary',asset_type:['image','button'],ai_mode:'image_translate_or_resize',status:'not_started',pagination:'cursor',limit:200,offset:0};
 const items=[],seen=new Set();let cursor=null,pages=0;
 do {
  const args={...query,...(cursor?{cursor}:{})};validateSchema(args,catalog.inputSchema);
  const response=unwrap(await transport.call('requirement_list',args));
  if(!Array.isArray(response.items)||!Object.hasOwn(response,'next_cursor'))throw new Error('Incomplete cursor evidence');
  items.push(...response.items);pages++;
  cursor=response.next_cursor;
  if(cursor&&(seen.has(cursor)||pages>=100))throw new Error('Repeated or excessive cursor');
  if(cursor)seen.add(cursor);
 }while(cursor);
 console.log(JSON.stringify({stage:'visible_unstarted_discovery',query,pages,complete:true,items},null,2));
 const ids=items.filter(row=>row.code==='SHE-20261105-043'||row.code==='SHE-20261103-068').map(row=>row.id);
 if(ids.length){
  for(const view of ['blind_brief','delivery']){
   const args={scope:'all',view,requirement_ids:ids,limit:100,offset:0};validateSchema(args,catalog.inputSchema);
   const result=unwrap(await transport.call('requirement_list',args));
   console.log(JSON.stringify({stage:view,arguments:args,result},null,2));
  }
 }
}finally{transport.close();}
