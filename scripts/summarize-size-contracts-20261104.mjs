import {readFile} from 'node:fs/promises';
const rows=JSON.parse(await readFile('work/fireplay-readonly/size-contract-values-20261104.json','utf8'));
const rule=rows.find(r=>r.name==='material_rules_list').result;
console.log(JSON.stringify({rule},null,2));
const options=rows.find(r=>r.name==='requirement_create_options').result;
console.log(JSON.stringify({fixed:options.fixed,configured:options.configured,defaults:options.defaults},null,2));
const requirements=rows.find(r=>r.name==='requirement_list').result;
console.log(JSON.stringify({total:requirements.total,returned:requirements.returned,next_offset:requirements.next_offset,keys:requirements.items?.[0]?Object.keys(requirements.items[0]):[],types:requirements.items?.map(r=>({code:r.code,assetType:r.assetType,channels:r.channels,category:r.category,dimensionKeys:Object.keys(r).filter(k=>/size|dimension|format|width|height|subtype|variant/i.test(k))}))},null,2));
