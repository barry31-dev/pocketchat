'use strict';
// Real browser and native CLI on an owned loopback fixture; never paid providers.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const {spawn, execFileSync} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.OFFICE_PYTHON || 'python3';
const output = process.env.OFFICE_OUTPUT;
async function main() {
 assert.ok(output, 'Set OFFICE_OUTPUT to a private evidence directory.');
 const fixture = path.join(output, 'fixture');
 const server = spawn(python, ['examples/office-demo.py', '--output', fixture], {cwd:root, stdio:['ignore','pipe','pipe']});
 let browser, stderr = '';
 server.stderr.on('data', chunk => {stderr += chunk;});
 try {
  const launch = await new Promise((resolve,reject) => {
   let data = ''; const timer = setTimeout(() => reject(new Error('Fixture startup timeout: '+stderr)), 10000);
   server.stdout.on('data', chunk => {data += chunk; if(data.includes('\n')){clearTimeout(timer);resolve(JSON.parse(data.split('\n')[0]));}});
   server.once('error', reject); server.once('exit', code => {clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});
  });
  const workspace = path.join(fixture, 'workspace');
  const cli = args => JSON.parse(execFileSync(python, ['-m','pocketchat','office','--data-dir',workspace,'--json',...args], {cwd:root,encoding:'utf8',timeout:15000})).result;
  browser = await chromium.launch({headless:true,channel:'chrome'});
  const page = await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const errors = [];page.on('pageerror', e => errors.push(e.message));
  await page.goto(launch.origin+'/#'+launch.token);
  await page.locator('#prompt').waitFor({state:'visible'});
  await page.waitForFunction(()=>!document.getElementById('send').disabled);
  assert.equal(await page.locator('#project-instructions').count(), 1, 'OF01: missing browser project-instruction control');
  await page.locator('#project-settings').evaluate(e=>{e.open=true;});
  await page.locator('#project-instructions').fill('Use concise project notes.');
  await page.locator('#save-instructions').click();
  await page.waitForFunction(()=>document.getElementById('foundation-status').textContent.includes('saved'));
  let project = cli(['project','show']);assert.equal(project.instructions,'Use concise project notes.');
  project = cli(['project','instructions','--project-id',project.project_id,'--revision',String(project.instruction_revision),'--text','CLI project guidance.']);
  await page.locator('#refresh-project').click();
  await page.waitForFunction(()=>document.getElementById('project-instructions').value==='CLI project guidance.');
  await page.locator('#memory-enabled').check();await page.locator('#save-memory-preference').click();
  await page.waitForFunction(()=>document.getElementById('foundation-status').textContent.includes('saved'));
  assert.equal(cli(['project','show']).memory_enabled,true);
  await page.locator('#memory-content').fill('Prefer short weekly project notes.');
  await page.locator('#memory-guidance').check();await page.locator('#propose-memory').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-memory-id]').length===1);
  let view=cli(['memory','list']);assert.equal(view.records[0].admission_status,'candidate');
  const id=view.records[0].memory_id;
  page.once('dialog', dialog=>dialog.accept());
  await page.locator('[data-memory-id="'+id+'"] button').filter({hasText:'Accept'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-memory-id]').textContent.includes('accepted'));
  view=cli(['memory','list']);assert.equal(view.records[0].admission_status,'accepted');
  await page.locator('#prompt').fill('Help with weekly project notes.');
  await page.locator('#preview-request').click();
  await page.locator('#context-preview').waitFor({state:'visible'});
  const preview = JSON.parse(await page.locator('#context-payload').textContent());
  const manifest=JSON.parse(await page.locator('#context-manifest').textContent());
  assert.ok(JSON.stringify(preview).includes('CLI project guidance.'));
  assert.ok(JSON.stringify(preview).includes('Prefer short weekly project notes.'));
  assert.equal(manifest.project_id,project.project_id);
  await page.locator('#close-context-preview').click();
  // A second client changes state after preview; Send must NOT silently re-preview.
  project=cli(['project','show']);
  cli(['project','instructions','--project-id',project.project_id,'--revision',String(project.instruction_revision),'--text','Changed after browser preview.']);
  await page.locator('#consent').check();await page.locator('#send').click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('changed'));
  assert.equal(cli(['conversations','list']).items.length,1);
  let c=cli(['conversations','show','--conversation',cli(['conversations','list']).items[0].id]);
  assert.equal(c.runs.length,0,'OF03: stale preview must have zero admitted requests');
  await page.locator('#preview-request').click();await page.locator('#context-preview').waitFor({state:'visible'});
  const finalPayload=JSON.parse(await page.locator('#context-payload').textContent());
  await page.locator('#close-context-preview').click();await page.locator('#consent').check();await page.locator('#send').click();
  await page.waitForFunction(()=>document.querySelectorAll('.message.assistant').length===1 && !document.getElementById('send').disabled);
  c=cli(['conversations','show','--conversation',c.id]);assert.equal(c.runs.length,1);
  assert.equal(c.runs[0].status,'completed');
  // Explicit summary keeps the original messages; memory forget acknowledges historical copies.
  await page.locator('#load-summary').click();
  await page.waitForFunction(()=>document.querySelectorAll('#summary-originals input').length===2);
  await page.locator('#summary-originals input').first().check();
  await page.locator('#summary-content').fill('Keep concise notes; next step is a weekly draft.');
  await page.locator('#summary-accept').check();await page.locator('#save-summary').click();
  await page.waitForFunction(()=>document.getElementById('foundation-status').textContent.includes('Summary saved'));
  assert.equal(cli(['summary','show','--conversation',c.id]).summary.revision,1);
  assert.equal(cli(['conversations','show','--conversation',c.id]).messages.length,2);
  page.once('dialog', dialog=>dialog.accept());
  await page.locator('[data-memory-id="'+id+'"] button').filter({hasText:'Forget'}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-memory-id]').length===0);
  assert.equal(cli(['memory','list']).records.length,0);
  await page.screenshot({path:path.join(output,'foundations-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#project-instructions').count(),1);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Controls must not cause mobile horizontal overflow');
  await page.screenshot({path:path.join(output,'foundations-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'expected-payload.json'),JSON.stringify(finalPayload,null,2));
  const result={project_web_cli:true,explicit_candidate_acceptance:true,stale_preview_zero_runs:true,summary_originals_preserved:true,forget_explicit:true,mobile_no_overflow:true,page_errors:errors};
  await fs.writeFile(path.join(output,'browser-result.json'),JSON.stringify(result,null,2));
 } finally {
  if(browser)await browser.close();
  if(server.exitCode===null){server.kill('SIGINT');await Promise.race([once(server,'exit'),new Promise(resolve=>setTimeout(resolve,5000))]);}
  if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}
 }
 const actual=JSON.parse(await fs.readFile(path.join(fixture,'provider-requests.json'),'utf8'));
 assert.equal(actual.length,1,'Preview and stale sends must not call the provider.');
 assert.deepEqual(actual[0],JSON.parse(await fs.readFile(path.join(output,'expected-payload.json'),'utf8')),'OF03: displayed payload must equal actual HTTP JSON');
 console.log(JSON.stringify({browser_cli_passed:true,actual_http_payload_equal:true,provider_calls:actual.length}));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
