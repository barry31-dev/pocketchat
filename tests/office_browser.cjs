/* Real browser flow against the explicitly selected offline fixture server. */
const {chromium} = require('playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async()=>{
 const browser = await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.OFFICE_URL);
  await page.getByText('offline-fixture',{exact:true}).waitFor();
  await page.locator('#prompt').fill('Hello');await page.locator('#consent').check();await page.locator('#send').click();
  await page.locator('.message.assistant').filter({hasText:'Hello!'}).waitFor();
  await page.locator('#upload').setInputFiles({name:'work-notes.txt',mimeType:'text/plain',buffer:Buffer.from('Completed 12 customer follow-ups. Prepare the handover.')});
  await page.locator('.source').filter({hasText:'work-notes.txt'}).waitFor();
  await page.locator('#report-toggle').click();await page.locator('#prompt').fill('Make a weekly report.');await page.locator('#consent').check();await page.locator('#send').click();
  await page.locator('.artifact').waitFor();
  await page.getByRole('button',{name:'Revise',exact:true}).click();await page.locator('#prompt').fill('Shorten it.');await page.locator('#consent').check();await page.locator('#send').click();
  await page.waitForFunction(()=>document.querySelectorAll('.artifact').length===2);
  const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Word',exact:true}).first().click();const download=await downloadPromise;
  await download.saveAs(process.env.OFFICE_OUTPUT+'/report.docx');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.artifact').length===2);
  assert.equal(await page.locator('.message').count(),6);
  await page.screenshot({path:process.env.OFFICE_OUTPUT+'/desktop.png'});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:process.env.OFFICE_OUTPUT+'/mobile.png',fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  const result={chat:true,source:true,report:true,revision:true,docx_download:true,reload:true,mobile_no_overflow:true,page_errors:errors};
  await fs.writeFile(process.env.OFFICE_OUTPUT+'/browser-result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
