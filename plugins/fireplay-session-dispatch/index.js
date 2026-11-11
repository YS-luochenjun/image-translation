import {admit} from './admission.js?shared-v1';
import {startOrdinary043} from './ordinary-043.js?scoped-admit-v2';
import {createIdentityMigration, identityParameters} from './identity-migration.js?verified-chain-v5';
import {createDispatcher} from './dispatch.js?code-cold-intake-v5';
import {createControl, createDispatchExecutor, controlParameters} from './control.js?identity-migration-readonly-artifacts-043-capacity-v6';
export const inject = ['tools', 'sessionController', 'workspaceRegistry', 'agents'];
export function apply(ctx) {
  const options={controller:ctx.sessionController,registry:ctx.workspaceRegistry,
    getStatus:id => ctx.agents.get(id)?.status ?? null};
  ctx.effect(() => startOrdinary043({...options,allowPrompt:false,admission:admit,onError:error=>console.error('043 ordinary launch:',error.message)}), 'user-authorized ordinary 043 creation and shared admission');
  const control=createControl(options),dispatcher=createDispatcher({...options,beforeDispatch:args=>control.checkColdDispatch(args)});
  const execute=createDispatchExecutor({dispatcher,control});
  ctx.effect(() => ctx.tools.register({
    name:'fireplay_session_dispatch',
    description:'Preview, create, inspect or dispatch one of the fixed 37 Fireplay requirements into an ordinary DSH session. A new original freezes its code title and latest unified control; create requires parent hash-reviewed source and delivery context and never starts an Agent. First dispatch requires the exact reviewed snapshot hash, explicit parent cold_dispatch_ids/cold_snapshot_sha256, current control SHA, fresh ordinary code-title/empty-inbox identity and shared maximum four active/reserved/wake/cold slots. For an already accepted current session, dispatch sends only the separately hash-reviewed unified control document to the reviewed active batch, using queue and a durable once-only control journal. Inactive accepted sessions stay pending; archived sessions are excluded. Unknown dispatch never replays. Acceptance is not execution.',
    parameters:{type:'object',properties:{
      action:{type:'string',enum:['preview','create','dispatch','inspect']},
      requirement_id:{type:'string'}, snapshot_sha256:{type:'string'},
    },required:['action','requirement_id'],additionalProperties:false},
    output:{schema:{type:'object',additionalProperties:true},
      render(_args,value){return [{type:'text',text:JSON.stringify(value)}];}},
    timeoutMs:120000,
    execute:async args => JSON.parse(JSON.stringify(await admit(()=>execute(args)))),
  }), 'reviewed Fireplay requirement session dispatcher');
  ctx.effect(() => ctx.tools.register({
    name:'fireplay_session_control',
    description:'Inspect all 37 fixed-manifest current ordinary requirement sessions without waking Agents or changing journals. Preview the fixed control SHA, explicit wake approval IDs, four-slot reservations and handoff readiness. Steer queues running sessions; only reviewed idle/inactive originals may wake, via stopped/known temporary handoff or explicit original_idle_resume_ids subset with parent identity/publication-journal evidence and fresh ordinary/title/empty-inbox verification. Cold null is never woken. Active, reserved, approved-wake and explicitly approved cold first-dispatch requirements together cannot exceed four slots; unrelated idle legacy journals do not consume slots, while a running identity mismatch fails closed with its requirement code. No arbitrary prompt/session ID; unknown delivery is never replayed.',
    parameters:controlParameters,
    output:{schema:{type:'object',additionalProperties:true},
      render(_args,value){return [{type:'text',text:JSON.stringify(value)}];}},
    timeoutMs:120000,
    execute:async args=>JSON.parse(JSON.stringify({...await admit(()=>control.execute(args)),host_revision:'ordinary043-admit-v2'})),
  }), 'fixed Fireplay current-session control');
  const identity=createIdentityMigration(options);
  ctx.effect(() => ctx.tools.register({
    name:'fireplay_session_identity',
    description:'Review or migrate one fixed-manifest original accepted session title through official rename only. Preview is read-only. Migrate requires a parent-reviewed frozen plan hash and fresh ordinary idle empty-inbox identity plus known publication outcomes. Keeps original journal bytes, accepted prompt and rpcId; never creates, prompts, resumes, unarchives or consumes production slots. Unknown rename is inspect-only and never replayed. Verified migrations are recognized by scoped control only with the complete immutable chain and fresh ordinary code-title identity; migration alone never authorizes control or wake.',
    parameters:identityParameters,
    output:{schema:{type:'object',additionalProperties:true},render(_args,value){return [{type:'text',text:JSON.stringify(value)}];}},
    timeoutMs:120000,
    execute:async args=>JSON.parse(JSON.stringify(await identity.execute(args))),
  }), 'fixed Fireplay original-session identity migration');
}
