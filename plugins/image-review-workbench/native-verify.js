import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
const PROJECT = 'C:/Users/Echo/Desktop/单图片翻译';
const NODE = 'C:/Users/Echo/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe';
export function verifyNativePage(ctx) {
  const target = ctx.connection.authenticatedUrl(ctx.webServer.getOrigin() + '/');
  return new Promise((resolveCheck, reject) => {
    const child = spawn(NODE, [resolve(PROJECT, 'scripts/verify_native_review.mjs')], {
      cwd: PROJECT, windowsHide: true,
      env: { ...process.env, DSH_REVIEW_CHECK_URL: target },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('界面检查超时；没有提交任何反馈或会话消息。')); }, 95000);
    child.stdout.on('data', value => { if (stdout.length < 32000) stdout += value; });
    child.stderr.on('data', value => { if (stderr.length < 4000) stderr += value; });
    child.once('error', () => { clearTimeout(timer); reject(new Error('无法启动隔离界面检查。')); });
    child.once('exit', code => {
      clearTimeout(timer);
      try {
        const result = JSON.parse(stdout.trim());
        resolveCheck({ ...result, exitCode: code });
      } catch { reject(new Error('界面检查未返回报告。' + (code === 0 ? '' : '请检查已保存的验收结果。'))); }
    });
  });
}
