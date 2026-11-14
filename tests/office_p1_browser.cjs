/* Cross-client acceptance against an owned loopback fixture, never a real account. */
const {chromium} = require('playwright');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
const python = process.env.OFFICE_PYTHON || 'python3';
const output = process.env.OFFICE_OUTPUT;
if (!output) throw new Error('Set OFFICE_OUTPUT to an existing private evidence directory.');
(async () => {
 const fixtureRoot = path.join(output, 'fixture');
 const server = spawn(python, ['examples/office-demo.py', '--output', fixtureRoot], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
 let browser;
 try {
  const launch = await new Promise((resolve, reject) => {
   let buffer = '';
   const timer = setTimeout(() => reject(new Error('Fixture startup timed out.')), 10000);
   server.stdout.on('data', chunk => {buffer += chunk; if (buffer.includes('\n')) {clearTimeout(timer); resolve(JSON.parse(buffer.split('\n')[0]));}});
   server.once('error', error => {clearTimeout(timer); reject(error);});
   server.once('exit', code => {clearTimeout(timer); reject(new Error('Fixture exited: ' + code));});
  });
  const workspace = path.join(fixtureRoot, 'workspace');
  function cli(args, dataDir = workspace) {
   return JSON.parse(execFileSync(python, ['-m', 'pocketchat', 'office', '--data-dir', dataDir, '--json', ...args], {cwd: root, encoding: 'utf8', timeout: 15000})).result;
  }
  const send = args => {
   const preview = cli(args);
   assert.equal(preview.sent, false);
   const done = cli([...args, '--confirm', preview.confirmation]);
   assert.equal(done.status, 'completed');
   // The exact original confirmation recovers its receipt, never a second model call.
   assert.equal(cli([...args, '--confirm', preview.confirmation]).id, done.id);
   return done;
  };
  const c = cli(['conversations', 'create', '--title', 'CLI to browser']);
  const ids = [];
  for (const [name, text] of [['sales.txt', 'Completed 12 customer follow-ups.'], ['next.md', 'Prepare the handover.']]) {
   const file = path.join(output, name); await fs.writeFile(file, text);
   ids.push(cli(['materials', 'add', '--conversation', c.id, '--file', file]).id);
  }
  send(['report', 'create', '--conversation', c.id, '--prompt', 'Create a report.', '--source', ids[0], '--source', ids[1], '--request-id', 'p1-cli-first']);
  const old = cli(['conversations', 'show', '--conversation', c.id]).artifacts[0].id;
  browser = await chromium.launch({headless: true, channel: 'chrome'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(launch.origin + '/#' + launch.token);
  await page.getByRole('button', {name: 'CLI to browser', exact: true}).first().waitFor();
  await page.locator('.artifact').waitFor();
  assert.equal(await page.locator('.source').count(), 2);
  await page.getByRole('button', {name: 'Revise', exact: true}).click();
  await page.locator('#prompt').fill('Shorten it.'); await page.locator('#consent').check(); await page.locator('#send').click();
  await page.waitForFunction(() => document.querySelectorAll('.artifact').length === 2);
  const newer = cli(['conversations', 'show', '--conversation', c.id]).artifacts[0].id;
  for (const [label, version] of [['old', old], ['new', newer]]) {
   for (const format of ['md', 'docx']) cli(['export', '--conversation', c.id, '--version', version, '--format', format, '--out', path.join(output, label + '.' + format)]);
  }
  assert.notEqual(await fs.readFile(path.join(output, 'old.md'), 'utf8'), await fs.readFile(path.join(output, 'new.md'), 'utf8'));
  // Reverse direction: browser-created conversation/report, then CLI revision.
  await page.locator('#new-chat').click();
  await page.locator('#upload').setInputFiles({name: 'browser-notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Completed 12 customer follow-ups.')});
  await page.locator('.source').waitFor();
  await page.locator('#report-toggle').click(); await page.locator('#prompt').fill('Make another report.');
  await page.locator('#consent').check(); await page.locator('#send').click(); await page.locator('.artifact').waitFor();
  const reverse = cli(['conversations', 'list']).items.find(item => item.id !== c.id);
  const firstReverse = cli(['conversations', 'show', '--conversation', reverse.id]).artifacts[0].id;
  send(['report', 'revise', '--conversation', reverse.id, '--version', firstReverse, '--prompt', 'Shorten it.', '--request-id', 'p1-cli-revision']);
  await page.reload(); await page.waitForFunction(() => document.querySelectorAll('.artifact').length === 2);
  const backup = path.join(output, 'backup.json'); cli(['backup', '--out', backup]);
  const restored = path.join(output, 'restored'); cli(['restore', '--from', backup], restored);
  assert.deepEqual(cli(['conversations', 'show', '--conversation', c.id], restored), cli(['conversations', 'show', '--conversation', c.id]));
  await page.screenshot({path: path.join(output, 'p1-desktop.png')});
  assert.deepEqual(errors, []);
  const result = {cli_to_browser_revision: true, browser_to_cli_revision: true, two_materials: true,
   old_and_new_md_docx: true, exact_receipt_recovery: true, live_owner_backup_and_restore: true, page_errors: errors};
  await fs.writeFile(path.join(output, 'p1-browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
 } finally {
  if (browser) await browser.close();
  if (server.exitCode === null) {server.kill('SIGINT'); await Promise.race([once(server, 'exit'), new Promise(resolve => setTimeout(resolve, 5000))]);}
  if (server.exitCode === null) {server.kill('SIGTERM'); await once(server, 'exit');}
 }
})().catch(error => {console.error(error); process.exitCode = 1;});
