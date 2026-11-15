/* Cross-client acceptance against an owned loopback fixture, never a real account. */
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {once} = require('node:events');
const root = path.resolve(__dirname, '..');
const python = process.env.OFFICE_PYTHON || 'python3';
const output = process.env.OFFICE_OUTPUT;
// Fast state-machine regressions: real app handlers, controlled asynchronous I/O.
// No browser, provider, Python process, filesystem fixtures or evidence files.
async function raceAcceptance() {
 const vm = require('node:vm');
 const {createHash, webcrypto} = require('node:crypto');
 const appPath = path.join(root, 'pocketchat/office_static/app.js');
 // Optional stdin source supports private historical snapshots without changing the worktree.
 const source = process.env.OFFICE_RACE_SOURCE_STDIN === '1'
  ? require('node:fs').readFileSync(0, 'utf8') : await fs.readFile(appPath, 'utf8');
 const startup = source.indexOf('(async()=>{try{const token=location.hash');
 assert.ok(startup > 0, 'Find the app startup boundary; never execute its network bootstrap implicitly.');
 console.log(JSON.stringify({mode: 'office-node-vm-races', node: process.version,
  app_sha256: createHash('sha256').update(source).digest('hex')}));
 const A = 'a'.repeat(32), B = 'b'.repeat(32), C = 'c'.repeat(32), SID = 'd'.repeat(32), RID = 'e'.repeat(32);
 const workspace = 'fixture-workspace';
 const legacyKey = 'pocketchat.pending-request.v1';
 const pendingKey = 'pocketchat.pending-request.v2:' + workspace;
 const clone = value => JSON.parse(JSON.stringify(value));
 const conversation = id => ({id, title: id, archived: false, sources: [], messages: [], artifacts: [], runs: []});
 const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
 const settle = async () => {for (let i = 0; i < 20; i++) await Promise.resolve();};
 class Element {
  constructor(tag = 'div') {
   this.tagName = tag; this.children = []; this.value = ''; this.checked = false;
   this.disabled = false; this.hidden = false; this.dataset = {}; this.className = '';
   this.files = []; this.attributes = {}; this.parent = null; this._text = '';
   this.classList = {add: name => {this.className += ' ' + name;}, remove: name => {
    this.className = this.className.split(' ').filter(x => x !== name).join(' ');
   }};
  }
  set textContent(value) {this._text = String(value); this.children = [];}
  get textContent() {return this._text + this.children.map(c => typeof c === 'string' ? c : c.textContent).join('');}
  append(...children) {for (const c of children) {if (typeof c !== 'string') c.parent = this; this.children.push(c);}}
  prepend(...children) {for (const c of children) if (typeof c !== 'string') c.parent = this; this.children.unshift(...children);}
  replaceChildren(...children) {this._text = ''; this.children = []; this.append(...children);}
  setAttribute(key, value) {this.attributes[key] = value;}
  focus() {} scrollIntoView() {} close() {} showModal() {}
  remove() {if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this);}
  querySelectorAll(selector) {
   const matches = [];
   const visit = el => {for (const c of el.children) if (typeof c !== 'string') {
    if (selector === c.tagName || (selector[0] === '.' && c.className.split(' ').includes(selector.slice(1)))) matches.push(c);
    visit(c);
   }};
   visit(this); return matches;
  }
  querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
 }
 function harness() {
  const elements = new Map(), storage = new Map(), calls = [], timers = new Map(), absent = new Set();
  let timer = 0, confirmations = 0, confirmResult = false;
  const el = id => {if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);};
  const shortcut = new Element('button'); shortcut.dataset.prompt = 'Unreviewed shortcut';
  const h = {el, storage, calls, shortcut, route: () => {throw new Error('Unexpected fixture request.');}};
  const context = vm.createContext({console, TextDecoder, URL, crypto: webcrypto,
   document: {getElementById: id => absent.has(id) ? null : el(id), createElement: tag => new Element(tag),
    querySelectorAll: selector => selector === '[data-prompt]' && !absent.has('weekly') ? [shortcut] : []},
   window: {addEventListener() {}, prompt: () => null, confirm: () => {confirmations++; return confirmResult;}},
   navigator: {clipboard: {writeText: async () => {}}},
   sessionStorage: {getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, String(value)), removeItem: key => storage.delete(key)},
   setTimeout: fn => {timers.set(++timer, fn); return timer;}, clearTimeout: id => timers.delete(id),
   bridge: {request: async (url, body) => {
    const call = {method: body === undefined ? 'GET' : 'POST', path: url, body: body === undefined ? null : clone(body)};
    calls.push(call); return h.route(call);
   }}, fetch: () => {throw new Error('The VM must not access a network.');}});
  vm.runInContext(source.slice(0, startup), context, {filename: appPath});
  h.run = code => vm.runInContext(code, context);
  h.read = code => clone(h.run(code));
  h.run('api=(url,body)=>bridge.request(url,body)')
  el('model').textContent='fixture';el('provider').textContent='https://provider.invalid';;
  h.init = c => {
   context.fixture = c;
   h.run(`current=fixture;selected=new Set();report=false;revision=null;activeRun=null;lastSubmission=null;opening=false;
    draftPrefix='pocketchat.draft.v1:${workspace}:browser:';viewKey=draftPrefix+'current';receiptKey='${pendingKey}';
    draftReady=true;restoreDraft();if(current)renderConversation(current);busy(false);`);
  };
  // Explicitly model detached welcome children; other tests retain their controlled DOM.
  h.removeWelcome = () => absent.add('weekly');
  h.edit = text => {el('prompt').value = text; h.run('changedDraft()');};
  h.draft = id => JSON.parse(storage.get('pocketchat.draft.v1:' + workspace + ':browser:' + id) || 'null');
  h.confirm = value => {confirmResult = value;};
  h.confirmations = () => confirmations;
  h.noPosts = () => assert.deepEqual(calls.filter(c => c.method !== 'GET'), [], 'Legacy recovery must use GET only.');
  return h;
 }
 const tests = [];
 const test = (name, action) => tests.push({name, action});
 test('branch selection saves an isolated unsent draft and excludes later history', async () => {
  const h=harness(), a=conversation(A);
  a.active_run_ids=[RID];a.chat_run_ids=[RID];
  a.messages=[{id:'1',role:'user',content:'Original question',run_id:RID},{id:'2',role:'assistant',content:'Original reply',run_id:RID}];
  h.init(a);
  h.route=call=>{assert.equal(call.path,'/api/runs/'+RID);return {id:RID,conversation_id:A,status:'completed',request:{intent:'chat',wire:{prompt:'Original question',intent:'chat',source_ids:[],revision_id:null}}};};
  await h.run(`startBranch('${RID}')`);
  assert.equal(h.el('prompt').value,'Original question');
  assert.equal(h.el('consent').checked,false);
  assert.equal(h.draft(A).branch_from,RID);
  h.run('restoreDraft()');assert.equal(h.read('branchFrom'),RID);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  h.run("$('leave-branch').onclick()");assert.equal(h.read('branchFrom'),null);
 });
 test('branch send discloses actual prefix before fresh consent and dispatch', async () => {
  const h=harness(), a=conversation(A);a.chat_run_ids=[RID];h.init(a);
  h.run(`setBranch('${RID}')`);h.edit('Regenerate older turn');
  h.route=call=>{if(call.path.endsWith('/preview'))return {confirmation:'f'.repeat(64),model:'fixture',provider:'https://provider.invalid',history:[{role:'user',content:'Old private prefix'}],message:'Regenerate older turn',source_ids:[]};throw new Error('Must not dispatch without reviewed prefix');};
  await h.el('composer').onsubmit({preventDefault(){}});
  assert.equal(h.el('branch-preview').hidden,false);
  assert.match(h.el('branch-history').textContent,/Old private prefix/);
  assert.equal(h.el('consent').checked,false);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirmed')).length,0);
  h.edit('Changed draft');assert.equal(h.el('branch-preview').hidden,true);
 });
 test('removed welcome controls do not crash conversation rendering or busy transitions', async () => {
  const h = harness(), a = conversation(A);
  a.messages = [{id: '1', role: 'user', content: 'Existing conversation', run_id: RID}];
  h.init(a); h.removeWelcome();
  assert.equal(h.run("$('weekly')"), null, 'Detached welcome IDs must not be synthesized by the DOM double.');
  assert.equal(h.run("document.querySelectorAll('[data-prompt]').length"), 0);
  assert.doesNotThrow(() => h.run('renderConversation(current)'), 'Opening existing messages removes welcome controls.');
  h.run('busy(true)'); assert.equal(h.el('consent').disabled, true);
  h.run('busy(false)'); assert.equal(h.el('consent').disabled, false);
  h.run('current.archived=true;busy(false)'); assert.equal(h.el('send').disabled, true);
 });
 test('upload delay cannot move or overwrite another conversation draft; shortcuts are guarded', async () => {
  const h = harness(), a = conversation(A), b = conversation(B), file = deferred();
  a.artifacts = [{id: 'f'.repeat(32), run_id: RID, created: 1}];
  h.init(a); h.edit('A private draft');
  h.route = call => {
   if (call.path.endsWith('/sources')) {a.sources = [{id: SID, name: 'a.txt', bytes: 1, sha256: '1'.repeat(64)}]; return {id: SID};}
   if (call.path === '/api/conversations/' + A) return a;
   if (call.path === '/api/conversations/' + B) return b;
   throw new Error(call.path);
  };
  h.el('upload').files = [{size: 1, name: 'a.txt', arrayBuffer: () => file.promise}];
  const uploading = h.run("$('upload').onchange()"); await settle();
  h.run("$('weekly').onclick()"); h.shortcut.onclick(); h.run("$('report-toggle').onclick()");
  const revise = h.el('artifacts').querySelectorAll('button').find(b => b.textContent === 'Revise');
  assert.ok(revise, 'Exercise the real report revision shortcut.'); await revise.onclick();
  assert.deepEqual(h.read('({report,revision})'), {report: false, revision: null}, 'Revise must not change a pending upload draft.');
  assert.equal(h.el('prompt').value, 'A private draft', 'Mutation must guard all prompt shortcuts.');
  await h.run(`openConversation('${B}').catch(()=>{})`);
  const before = h.read('current.id');
  if (before === B) h.edit('B private draft');
  const prompt = h.el('prompt').value;
  file.resolve(new Uint8Array([65]).buffer); await uploading;
  assert.equal(h.read('current.id'), before, 'An upload response must not steal the current view.');
  assert.equal(h.el('prompt').value, prompt);
  assert.equal(h.draft(A).prompt, 'A private draft');
  assert.ok(!h.draft(B)?.sources.includes(SID), 'A source must not enter B draft.');
  await h.run(`openConversation('${B}')`);
  assert.equal(h.draft(A).prompt, 'A private draft', 'Subsequent navigation must not overwrite A.');
 });
 test('archive delay cannot replace the current view or its draft', async () => {
  const h = harness(), a = conversation(A), b = conversation(B), response = deferred();
  h.init(a); h.edit('A original');
  h.route = call => {
   if (call.path.endsWith('/archive')) return response.promise;
   if (call.path === '/api/conversations/' + B) return b;
   if (call.path.startsWith('/api/conversations')) return {items: []};
   throw new Error(call.path);
  };
  const archiving = h.run("$('archive').onclick()"); await settle();
  await h.run(`openConversation('${B}').catch(()=>{})`);
  const before = h.read('current.id'); if (before === B) h.edit('B retained');
  const prompt = h.el('prompt').value;
  response.resolve({...a, archived: true}); await archiving;
  assert.equal(h.read('current.id'), before, 'Archive response must not switch conversations.');
  assert.equal(h.el('prompt').value, prompt);
  assert.equal(h.draft(A).prompt, 'A original');
 });
 for (const change of ['edit', 'new-chat', 'shortcut']) test('create/submit delay preserves confirmed content: ' + change, async () => {
  const h = harness(), response = deferred(), original = 'Reviewed public prompt';
  h.init(null); h.edit(original); h.el('consent').checked = true;
  h.route = call => {
   if (call.path === '/api/conversations' && call.method === 'POST') return response.promise;
   if (call.path === '/api/conversations') return {items: []};
   if (call.path.endsWith('/preview')) return {model:'fixture',provider:'https://provider.invalid',confirmation:'f'.repeat(64)};
   if (call.path.endsWith('/confirmed')) {const e = new Error('Captured send; no provider invoked.'); e.status = 400; throw e;}
   throw new Error(call.path);
  };
  const sending = h.run("$('composer').onsubmit({preventDefault(){}})"); await settle();
  if (change === 'edit') h.edit('Unreviewed private replacement');
  if (change === 'new-chat') h.run("$('new-chat').onclick()");
  if (change === 'shortcut') {h.run("$('weekly').onclick()"); h.shortcut.onclick(); h.run("$('report-toggle').onclick()");}
  response.resolve(conversation(C)); await sending;
  const sends = h.calls.filter(c => c.path.endsWith('/confirmed') && c.method === 'POST');
  assert.ok(sends.length <= 1, 'A create must not admit multiple sends.');
  for (const send of sends) assert.deepEqual(send.body.message, {prompt: original, intent: 'chat', source_ids: [],
   revision_id: null, request_id: send.body.message.request_id, consent: true}, 'Never send replaced or unconfirmed content.');
  if (change !== 'edit') assert.equal(sends.length, 1, 'A guarded shortcut must not lose the confirmed submission.');
 });
 test('definite unaccepted confirmation revokes old consent without losing draft', async () => {
  const h=harness(),a=conversation(A);h.init(a);h.edit('Retained draft');h.el('consent').checked=true;
  h.run("lastSubmission={cid:current.id,body:{prompt:'Retained draft',intent:'chat',source_ids:[],revision_id:null,request_id:'unaccepted-request',consent:true},confirmation:'a'.repeat(64)}");
  h.route=call=>{const e=new Error('Preview expired');e.status=409;e.requestNotAccepted=true;throw e;};
  await h.run('dispatchPending()');
  assert.equal(h.read('lastSubmission'),null);assert.equal(h.el('consent').checked,false);assert.equal(h.el('prompt').value,'Retained draft');
 });
 test('draft edit during preview never sends changed or previously confirmed content', async () => {
  const h=harness(),a=conversation(A),response=deferred();h.init(a);
  h.edit('Confirmed text');h.el('consent').checked=true;
  h.route=call=>{
   if(call.path.endsWith('/preview'))return response.promise;
   if(call.path.endsWith('/confirmed'))throw new Error('Must not send');
   throw new Error(call.path);
  };
  const action=h.run("$('composer').onsubmit({preventDefault(){}})");await settle();
  h.edit('Replacement text');response.resolve({model:'fixture',provider:'https://provider.invalid',confirmation:'a'.repeat(64)});await action;
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirmed')).length,0);
  assert.equal(h.el('prompt').value,'Replacement text');assert.equal(h.el('consent').checked,false);
 });
 test('same model endpoint profile change cannot reuse browser consent', async () => {
  const h=harness(),a=conversation(A);h.init(a);h.el('model-choice').value='first';
  h.edit('Reviewed draft');h.el('consent').checked=true;
  h.route=call=>{
   if(call.path.endsWith('/preview'))return {profile:'second',model:'fixture',provider:'https://provider.invalid',confirmation:'a'.repeat(64)};
   if(call.path==='/api/models')return {selected:'second',items:[{profile:'second',model:'fixture',provider:'https://provider.invalid'}]};
   if(call.path.endsWith('/confirmed')){const e=new Error('Unexpected send');e.status=400;throw e;}
   throw new Error(call.path);
  };
  await h.run("$('composer').onsubmit({preventDefault(){}})");
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirmed')).length,0);
  assert.equal(h.el('consent').checked,false);assert.equal(h.el('prompt').value,'Reviewed draft');
 });
 test('completed run keeps navigation locked until its conversation refresh finishes', async () => {
  const h = harness(), a = conversation(A), b = conversation(B), response = deferred();
  h.init(a); h.edit('A retained draft');
  h.route = call => {
   if (call.path === '/api/runs/' + RID) return {id: RID, conversation_id: A, status: 'completed', preview: '', request: {intent: 'chat'}};
   if (call.path === '/api/conversations/' + A) return response.promise;
   if (call.path === '/api/conversations/' + B) return b;
   if (call.path === '/api/conversations') return {items: [a, b]};
   throw new Error(call.path);
  };
  h.run(`watch('${RID}')`); await settle();
  await h.run(`openConversation('${B}').catch(()=>{})`);
  assert.equal(h.read('current.id'), A, 'Do not release the run lock before the final refresh returns.');
  response.resolve(a); await settle();
  assert.equal(h.read('activeRun'), null); assert.equal(h.el('prompt').value, 'A retained draft');
  await h.run(`openConversation('${B}')`); assert.equal(h.read('current.id'), B);
 });
 const saved = {cid: A, body: {prompt: 'Legacy reviewed', intent: 'chat', source_ids: [SID], revision_id: null,
  request_id: 'legacy-request-001', consent: true}};
 const wire = {prompt: saved.body.prompt, intent: 'chat', source_ids: [SID], revision_id: null};
 function legacyHarness(kind = 'match') {
  const h = harness(), a = conversation(A);
  a.runs = kind === 'missing' ? [] : [{id: RID, status: 'running'}];
  const status = {id: RID, conversation_id: kind === 'cid' ? B : A,
   request_id: kind === 'request-id' ? 'another-request' : saved.body.request_id,
   status: 'running', preview: '', request: {wire: {...wire, prompt: kind === 'wire' ? 'Different content' : wire.prompt}}};
  h.storage.set(legacyKey, JSON.stringify(saved)); h.storage.set('unrelated-tab-record', 'keep');
  h.route = call => {
   if(call.path === '/api/models') return {selected:'fixture',items:[{profile:'fixture',model:'fixture',provider:'https://provider.invalid'}]};
   if (call.path === '/api/bootstrap') return {csrf: 'fixture-csrf', workspace_id: workspace,
    database_version: 3, model: 'fixture', provider: 'https://provider.invalid'};
   if (call.path === '/api/conversations') return {items: [a]};
   if (call.path === '/api/conversations/' + A) return a;
   if (call.path === '/api/runs/' + RID) return status;
   throw new Error(call.path);
  };
  return h;
 }
 const recovery = h => h.el('notice').querySelectorAll('button').find(b => /(?:check|recover).*receipt/i.test(b.textContent));
 test('legacy bootstrap explicitly blocks sending, does not POST or auto-migrate', async () => {
  const h = legacyHarness(); await h.run('boot()');
  assert.equal(h.el('send').disabled, true, 'Unbound v1 pending must block sending.');
  assert.match(h.el('notice').textContent, /(?:older|old|legacy|unbound)/i);
  assert.ok(recovery(h), 'Expose a user-invoked verifiable receipt check.');
  h.edit('Another private draft'); h.el('consent').checked = true;
  await h.run("$('composer').onsubmit({preventDefault(){}})");
  h.noPosts(); assert.ok(h.storage.has(legacyKey)); assert.ok(!h.storage.has(pendingKey));
 });
 test('legacy explicit receipt check matches ID/cid/wire, migrates only then, watches with GET', async () => {
  const h = legacyHarness(); await h.run('boot()');
  const button = recovery(h); assert.ok(button, 'Receipt recovery entry point is missing.');
  await button.onclick(); await settle(); h.noPosts();
  assert.ok(h.calls.some(c => c.path === '/api/conversations/' + A));
  assert.ok(h.calls.filter(c => c.path === '/api/runs/' + RID).length >= 2, 'Verify receipt then watch its status.');
  assert.deepEqual(JSON.parse(h.storage.get(pendingKey)), saved);
  assert.ok(!h.storage.has(legacyKey)); assert.equal(h.read('activeRun'), RID);
 });
 for (const kind of ['missing', 'request-id', 'cid', 'wire']) test('legacy ' + kind + ' mismatch stays blocked; discard is confirmed and tab-only', async () => {
  const h = legacyHarness(kind); await h.run('boot()');
  const button = recovery(h); assert.ok(button, 'Receipt recovery entry point is missing.');
  await button.onclick(); await settle(); h.noPosts();
  assert.equal(h.el('send').disabled, true); assert.ok(h.storage.has(legacyKey)); assert.ok(!h.storage.has(pendingKey));
  const discard = h.el('notice').querySelectorAll('button').find(b => /discard|abandon/i.test(b.textContent));
  assert.ok(discard, 'Provide an explicit confirmed discard for an unverifiable record.');
  h.confirm(false); await discard.onclick(); assert.ok(h.storage.has(legacyKey));
  h.confirm(true); await discard.onclick(); assert.ok(!h.storage.has(legacyKey));
  assert.equal(h.confirmations(), 2); assert.equal(h.storage.get('unrelated-tab-record'), 'keep'); h.noPosts();
 });
 const filter = process.env.OFFICE_RACE_FILTER || '';
 const selectedTests = tests.filter(({name}) => name.includes(filter));
 assert.ok(selectedTests.length, 'OFFICE_RACE_FILTER must select at least one test.');
 let failed = 0;
 for (const {name, action} of selectedTests) {
  try {await action(); console.log('PASS ' + name);}
  catch (error) {failed++; console.error('FAIL ' + name + '\n' + error.stack);}
 }
 console.log(JSON.stringify({tests: selectedTests.length, passed: selectedTests.length - failed, failed, filter}));
 if (failed) process.exitCode = 1;
}

