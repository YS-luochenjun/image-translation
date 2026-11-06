// Synthetic offline contracts: exercise validation/routing only, never describe a
// live account, requirement, resource or authorization. These are not a catalog
// snapshot and must never be used as pinned contracts for real platform writes.
const string = {type:'string',minLength:1};
const boolean = {type:'boolean'};
const uuid = {type:'string',format:'uuid',pattern:'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'};
const list = items => ({type:'array',items,minItems:1});
const object = (properties,required=[]) => ({type:'object',properties,required,additionalProperties:false});
const definition = (name,inputSchema,operation='write',invoke=`tool_${operation}`) => ({name,inputSchema,operation_class:operation,invoke_via:invoke,schema_version:`offline-fixture-${name}-v1`});
const cropTarget=object({specCode:{enum:['square_1x1']},prompt:string},['specCode','prompt']);
const crop=object({workspace_id:uuid,source_asset_ids:list(uuid),targets:list(cropTarget),idempotency_key:string,confirmed:boolean},['workspace_id','source_asset_ids','targets','idempotency_key','confirmed']);
const translate=object({workspace_id:uuid,groups:list(object({sourceAssetIds:list(uuid)},['sourceAssetIds'])),target_languages:list(string),prompt_template:string,idempotency_key:string,confirmed:boolean},['workspace_id','groups','target_languages','prompt_template','idempotency_key','confirmed']);
const resize=object({asset_id:uuid,options:object({width:{type:'integer',minimum:1},height:{type:'integer',minimum:1},mode:{enum:['contain','cover']}},['width','height','mode']),idempotency_key:string,confirmed:boolean},['asset_id','options','idempotency_key','confirmed']);
const model={type:'object',oneOf:[
 object({action:{const:'list'},type:{enum:['generation']},media_type:{enum:['image']}},['action','type','media_type']),
 object({action:{const:'get'},model_id:string},['action','model_id']),
]};
export const imageDefinitions=[
 definition('generation_crop_create',crop),
 definition('generation_translate_create',translate),
 definition('generation_image_resize',resize),
 definition('generation_asset_upload_url',object({source_url:string,confirmed:boolean},['source_url','confirmed'])),
 definition('generation_workspace_create',object({name:string,confirmed:boolean},['name','confirmed'])),
 definition('generation_image_job_query',object({job_id:uuid},['job_id']),'read'),
 definition('generation_workspace_list',object({}),'read'),
 definition('generation_model_query',model,'read'),
 definition('generation_quota',object({}),'read'),
];
export const imageContractBytes=Buffer.from(JSON.stringify(imageDefinitions.map(value=>({result:{structuredContent:value}}))));
export const imageCatalogBytes=Buffer.from(JSON.stringify([{result:{structuredContent:{tools:imageDefinitions.map(({name,operation_class,invoke_via})=>({name,operation_class,invoke_via}))}}}]));
const importItem=object({url:string,resource_id:uuid,already_in_r2:boolean,type:{enum:['image','button']},source:{const:'MCP'},name:string,shared_dept_id:string},['url','resource_id','already_in_r2','type','source','name','shared_dept_id']);
export const publicationDefinitions=[
 definition('material_r2_upload_from_bytes',object({content_type:{enum:['image/png','image/jpeg','image/gif']},ext:{enum:['png','jpg','jpeg','gif']},file_name:string,file_base64:string},['content_type','ext','file_name','file_base64'])),
 definition('material_upload_from_url',object({confirmed:boolean,items:list(importItem)},['confirmed','items']),'write','material_upload_from_url'),
 definition('material_attribution_update',object({material_ids:list(uuid),designer_ids:list(string),creation_type:{enum:['derivative']},confirmed:boolean},['material_ids'])),
 definition('requirement_bind_materials',object({confirmed:boolean,items:list(object({requirement_id:uuid,material_ids:list(uuid)},['requirement_id','material_ids']))},['confirmed','items']),'write','requirement_bind_materials'),
];
