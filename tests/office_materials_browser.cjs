/* Focused real-app VM races or an owned browser/native-CLI snapshot journey. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');
const {spawn, execFileSync} = require('node:child_process');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
async function races() {
 const source = await fs.readFile(path.join(root,'pocketchat/office_static/app.js'),'utf8');
 const elements = new Map();
 const el = id => {if(!elements.has(id))elements.set(id,{textContent:'',open:false,showModal(){this.open=true;},close(){this.open=false;}});return elements.get(id);};
 const jobs = [];
 const cid='a'.repeat(32), sid='b'.repeat(32);
 const context=vm.createContext({$:el,document:{getElementById:el},api:()=>new Promise((resolve,reject)=>jobs.push({resolve,reject}))});
 const start=source.indexOf('function closeMaterial()'), end=source.indexOf('async function openConversation(cid)');
 assert.ok(start>0 && end>start);
 vm.runInContext('let opening=false;let materialSequence=0;let current={id:"'+cid+'"};'+source.slice(start,end),context);
 vm.runInContext('opening=true',context);
 const switching=vm.runInContext(`viewMaterial('${cid}','${sid}')`,context);
 assert.equal(jobs.length,0,'Switching must reject a fresh old-View click.');
 assert.equal(el('material-preview').open,false);
 await switching;
 vm.runInContext('opening=false',context);
 const first=vm.runInContext(`viewMaterial('${cid}','${sid}')`,context);
 vm.runInContext('current={id:"foreign"};closeMaterial()',context);
 jobs[0].resolve({id:sid,conversation_id:cid,name:'old.md',text:'FOREIGN PRIVATE',sha256:'digest',bytes:1,citation:'S1'});
 await first;assert.equal(el('material-text').textContent,'');assert.equal(el('material-preview').open,false);
 vm.runInContext('current={id:"'+cid+'"}',context);
 const old=vm.runInContext(`viewMaterial('${cid}','${sid}')`,context);
 const latest=vm.runInContext(`viewMaterial('${cid}','${sid}')`,context);
 jobs[2].resolve({id:sid,conversation_id:cid,name:'latest.md',text:'latest',sha256:'digest',bytes:6,citation:'S1'});await latest;
 jobs[1].resolve({id:sid,conversation_id:cid,name:'stale.md',text:'STALE',sha256:'digest',bytes:5,citation:'S1'});await old;
 assert.equal(el('material-text').textContent,'latest');
 const bad=vm.runInContext(`viewMaterial('${cid}','${sid}')`,context);
 jobs[3].resolve({id:'wrong',conversation_id:cid,text:'WRONG PRIVATE'});await bad;
 assert.equal(el('material-text').textContent,'');assert.match(el('material-status').textContent,/identity mismatch/);
 console.log(JSON.stringify({mode:'materials-vm-races',cases:4,passed:true}));
}
async function browser() {
 const {chromium}=require('playwright');
 const python=process.env.OFFICE_PYTHON || 'python3';
 const output=process.env.OFFICE_OUTPUT;
 if(!output)throw new Error('Set OFFICE_OUTPUT to an existing private evidence directory.');
 const fixture=path.join(output,'fixture');
 const server=spawn(python,['examples/office-demo.py','--output',fixture],{cwd:root,stdio:['ignore','pipe','pipe']});
 let browser;
 try {
  const launch=await new Promise((resolve,reject)=>{
   let raw='';const timer=setTimeout(()=>reject(new Error('Fixture startup timeout')),10000);
   server.stdout.on('data',chunk=>{raw+=chunk;if(raw.includes('\n')){clearTimeout(timer);resolve(JSON.parse(raw.split('\n')[0]));}});
   server.once('error',error=>{clearTimeout(timer);reject(error);});
   server.once('exit',()=>{clearTimeout(timer);reject(new Error('Fixture exited'));});
  });
  const cli=args=>JSON.parse(execFileSync(python,['-m','pocketchat','office','--data-dir',path.join(fixture,'workspace'),'--json',...args],{cwd:root,encoding:'utf8',timeout:15000})).result;
  const c=cli(['conversations','create','--title','Material readback']);
  const original='原始资料 <script>window.materialInjected=true</script>\n'+ 'long-original-'.repeat(40);
  const file=path.join(output,'notes.md');await fs.writeFile(file,original);
  const s=cli(['materials','add','--conversation',c.id,'--file',file]);await fs.unlink(file);
  const saved=cli(['materials','show','--conversation',c.id,'--source',s.id]);
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1200,height:900},reducedMotion:'reduce'});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(launch.origin+'/#'+launch.token);
  await page.getByRole('button',{name:'View saved material notes.md'}).waitFor();
  const view=page.getByRole('button',{name:'View saved material notes.md'}).first();
  await view.focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.getElementById('material-text').textContent.length>0);
  assert.equal(await page.locator('#material-text').textContent(),saved.text);
  assert.match(await page.locator('#material-meta').textContent(),new RegExp(saved.sha256));
  assert.equal(await page.evaluate(()=>window.materialInjected===true),false);
  assert.equal(await page.locator('#sources input').first().isChecked(),false);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#material-preview').isVisible(),false);
  await fs.writeFile(file,'Changed copy');cli(['materials','add','--conversation',c.id,'--file',file]);
  cli(['conversations','archive','--conversation',c.id]);cli(['conversations','restore','--conversation',c.id]);
  assert.deepEqual(cli(['materials','show','--conversation',c.id,'--source',s.id]),saved);
  await page.reload();await page.getByRole('button',{name:'View saved material notes.md'}).first().click();
  await page.waitForFunction(()=>document.getElementById('material-text').textContent.length>0);
  assert.equal(await page.locator('#material-text').textContent(),original);
  await page.screenshot({path:path.join(output,'materials-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const dialog=await page.locator('#material-preview').boundingBox();assert.ok(dialog.x>=0 && dialog.x+dialog.width<=390);
  await page.screenshot({path:path.join(output,'materials-mobile.png')});
  assert.deepEqual(errors,[]);
  const result={same_cli_browser_snapshot:true,old_copy_retained:true,keyboard_close:true,source_not_selected:true,text_not_html:true,mobile_no_overflow:true,page_errors:errors};
  await fs.writeFile(path.join(output,'materials-browser-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 } finally {
  if(browser)await browser.close();
  if(server.exitCode===null){server.kill('SIGINT');await Promise.race([once(server,'exit'),new Promise(resolve=>setTimeout(resolve,5000))]);}
  if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}
  const requests=JSON.parse(await fs.readFile(path.join(fixture,'provider-requests.json'),'utf8'));
  assert.deepEqual(requests,[],'Readback must not call a provider.');
 }
}
(process.env.OFFICE_MATERIAL_RACE_ONLY==='1'?races():browser()).catch(error=>{console.error(error);process.exitCode=1;});