async function browserAcceptance() {
 const {chromium} = require('playwright');
 if (!output) throw new Error('Set OFFICE_OUTPUT to an existing private evidence directory.');
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
  // Existing-profile choices are shared, explicit, and never run a probe model.
  const choices = cli(['models']);
  assert.equal(choices.items.length, 2);
  await page.locator('#model-choice').selectOption('alternate');
  await page.waitForFunction(() => document.querySelector('#model').textContent === 'offline-alternate');
  assert.equal(cli(['models']).selected, 'alternate');
  assert.equal(await page.locator('#consent').isChecked(), false);
  await page.locator('#prompt').fill('Not sent after remote model change');
  await page.locator('#consent').check();
  cli(['models', '--select', 'fixture']);
  await page.locator('#send').click();
  await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Model changed in another client'));
  assert.equal(await page.locator('#consent').isChecked(), false);
  assert.equal(await page.locator('#prompt').inputValue(), 'Not sent after remote model change');
  await page.reload(); await page.locator('.artifact').waitFor();
  assert.equal(await page.locator('#model-choice').inputValue(), 'fixture');

  // Unsent browser drafts survive a switch and refresh without inheriting consent.
  await page.locator('#prompt').fill('Unsent draft for CLI conversation');
  await page.locator('#consent').check();
  await page.locator('#new-chat').click();
  assert.equal(await page.locator('#prompt').inputValue(), '');
  await page.locator('#prompt').fill('Separate new conversation draft');
  await page.getByRole('button', {name: 'CLI to browser', exact: true}).first().click();
  await page.waitForFunction(() => document.querySelector('#rename').textContent === 'CLI to browser');
  assert.equal(await page.locator('#prompt').inputValue(), 'Unsent draft for CLI conversation');
  assert.equal(await page.locator('#consent').isChecked(), false);
  await page.reload();
  await page.locator('.artifact').waitFor();
  assert.equal(await page.locator('#prompt').inputValue(), 'Unsent draft for CLI conversation');
  assert.equal(await page.locator('#consent').isChecked(), false);
  await page.locator('#search').fill('budget');
  const hit = page.locator('#search-results button').first(); await hit.waitFor(); await hit.click();
  await page.waitForFunction(() => document.activeElement?.classList.contains('search-hit'));
  assert.match(await page.locator('.search-hit').innerText(), /budget/);
  await page.locator('#search').fill('');
  await page.locator('#archive').click();
  await page.waitForFunction(() => document.querySelector('#archive').textContent === 'Restore');
  assert.equal(await page.locator('#send').isDisabled(), true);
  assert.equal(cli(['conversations', 'list']).items.length, 0);
  assert.equal(cli(['conversations', 'list', '--archived']).items[0].id, c.id);
  await page.locator('#archive').click();
  await page.waitForFunction(() => document.querySelector('#archive').textContent === 'Archive');
  assert.equal(await page.locator('#prompt').inputValue(), 'Unsent draft for CLI conversation');
  assert.equal(cli(['search', '--query', 'budget']).items[0].conversation_id, c.id);

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
  // Branching preserves original messages and excludes their later tail from new context.
  const branching=cli(['conversations','create','--title','Branch lab']);
  const original=send(['chat','--conversation',branching.id,'--prompt','Original planning question','--request-id','branch-original']);
  send(['chat','--conversation',branching.id,'--prompt','Later confidential detail','--request-id','branch-later']);
  const savedMessages=cli(['conversations','show','--conversation',branching.id]).messages;
  const staleArgs=['chat','--conversation',branching.id,'--prompt','Stale edit','--branch-from',original.id,'--request-id','branch-stale'];
  const stalePreview=cli(staleArgs);
  assert.deepEqual(stalePreview.history,[]);
  await page.reload();await page.getByRole('button',{name:'Branch lab',exact:true}).first().click();
  await page.getByRole('button',{name:'Edit question',exact:true}).first().click();
  await page.waitForFunction(()=>!document.querySelector('#branch-context').hidden);
  assert.equal(await page.locator('#prompt').inputValue(),'Original planning question');
  await page.locator('#prompt').fill('Edited planning question');await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#branch-context').hidden);
  assert.equal(await page.locator('#prompt').inputValue(),'Edited planning question');
  assert.equal(await page.locator('#consent').isChecked(),false);
  await page.locator('#send').click();await page.locator('#branch-preview').waitFor();
  assert.match(await page.locator('#branch-history').textContent(),/No earlier history/);
  await page.locator('#consent').check();await page.locator('#send').click();
  await page.waitForFunction(()=>document.querySelector('.earlier-branches') && !document.querySelector('#send').disabled);
  const afterEdit=cli(['conversations','show','--conversation',branching.id]);
  assert.deepEqual(afterEdit.messages.slice(0,4),savedMessages);assert.equal(afterEdit.active_run_ids.length,1);
  let refused=false;try {cli([...staleArgs,'--confirm',stalePreview.confirmation]);} catch {refused=true;}assert.equal(refused,true);
  await page.locator('#search').fill('Later confidential detail');await page.locator('#search-results button').first().click();
  await page.waitForFunction(()=>document.querySelector('.earlier-branches')?.open===true);
  assert.match(await page.locator('.search-hit').innerText(),/Later confidential detail/);await page.locator('#search').fill('');
  await page.locator('#messages > .assistant').getByRole('button',{name:'Regenerate',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#branch-context').hidden);
  assert.equal(await page.locator('#prompt').inputValue(),'Edited planning question');
  await page.locator('#send').click();await page.locator('#branch-preview').waitFor();
  await page.locator('#consent').check();await page.locator('#send').click();
  await page.waitForFunction(()=>document.querySelectorAll('#messages .message').length===8 && !document.querySelector('#send').disabled);
  const afterRegenerate=cli(['conversations','show','--conversation',branching.id]);
  assert.deepEqual(afterRegenerate.messages.slice(0,6),afterEdit.messages);assert.equal(afterRegenerate.active_run_ids.length,1);
  const nextPreview=cli(['chat','--conversation',branching.id,'--prompt','Continue new branch','--request-id','branch-next']);
  assert.equal(nextPreview.history.length,2);assert.equal(nextPreview.history[0].content,'Edited planning question');
  assert.equal(JSON.stringify(nextPreview.history).includes('Later confidential detail'),false);
  await page.screenshot({path:path.join(output,'office-branches.png'),fullPage:true});
  const backup = path.join(output, 'backup.json'); cli(['backup', '--out', backup]);
  const restored = path.join(output, 'restored'); cli(['restore', '--from', backup], restored);
  assert.deepEqual(cli(['conversations', 'show', '--conversation', c.id], restored), cli(['conversations', 'show', '--conversation', c.id]));
  await page.screenshot({path: path.join(output, 'p1-desktop.png')});
  assert.deepEqual(errors, []);
  const result = {cli_to_browser_revision: true, browser_to_cli_revision: true, two_materials: true, body_search_location: true, archive_restore: true, isolated_browser_drafts: true,
   old_and_new_md_docx: true, exact_receipt_recovery: true, live_owner_backup_and_restore: true, chat_edit_and_regenerate: true, preserved_branch_history: true, branch_draft_reload: true, stale_branch_confirmation_refused: true, page_errors: errors};
  await fs.writeFile(path.join(output, 'p1-browser-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
 } finally {
  if (browser) await browser.close();
  if (server.exitCode === null) {server.kill('SIGINT'); await Promise.race([once(server, 'exit'), new Promise(resolve => setTimeout(resolve, 5000))]);}
  if (server.exitCode === null) {server.kill('SIGTERM'); await once(server, 'exit');}
 }
}

(process.env.OFFICE_RACE_ONLY === '1' ? raceAcceptance() : browserAcceptance())
 .catch(error => {console.error(error); process.exitCode = 1;});
