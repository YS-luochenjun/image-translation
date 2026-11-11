import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const id='01a1ab5e-81f0-7b33-b7c8-a2d958e79b36',code='SHE-20261105-043';
export async function ordinary043Slot({project,controller,registry,getStatus}){
 let state;
 try{state=JSON.parse(await readFile(resolve(project,`work/fireplay-production/${code}/ordinary-session-journal.json`),'utf8'));}
 catch(error){if(error.code==='ENOENT')return [];throw error;}
 if(state.requirement_id!==id||state.code!==code||typeof state.session_id!=='string')throw Error('External 043 journal identity mismatch');
 const unknown=['dispatching','dispatch_unknown'].includes(state.stage),running=getStatus(state.session_id)==='running';
 if(!unknown&&!running&&state.stage!=='accepted')return [];
 if(!Array.isArray(registry.archivedSessionIds))throw Error('External 043 archive observer unavailable');
 if(registry.archivedSessionIds.includes(state.session_id)){if(unknown||running)throw Error('External 043 archived with active/unknown dispatch');return [];}
 const workspaces=registry.list().filter(row=>resolve(row.path).toLowerCase()===resolve(project).toLowerCase());
 if(workspaces.length!==1||!workspaces[0].sessionIds?.includes(state.session_id))throw Error('External 043 project membership mismatch');
 const p=await controller.projections({sessionId:state.session_id},new AbortController().signal);
 if(p?.kind!=='sequenced'||p.values?.title!==code||!Object.hasOwn(p.values,'subagent')||p.values.subagent!==null)throw Error('External 043 ordinary/title identity mismatch');
 const queues=['next-turn','next-step'].map(key=>p.values.inbox?.[key]);
 if(queues.some(queue=>!Array.isArray(queue)))throw Error('External 043 inbox observer unavailable');
 return unknown||running||queues.some(queue=>queue.length)?[id]:[];
}
