import { APP_ID, PREFIX, PROJECT_ID, createBackend, createHandler } from './host-adapter.js?transport=workflow-v3';
import { createWorkflowHandler } from './workflow-http.js?revision=workflow-v5.2.1';
import { installWorkflowCapture } from './workflow-capture.js?revision=workflow-v5.2.1';

export const inject = ['webServer', 'connection', 'tools', 'remoteAccess', 'sessionQuery', 'sessions', 'workingDirectory'];
export function apply(ctx) {
  const backend = createBackend();
  let capture;
  ctx.effect(() => {
    const fallback = createHandler({ connection: ctx.connection, getOrigin: () => ctx.webServer.getOrigin(), backend });
    const unregister = ctx.webServer.register({ kind: 'prefix', path: PREFIX.replace(/\/$/, ''), handler: createWorkflowHandler(ctx, backend, fallback) });
    capture = installWorkflowCapture(ctx, backend);
    void backend.ready().catch(() => {});
    return async () => { capture?.(); unregister(); await backend.dispose(); };
  }, 'session-bound workflow capture and authenticated project route');
  ctx.effect(() => ctx.tools.register({
    name: 'local_image_review',
    description: 'Check the project workflow desk or verify its native DSH interface. Observes session-bound business stages and artifacts; never sends a conversation message automatically.',
    parameters: { type: 'object', properties: { action: { type: 'string', enum: ['status', 'verify_ui'], description: 'status checks project service and workflow capture; verify_ui captures the authenticated workflow panel without sending real session messages.' } }, required: ['action'], additionalProperties: false },
    output: { schema: { type: 'object', additionalProperties: true }, render(_args, value) { return [{ type: 'text', text: JSON.stringify(value) }]; } },
    timeoutMs: 120000,
    async execute(args) {
      if (args.action === 'verify_ui') { const { verifyNativePage } = await import('./native-verify.js?revision=workflow-v5.2.1'); return verifyNativePage(ctx); }
      try { await backend.ready(); return { ready: true, runtime_revision: '5.2.1', app: APP_ID, project_id: PROJECT_ID, api: PREFIX + 'workflow', capture: capture?.status() || null }; }
      catch (error) { return { ready: false, error: error.status ? error.message : 'Workflow service is unavailable' }; }
    },
  }), 'workflow status and UI verification');
}
