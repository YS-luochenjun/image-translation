import { APP_ID, PREFIX, PROJECT_ID, createBackend, createHandler } from './host-adapter.js';

export const inject = ['webServer', 'connection', 'tools'];

export function apply(ctx) {
  const backend = createBackend();
  ctx.effect(() => {
    const unregister = ctx.webServer.register({
      kind: 'prefix', path: PREFIX,
      handler: createHandler({ connection: ctx.connection, getOrigin: () => ctx.webServer.getOrigin(), backend }),
    });
    void backend.ready().catch(() => {});
    return async () => { unregister(); await backend.dispose(); };
  }, 'image review same-origin HTTP adapter');
  ctx.effect(() => ctx.tools.register({
    name: 'local_image_review',
    description: 'Check the image project review service, or capture its native DSH interface in an isolated browser. Returns readiness or verification results and evidence paths.',
    parameters: { type: 'object', properties: { action: { type: 'string', enum: ['status', 'verify_ui'], description: 'status checks the service; verify_ui opens the authenticated current DSH page and captures evidence without saving feedback or sending messages.' } }, required: ['action'], additionalProperties: false },
    output: { schema: { type: 'object', additionalProperties: true }, render(_args, value) { return [{ type: 'text', text: JSON.stringify(value) }]; } },
    timeoutMs: 120000,
    async execute(args) {
      if (args.action === 'verify_ui') {
        const { verifyNativePage } = await import('./native-verify.js');
        return verifyNativePage(ctx);
      }
      if (args.action !== 'status') throw new Error('Unknown review action');
      try {
        await backend.ready();
        return { ready: true, app: APP_ID, project_id: PROJECT_ID, api: PREFIX };
      } catch (error) { return { ready: false, error: error.status ? error.message : 'Local review service is unavailable' }; }
    },
  }), 'image review readiness and native verification');
}
