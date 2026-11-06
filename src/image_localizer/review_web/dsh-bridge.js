'use strict';

// Only the explicitly configured local DSH parent can exchange review context.
// A request reads saved feedback; this frame never submits a DSH conversation.
(() => {
  let context = null;
  const desk = () => window.ImageReviewDesk;
  const config = () => desk()?.getHostConfig();
  const embedded = () => window.parent !== window && Boolean(config()?.host_origin);
  function send(message) {
    if (embedded()) window.parent.postMessage({ version: 1, ...message }, config().host_origin);
  }
  function publishTask() {
    if (!embedded()) return;
    const task = desk().getCurrentTask();
    send({ type: 'image-review-task', project_id: config().project_id,
      taskId: task?.id || null, source_sha256: task?.source_sha256 || null,
      output_sha256: task?.output_sha256 || null, manifest_sha256: task?.manifest_sha256 || null });
  }
  async function receive(event) {
    if (!embedded() || event.source !== window.parent || event.origin !== config().host_origin) return;
    const message = event.data;
    if (!message || typeof message !== 'object' || message.version !== 1) return;
    if (message.type === 'image-review-host-context') {
      if (typeof message.sessionId !== 'string' || message.sessionId.length > 200
          || typeof message.projectRoot !== 'string' || message.projectRoot.length > 1000) return;
      context = { sessionId: message.sessionId, projectRoot: message.projectRoot };
      publishTask();
      return;
    }
    if (message.type !== 'image-review-request-revision' || !context
        || typeof message.requestId !== 'string' || message.requestId.length > 100) return;
    const task = desk().getCurrentTask();
    const requestContext = context;
    const requestId = message.requestId;
    try {
      if (desk().hasUnsavedChanges()) throw new Error('请先保存当前审阅意见，或等待任务载入完成。');
      const result = await desk().getRevisionPrompt();
      if (context !== requestContext) throw new Error('关联会话已变化，请重新获取返工指令。');
      if (task?.id !== desk().getCurrentTask()?.id || desk().hasUnsavedChanges()) {
        throw new Error('图片版本或审阅意见已变化，请重新获取返工指令。');
      }
      send({ ...result, type: 'image-review-revision', requestId,
        project_id: config().project_id, sessionId: context.sessionId });
    } catch (error) {
      send({ type: 'image-review-revision-error', requestId, error: error.message });
    }
  }
  window.addEventListener('message', receive);
  window.addEventListener('image-review-task-change', publishTask);
  window.addEventListener('image-review-bridge-ready', publishTask);
})();
