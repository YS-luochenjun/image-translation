export const inject = ['webServer', 'connection', 'tools'];
export async function apply(ctx) {
  const plugin = await import('./route-host.js?feature=monitor-v1');
  return plugin.apply(ctx);
}
