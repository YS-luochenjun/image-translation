import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {once} from 'node:events';
const output=resolve('.review-desk/verification/installed-plugin');
await mkdir(output,{recursive:true});
const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${resolve('.review-desk/plugin-browser-profile')}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try {
 const endpoint=await new Promise((done,fail)=>{let text='';const timer=setTimeout(()=>fail(new Error('Browser startup timeout')),15000);chrome.on('error',fail);chrome.stderr.on('data',chunk=>{text+=chunk;const m=text.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m){clearTimeout(timer);done(m[1]);}});});
 const pages=await(await fetch(new URL('/json/list',endpoint.replace('ws:','http:')))).json();
 socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);await once(socket,'open');
 let serial=0;const pending=new Map();const errors=[];
 socket.onmessage=event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
 const evaluate=async(expression)=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error('Page evaluation failed');return r.result.value;};
 await send('Runtime.enable');await send('Page.enable');await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:'http://127.0.0.1:64626/'});
 let state;
 for(let n=0;n<35;n++){
  await new Promise(r=>setTimeout(r,200));
  state=await evaluate("({title:document.title,hasReview:[...document.querySelectorAll('button')].some(e=>(e.getAttribute('aria-label')||e.title||e.textContent).trim()==='图片审阅'),login:!!document.querySelector('input[type=password]'),bodyLength:document.body?.innerText.length||0})");
  if(state.hasReview||state.login)break;
 }
 if(state.hasReview){await evaluate("[...document.querySelectorAll('button')].find(e=>(e.getAttribute('aria-label')||e.title||e.textContent).trim()==='图片审阅').click()");await new Promise(r=>setTimeout(r,1500));}
 const panel=await evaluate("({iframes:[...document.querySelectorAll('iframe')].map(e=>({title:e.title,src:e.src})),hasProjectNotice:document.body.innerText.includes('当前会话不属于绑定项目'),hasSessionSelect:[...document.querySelectorAll('select')].some(e=>[...e.options].some(o=>o.text.includes('请选择本项目会话'))),hasServiceUnavailable:document.body.innerText.includes('审阅服务未就绪')})");
 const report={state,panel,errors,checkedAt:new Date().toISOString(),note:'Fresh isolated browser, no copied authentication or real feedback/messages submitted.'};
 await writeFile(resolve(output,'report.json'),JSON.stringify(report,null,2));
 await writeFile(resolve(output,'dsh.png'),Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 console.log(JSON.stringify(report,null,2));
}finally{socket?.close();chrome.kill();}
