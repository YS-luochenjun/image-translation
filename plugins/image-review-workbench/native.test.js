import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { createServer } from 'node:http';
import { apply } from './route-host.js';

test('route registration obeys DSH segment-prefix semantics and reaches health', async () => {
  let route;
  const effects=[];
  const ctx={webServer:{register(value){route=value;return ()=>{};},getOrigin(){return 'http://127.0.0.1:0';}},connection:{admit(){return {peer:{}};}},tools:{register(){return ()=>{};}},effect(callback){effects.push(callback());}};
  apply(ctx);
  assert.equal(route.path,'/local-image-review');
  assert.equal(route.kind,'prefix');
  const requestPath=route.path+'/health';
  assert.ok(requestPath===route.path || requestPath.startsWith(route.path+'/'));
  const server=createServer((req,res)=>{if(req.url===route.path||req.url.startsWith(route.path+'/'))return route.handler(req,res);res.writeHead(404);res.end();});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  ctx.webServer.getOrigin=()=>`http://127.0.0.1:${server.address().port}`;
  try{const r=await fetch(ctx.webServer.getOrigin()+requestPath);const body=await r.json();assert.equal(r.status,200);assert.equal(body.ready,true);assert.equal('token' in body,false);}
  finally{await new Promise(resolve=>server.close(resolve));for(const cleanup of effects.reverse())await cleanup?.();}
});

test('native client preserves project boundaries and requires bound saved revisions', async () => {
 const source=await readFile(new URL('./client.js',import.meta.url),'utf8');let loaded;
 vm.runInNewContext(source,{window:{__ModuleLoader__:{load(value){loaded=value;}}}});
 assert.equal(loaded.id,'@local/image-review-ui');
 const plugin=loaded.factory(()=>({createElement(){}}));const a=plugin.__test;
 assert.ok(a.canTarget({cwd:a.PROJECT,parentId:'ordinary-fork',origin:'fork'}));
 assert.equal(a.canTarget({cwd:a.PROJECT,origin:'subagent'}),false);
 assert.equal(a.canTarget({cwd:a.PROJECT+'-other'}),false);
 assert.equal(a.assetUrl('/api/assets?path=work%2Fone.png'),'/local-image-review/image?path=work%2Fone.png');
 assert.equal(a.assetUrl('https://example.com/file.png'),'');
 assert.equal(a.canTarget({cwd:a.PROJECT.replaceAll('/',String.fromCharCode(92)),origin:'fork'}),true);
 assert.equal(a.workflowNodes({nodes:[]},[{id:'unexecuted'}]).length,0);
 assert.ok(source.includes('ctx.remote.session.prompt'));
 assert.ok(source.includes('sessionId: saved.comment.session_id') || source.includes('sessionId: receipt.comment.session_id') || source.includes('sessionId: draft.session_id') || source.includes('sessionId: current.session_id'));
 assert.ok(!source.includes("h('iframe'"));
});
