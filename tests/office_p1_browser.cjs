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

async function remainingAcceptance(page, cli, workspace, fixtureRoot) {
 const {execFile} = require('node:child_process');
 const {promisify} = require('node:util');
 const runCLI = promisify(execFile);
 const waitFor = async (check, message) => {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {if (await check()) return; await new Promise(r => setTimeout(r, 80));}
  throw new Error(message);
 };
 const pressTo = async selector => {
  for (let n = 0; n < 20; n++) {
   if (await page.locator(selector).evaluate(el => el === document.activeElement)) return;
   await page.keyboard.press('Tab');
  }
  throw new Error('Keyboard could not reach ' + selector);
 };
 const long = cli(['conversations', 'list']).items.find(c => c.title === 'Long conversation fixture');
 assert.ok(long);
 await page.locator('#conversations button', {hasText: long.title}).click();
 await page.locator('.message').last().waitFor();
 assert.equal(await page.locator('.message').count(), 400);
 assert.ok(await page.locator('#messages').evaluate(el => el.scrollHeight > el.clientHeight * 10));
 await page.locator('#prompt').focus();
 await page.keyboard.type('Unsent long chat draft');
 await page.keyboard.press('Enter');
 await page.keyboard.type('Second paragraph');
 const draft = 'Unsent long chat draft\nSecond paragraph';
 assert.equal(await page.locator('#prompt').inputValue(), draft);
 await page.locator('#search').focus();
 await page.keyboard.type('Saved question 003');
 const hit = page.locator('#search-results button').first();
 await hit.waitFor();
 await pressTo('#show-archived'); await pressTo('#search-results button');
 await page.keyboard.press('Enter');
 await page.waitForFunction(() => document.activeElement?.classList.contains('search-hit'));
 const message = page.locator('.search-hit');
 assert.match(await message.innerText(), /Saved question 003/);
 assert.ok(await message.evaluate(el => {const r=el.getBoundingClientRect(), p=el.closest('#messages').getBoundingClientRect();return r.bottom>p.top && r.top<p.bottom;}));
 assert.equal(await page.locator('#prompt').inputValue(), draft);
 await page.reload(); await page.locator('.message').last().waitFor();
 assert.equal(await page.locator('#prompt').inputValue(), draft);
 // Native tab order and form activation, without implicit Enter-to-send.
 await page.locator('#new-chat').focus(); await page.keyboard.press('Enter');
 await page.locator('#prompt').focus();
 await page.keyboard.press('Shift+Tab');
 assert.equal(await page.locator('#report-toggle').evaluate(el=>el===document.activeElement),true);
 await page.keyboard.press('Shift+Tab');
 assert.equal(await page.locator('#upload').evaluate(el=>el===document.activeElement),true);
 assert.notEqual(await page.locator('.file-button').evaluate(el=>getComputedStyle(el).outlineStyle),'none', 'Keyboard material picker needs a visible focus indicator on its label.');
 await page.locator('#prompt').fill('hold-browser');
 await pressTo('#consent'); await page.keyboard.press('Space');
 await pressTo('#send'); await page.keyboard.press('Enter');
 await page.locator('#stop').waitFor();
 await waitFor(async () => fs.access(path.join(fixtureRoot, 'hold-browser.started')).then(()=>true,()=>false), 'Provider never received browser request');
 const created = cli(['conversations', 'list']).items.find(c => c.title === 'New conversation');
 const browserRun = cli(['conversations', 'show', '--conversation', created.id]).runs[0];
 assert.equal(cli(['runs', 'stop', '--run', browserRun.id]).status, 'stopping');
 await page.waitForFunction(() => document.getElementById('pending').textContent.includes('Stopping'));
 assert.equal(await page.locator('#send').isDisabled(), true);
 const blockedConversation=cli(['conversations','create','--title','Blocked other conversation']);
 const busyAttempt=await page.evaluate(async cid=>{
  const bootstrap=await (await fetch('/api/bootstrap')).json();
  const response=await fetch('/api/conversations/'+cid+'/runs',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':bootstrap.csrf},body:JSON.stringify({prompt:'Do not admit while stopping',intent:'chat',source_ids:[],revision_id:null,request_id:'blocked-while-stopping',consent:true})});
  return response.status;
 },blockedConversation.id);
 assert.equal(busyAttempt,409, 'Stopping must retain the workspace execution slot across conversations.');
 assert.deepEqual(cli(['conversations','show','--conversation',blockedConversation.id]).runs,[]);
 await fs.writeFile(path.join(fixtureRoot, 'hold-browser.release'), 'release');
 await waitFor(() => cli(['runs', 'show', '--run', browserRun.id]).status === 'cancelled', 'Browser request did not cancel');
 await page.waitForFunction(() => !document.getElementById('send').disabled);
 assert.match(await page.locator('#notice').innerText(), /Stopped by the user/);
 assert.equal(cli(['conversations', 'show', '--conversation', created.id]).messages.length, 0);
 // Other direction: real CLI submits, browser observes and stops the same receipt.
 const args = ['chat', '--conversation', created.id, '--prompt', 'hold-cli', '--request-id', 'remaining-cli-stop'];
 const preview = cli(args);
 const child = runCLI(python, ['-m','pocketchat','office','--data-dir',workspace,'--json',...args,'--confirm',preview.confirmation], {cwd:root,encoding:'utf8',timeout:20000}).then(
  r => ({code:0,...r}), e => ({code:e.code,stdout:e.stdout,stderr:e.stderr}));
 await waitFor(async () => fs.access(path.join(fixtureRoot, 'hold-cli.started')).then(()=>true,()=>false), 'Provider never received CLI request');
 await page.locator('#conversations button', {hasText: 'New conversation'}).click();
 await page.locator('#stop').waitFor();
 const cliRun = cli(['conversations','show','--conversation',created.id]).runs[0];
 await page.locator('#stop').focus(); await page.keyboard.press('Enter');
 await waitFor(() => cli(['runs','show','--run',cliRun.id]).status === 'stopping', 'CLI did not observe browser stop');
 await page.waitForFunction(() => document.getElementById('pending').textContent.includes('Stopping'));
 await fs.writeFile(path.join(fixtureRoot,'hold-cli.release'), 'release');
 const terminal = await child;
 assert.equal(terminal.code, 1, terminal.stderr);
 const receipt = JSON.parse(terminal.stdout).result;
 assert.equal(receipt.id, cliRun.id); assert.equal(receipt.status, 'cancelled');
 await page.waitForFunction(() => !document.getElementById('send').disabled);
 assert.match(await page.locator('#notice').innerText(), /Stopped by the user/);
 assert.equal(cli(['conversations','show','--conversation',created.id]).messages.length, 0);
 // A retry is explicit and requires new consent; it does not auto-dispatch.
 await page.locator('#notice button').focus(); await page.keyboard.press('Enter');
 assert.equal(await page.locator('#prompt').inputValue(), 'hold-cli');
 assert.equal(await page.locator('#consent').isChecked(), false);
 await page.locator('#new-chat').focus(); await page.keyboard.press('Enter');
 await page.locator('#search').fill('');
 await page.locator('#conversations button', {hasText: long.title}).click();
 await page.waitForFunction(title => document.getElementById('rename').textContent === title && !document.getElementById('prompt').disabled, long.title);
 assert.equal(await page.locator('#prompt').inputValue(), draft);
 await page.screenshot({path:path.join(output,'p1-remaining-desktop.png')});
 return {long_conversation_messages:400, keyboard_search_location:true, multiline_draft_reload:true, keyboard_send_stop_retry:true, keyboard_material_focus:true, stopping_retains_slot:true,
  browser_request_cli_stop:true, cli_request_browser_stop:true, cancelled_output_not_saved:true, isolated_draft_retained:true};
}

async function reverseExportAcceptance(page, cli) {
 const {createHash}=require('node:crypto');
 const materials=[['browser-notes.txt','Completed 12 customer follow-ups.'],['browser-next.md','Prepare the handover.']];
 await page.locator('#new-chat').click();
 await page.locator('#upload').setInputFiles(materials.map(([name,text])=>({name,mimeType:'text/plain',buffer:Buffer.from(text)})));
 await page.waitForFunction(()=>document.querySelectorAll('.source').length===2 && !document.getElementById('prompt').disabled);
 await page.locator('#report-toggle').click();await page.locator('#prompt').fill('Make another report.');
 await page.locator('#consent').check();await page.locator('#send').click();
 await page.waitForFunction(()=>document.querySelectorAll('.artifact').length===1 && !document.getElementById('send').disabled);
 const c=cli(['conversations','list']).items[0];
 const old=cli(['conversations','show','--conversation',c.id]).artifacts[0];
 const args=['report','revise','--conversation',c.id,'--version',old.id,'--prompt','Shorten it.','--request-id','reverse-export-revision'];
 const preview=cli(args),done=cli([...args,'--confirm',preview.confirmation]);assert.equal(done.status,'completed');
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.artifact').length===2);
 const versions=cli(['conversations','show','--conversation',c.id]).artifacts;
 assert.equal(versions[1].id,old.id);assert.equal(versions[0].predecessor,old.id);
 const expectedSources=materials.map(([name,text],i)=>({name,citation:'S'+(i+1),sha256:createHash('sha256').update(text).digest('hex')}));
 const exports=[];
 for(const [index,label] of [[1,'old'],[0,'new']]){
  const card=page.locator('.artifact').nth(index);
  // Open and dismiss the report preview with native keyboard activation.
  await card.getByRole('button',{name:'Preview',exact:true}).focus();await page.keyboard.press('Enter');
  await page.locator('#report-preview').waitFor();
  assert.match(await page.locator('#preview-content').innerText(),/12/);
  await page.keyboard.press('Escape');await page.locator('#report-preview').waitFor({state:'hidden'});
  for(const [format,button] of [['md','Markdown'],['docx','Word']]){
   const download=page.waitForEvent('download');
   await card.getByRole('button',{name:button,exact:true}).click();
   const item=await download;assert.equal(await item.failure(),null);
   assert.equal(item.suggestedFilename(),'report-'+versions[index].id.slice(0,8)+'.'+format);
   const filename=path.join(output,'reverse-'+label+'.'+format);await item.saveAs(filename);
   exports.push({label,format,version:versions[index].id,filename});
  }
 }
 const oldText=await fs.readFile(path.join(output,'reverse-old.md'),'utf8'),newText=await fs.readFile(path.join(output,'reverse-new.md'),'utf8');
 assert.notEqual(oldText,newText);assert.match(oldText,/## Risks/);assert.doesNotMatch(newText,/## Risks/);
 for(const version of versions){
  const artifact=await page.evaluate(async ({cid,aid})=>(await fetch('/api/conversations/'+cid+'/artifacts/'+aid+'/view')).json(),{cid:c.id,aid:version.id});
  assert.deepEqual(artifact.sources.map(({name,citation,sha256})=>({name,citation,sha256})),expectedSources);
  const label=version.id===old.id?'old':'new';assert.equal(await fs.readFile(path.join(output,'reverse-'+label+'.md'),'utf8'),artifact.content);
 }
 const docxRead=JSON.parse(execFileSync(python,['-c',"import json,sys; from docx import Document; print(json.dumps(['\\n'.join(p.text for p in Document(f).paragraphs) for f in sys.argv[1:]]))",...['old','new'].map(label=>path.join(output,'reverse-'+label+'.docx'))],{encoding:'utf8'}));
 assert.notEqual(docxRead[0],docxRead[1]);assert.match(docxRead[0],/Risks/);assert.doesNotMatch(docxRead[1],/Risks/);
 for(const text of docxRead)for(const source of expectedSources){assert.ok(text.includes('['+source.citation+'] '+source.name));assert.ok(text.includes(source.sha256));}
 await page.screenshot({path:path.join(output,'p1-reverse-exports.png')});
 return {two_browser_materials:true,cli_revision_preserves_predecessor:true,old_new_browser_md_docx:true,exact_markdown_and_source_manifests:true,docx_readback:true,keyboard_preview_escape:true,exports:exports.map(({filename,...row})=>({...row,file:path.basename(filename)}))};
}

async function browserAcceptance() {
 const {chromium} = require('playwright');
 if (!output) throw new Error('Set OFFICE_OUTPUT to an existing private evidence directory.');
 const fixtureRoot = path.join(output, 'fixture');
 const server = spawn(python, ['examples/office-demo.py', '--output', fixtureRoot, ...(process.env.OFFICE_REMAINING_ONLY === '1' ? ['--acceptance'] : [])], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});
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
  if (process.env.OFFICE_REMAINING_ONLY === '1' || process.env.OFFICE_REVERSE_EXPORT_ONLY === '1') {
   browser = await chromium.launch({headless:true,channel:'chrome'});
   const page = await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
   const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(launch.origin+'/#'+launch.token);
   await page.waitForFunction(() => document.getElementById('model').textContent === 'offline-fixture');
   const reverseOnly=process.env.OFFICE_REVERSE_EXPORT_ONLY === '1';
   const result=reverseOnly ? await reverseExportAcceptance(page,cli) : await remainingAcceptance(page,cli,workspace,fixtureRoot);
   assert.deepEqual(errors,[]);result.page_errors=errors;
   await fs.writeFile(path.join(output,reverseOnly?'p1-reverse-export-result.json':'p1-remaining-result.json'),JSON.stringify(result,null,2));
   console.log(JSON.stringify(result));return;
  }
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
