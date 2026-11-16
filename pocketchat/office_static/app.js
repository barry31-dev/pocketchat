'use strict';
const $ = id => document.getElementById(id);
let csrf = '', current = null, selected = new Set(), report = false, revision = null, activeRun = null, polling = null;
let conversations = [], lastSubmission = null, displayedModelRevision = null;
let receiptKey = '', draftPrefix = '', viewKey = '', draftReady = false, opening = false, searchSequence = 0;
let materialSequence = 0;
let projectState = null, memoryState = null, summaryState = null, contextReview = null;
let searchTimer = null, mutation = false, legacyPending = false, branchFrom = null, branchReview = null;
function editingBlocked() { return Boolean(activeRun || lastSubmission || opening || mutation || legacyPending); }
const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
function notice(message = '') { $('notice').textContent = message; }
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type':'application/json','X-CSRF-Token':csrf}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { const e=new Error(data.error || 'Request failed.');e.status=response.status;e.requestNotAccepted=data.request_not_accepted===true;throw e; }
  return data;
}
function markdown(parent, content) {
  parent.replaceChildren();
  let list = null;
  for (const line of content.split('\n')) {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line), bullet = /^[-*]\s+(.+)$/.exec(line);
    let e;
    if (bullet) { if (!list) { list = node('ul'); parent.append(list); } e = node('li'); list.append(e); }
    else { list = null; e = node(heading ? 'h' + heading[1].length : 'p'); parent.append(e); }
    const text = heading ? heading[2] : bullet ? bullet[1] : line;
    text.split('**').forEach((part, i) => e.append(node(i % 2 ? 'strong' : 'span', part)));
  }
}
function busy(value) {
  value = value || editingBlocked();
  const readOnly = value || Boolean(current?.archived);
  const choice=$('model-choice');if(choice)choice.disabled=value;
  for (const id of ['send','upload','prompt','consent','report-toggle','leave-branch']) $(id).disabled = readOnly;
  for (const e of $('project-settings')?.querySelectorAll('button, input, textarea') || []) e.disabled = value;
  if ($('preview-request')) $('preview-request').disabled = readOnly;
  $('consent').disabled=readOnly || Boolean(branchFrom && !branchReview && !contextReview);
  $('send').textContent=branchFrom && !branchReview && !contextReview ? 'Review branch' : 'Send ↑';
  $('stop').hidden = !value || !activeRun; $('new-chat').disabled = value;
  $('archive').disabled = value; $('rename').disabled = readOnly;
  for (const button of document.querySelectorAll('[data-branch-run]')) button.disabled = readOnly;
  for (const input of $('sources').querySelectorAll('input')) input.disabled = readOnly;
  for (const button of $('sources').querySelectorAll('.source-view')) button.disabled = opening;
  const weekly = $('weekly'); if(weekly)weekly.disabled = readOnly;
  for (const button of document.querySelectorAll('[data-prompt]')) button.disabled = readOnly;
}
function draftValue() { return {prompt:$('prompt').value, report, revision, sources:[...selected], branch_from:branchFrom}; }
function saveDraft() {
  if (!draftPrefix || !draftReady) return;
  try { sessionStorage.setItem(draftPrefix + (current?.id || 'new'), JSON.stringify(draftValue())); }
  catch { notice('Draft could not be saved in this browser. Copy it before leaving.'); }
}
function restoreDraft() {
  closeMaterial();
  contextReview=null;summaryState=null;
  if($('summary-originals'))$('summary-originals').replaceChildren();
  if($('summary-content'))$('summary-content').value='';
  if($('summary-accept'))$('summary-accept').checked=false;
  let draft = null;
  try { draft = JSON.parse(sessionStorage.getItem(draftPrefix + (current?.id || 'new')) || 'null'); }
  catch { notice('Saved draft could not be read; no message was sent.'); }
  if (!draft || typeof draft.prompt !== 'string' || draft.prompt.length > 12000 ||
      typeof draft.report !== 'boolean' || !Array.isArray(draft.sources)) draft = {prompt:'',report:false,revision:null,sources:[]};
  $('prompt').value = draft.prompt;
  selected = new Set(draft.sources.filter(id => current?.sources.some(source => source.id === id)));
  setReport(draft.report, current?.artifacts.some(a => a.id === draft.revision) ? draft.revision : null);
  setBranch(typeof draft.branch_from === 'string' && current?.chat_run_ids?.includes(draft.branch_from) ? draft.branch_from : null);
  $('consent').checked = false; draftReady = true;
  try { sessionStorage.setItem(viewKey, current?.id || 'new'); } catch { /* Draft save reports unavailable storage. */ }
}
function changedDraft() { contextReview=null; $('consent').checked = false; branchReview=null; $('branch-preview').hidden=true; saveDraft(); busy(false); }
async function searchMessages() {
  const sequence = ++searchSequence, query = $('search').value.trim();
  $('search-results').replaceChildren(); renderList();
  if (!query) return;
  try {
    const result = await api('/api/search', {query, archived:$('show-archived').checked});
    if (sequence !== searchSequence) return;
    $('search-results').append(node('p', result.truncated ? 'First 100 message matches' : `${result.items.length} message matches`, 'muted'));
    for (const hit of result.items) {
      const button = node('button', undefined, 'search-result');
      button.append(node('strong', hit.title), node('small', hit.excerpt));
      button.onclick = async () => { try {
        await openConversation(hit.conversation_id);
        const message = $('messages').querySelector(`[data-message-id="${hit.message_id}"]`);
        if (!message) throw new Error('The matching message is not available. Search again.');
        for (const old of $('messages').querySelectorAll('.search-hit')) old.classList.remove('search-hit');
        const earlier=message.closest?.('details');if(earlier)earlier.open=true;
        message.classList.add('search-hit'); message.focus(); message.scrollIntoView({block:'center'});
      } catch(e) { notice(e.message); } };
      $('search-results').append(button);
    }
  } catch(e) { if (sequence === searchSequence) notice(e.message); }
}
function setReport(value, old = null) { if(value)setBranch(null); report = value; revision = old; $('report-toggle').setAttribute('aria-pressed', String(value)); $('revision-label').textContent = old ? 'Revising version ' + ((current?.artifacts.length || 0) - (current?.artifacts.findIndex(a=>a.id===old) ?? 0)) : ''; $('prompt').placeholder = value ? 'What should this report cover?' : 'Ask anything, or tell me what you need…'; }
function setBranch(rid) {
  branchFrom=rid;branchReview=null;$('branch-preview').hidden=true;
  $('branch-context').hidden=!rid;
  $('branch-label').textContent=rid ? 'New branch from this turn. Review its exact history below before sending; old replies stay saved.' : '';
}
async function startBranch(rid) {
  if(editingBlocked() || !current || current.archived)return;
  const cid=current.id;mutation=true;busy(true);
  try {
    const run=await api('/api/runs/'+rid);
    if(current?.id!==cid || run.conversation_id!==cid || run.status!=='completed' || run.request.intent!=='chat')throw new Error('Choose a completed chat turn in this conversation.');
    const wire=run.request.wire || run.request;
    $('prompt').value=wire.prompt;selected=new Set(wire.source_ids);setReport(false);setBranch(rid);
    changedDraft();renderConversation(current);notice('Edit the question, or send it unchanged to regenerate. Review and confirm before sending.');$('prompt').focus();
  } finally {mutation=false;busy(false);}
}
$('leave-branch').onclick=()=>{if(editingBlocked())return;setBranch(null);changedDraft();notice('Continuing the current branch. Draft text retained.');};
async function refreshList() {
  conversations = (await api('/api/conversations' + ($('show-archived').checked ? '/archived' : ''))).items;
  renderList();
}
function renderList() {
  $('conversations').replaceChildren();
  for (const c of conversations.filter(c => c.title.toLowerCase().includes($('search').value.toLowerCase()))) {
    const b = node('button', c.title, current?.id === c.id ? 'active' : '');
    b.onclick = () => openConversation(c.id).catch(e=>notice(e.message)); $('conversations').append(b);
  }
}
function renderConversation(c) {
  current = c; $('rename').textContent = c.title; $('archive').hidden=false; $('archive').textContent=c.archived?'Restore':'Archive'; renderList();
  if (c.messages.length) {
    $('messages').replaceChildren();
    const active=new Set(c.active_run_ids || c.messages.map(m=>m.run_id));
    const earlier=node('details',undefined,'earlier-branches');earlier.append(node('summary',branchFrom ? 'Saved messages · review the pending branch history before sending' : 'Saved earlier branches · not sent as current history'));
    for (const m of c.messages) {
      const section = node('section', undefined, 'message ' + m.role); section.dataset.messageId=String(m.id); section.tabIndex=-1; section.append(node('div', m.role === 'user' ? 'You' : 'PocketChat', 'role'));
      const text = node('div'); markdown(text, m.content); section.append(text);
      const copy = node('button','Copy','copy'); copy.onclick=()=>navigator.clipboard.writeText(m.content).then(()=>notice('Copied.')).catch(()=>notice('Copy unavailable. Select the text instead.'));
      section.append(copy);
      if(c.chat_run_ids?.includes(m.run_id)){
        const branch=node('button',m.role==='user'?'Edit question':'Regenerate','branch-action');branch.dataset.branchRun=m.run_id;
        branch.onclick=()=>startBranch(m.run_id).catch(e=>notice(e.message));section.append(branch);
      }
      (active.has(m.run_id) ? $('messages') : earlier).append(section);
    }
    if(c.messages.some(m=>!active.has(m.run_id)))$('messages').append(earlier);
  } else if (!$('messages').querySelector('.welcome')) { $('messages').replaceChildren(node('p','This is a fresh conversation. Start with a question or add some material.','empty-note')); }
  $('sources').replaceChildren();
  if (!c.sources.length) $('sources').append(node('p','Add notes to give your work a starting point.','empty-note'));
  for (const s of c.sources) {
    const label = node('label',undefined,'source'), checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=selected.has(s.id);
    checkbox.onchange=()=>{if(checkbox.checked)selected.add(s.id);else selected.delete(s.id);changedDraft();};
    const detail=node('span',s.name); detail.append(node('small',`${s.bytes} bytes · ${s.sha256.slice(0,10)}`)); label.append(checkbox,detail);
    const row=node('div',undefined,'source-row'), view=node('button','View','source-view');view.type='button';
    view.setAttribute('aria-label','View saved material '+s.name);view.onclick=()=>viewMaterial(c.id,s.id);
    row.append(label,view);$('sources').append(row);
  }
  $('artifacts').replaceChildren();
  if (!c.artifacts.length) $('artifacts').append(node('p','Finished reports appear here. Earlier versions stay yours.','empty-note'));
  for (const [index,a] of c.artifacts.entries()) {
    const card=node('div',undefined,'artifact');card.append(node('strong','Report · version '+(c.artifacts.length-index)),node('small',new Date(a.created*1000).toLocaleString()));
    const actions=node('div',undefined,'artifact-actions');
    for (const [label,action] of [['Preview',async()=>{const d=await api(`/api/conversations/${c.id}/artifacts/${a.id}/view`);markdown($('preview-content'),d.content);$('report-preview').showModal();}],['Revise',async()=>{if(editingBlocked()||current.archived)return;setReport(true,a.id);changedDraft();$('prompt').focus();}],['Word',()=>download(c.id,a.id,'docx')],['Markdown',()=>download(c.id,a.id,'md')]]) {
      const b=node('button',label);b.onclick=()=>Promise.resolve(action()).catch(e=>notice(e.message));actions.append(b);
    }card.append(actions);$('artifacts').append(card);
  }
  $('messages').scrollTop=$('messages').scrollHeight; busy(Boolean(activeRun || lastSubmission || opening));
}
async function download(cid,aid,format) {
  const response=await fetch(`/api/conversations/${cid}/artifacts/${aid}/${format}`);
  if(!response.ok)throw new Error((await response.json()).error);
  const url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download=`report-${aid.slice(0,8)}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function closeMaterial() {
  materialSequence++;
  const dialog=$('material-preview');
  if(dialog?.open)dialog.close();
  if($('material-text'))$('material-text').textContent='';
  if($('material-meta'))$('material-meta').textContent='';
  if($('material-status'))$('material-status').textContent='';
}
async function viewMaterial(cid, sid) {
  if(opening || current?.id!==cid)return;
  closeMaterial();
  const sequence=materialSequence;
  const dialog=$('material-preview');
  $('material-title').textContent='Saved material';
  $('material-status').textContent='Loading local copy…';
  dialog.showModal();
  try {
    const source=await api(`/api/conversations/${cid}/sources/${sid}`);
    if(opening || sequence!==materialSequence || current?.id!==cid || !dialog.open)return;
    if(source.conversation_id!==cid || source.id!==sid)throw new Error('Material identity mismatch.');
    $('material-title').textContent=source.name;
    $('material-meta').textContent=`${source.citation} · ${source.bytes} bytes · SHA-256 ${source.sha256}`;
    $('material-text').textContent=source.text;
    $('material-status').textContent='Saved local copy. Viewing does not select it or send a request.';
  } catch(e) {
    if(opening || sequence!==materialSequence || current?.id!==cid || !dialog.open)return;
    $('material-status').textContent='Material could not be read: '+e.message;
  }
}
async function openConversation(cid) {
  closeMaterial();
  if(opening||mutation||legacyPending)throw new Error('Wait for the current action or resolve the saved receipt before switching.');
  if((activeRun||lastSubmission) && current?.id!==cid)throw new Error('Wait for this request to finish before switching conversations.');
  saveDraft(); opening=true; busy(true);
  try {
    const c=await api('/api/conversations/'+cid); current=c; restoreDraft(); renderConversation(c);
    const running=c.runs.find(r=>['running','stopping'].includes(r.status));
    if(running)watch(running.id);
    else if(c.runs[0] && ['failed','cancelled','unknown'].includes(c.runs[0].status))showFailure(await api('/api/runs/'+c.runs[0].id));
  } finally { opening=false; busy(Boolean(activeRun||lastSubmission)); }
}
async function ensureConversation() {
  if(!current){
    const c=await api('/api/conversations',{title:'New conversation'});
    current=c; saveDraft(); sessionStorage.removeItem(draftPrefix+'new');
    sessionStorage.setItem(viewKey,c.id); await refreshList(); renderConversation(c);
  }
  return current.id;
}
function showFailure(r) {
  notice(r.error || 'The request did not finish.');const retry=node('button','Review & retry');
  retry.onclick=()=>{ if(editingBlocked())return; $('prompt').value=r.request.prompt;selected=new Set(r.request.source_ids);setReport(r.request.intent==='report',r.request.revision_id);setBranch(r.request.branch_from || null);renderConversation(current);changedDraft();notice('Review the request, then send to retry.'); };$('notice').append(' ',retry);
}
function watch(rid) {
  activeRun=rid;busy(true);if(polling)clearTimeout(polling);
  const poll=async()=>{try{
    const r=await api('/api/runs/'+rid);$('pending').hidden=false;$('pending').textContent=r.status==='stopping'?'Stopping… waiting for the running request to exit.':'Working on your request…';
    let preview=$('stream-preview');if(!preview){preview=node('div',undefined,'message streaming');preview.id='stream-preview';$('messages').append(preview);}preview.textContent=r.preview || 'Thinking…';
    if(['running','stopping'].includes(r.status)){polling=setTimeout(poll,350);return;}
    const conversation=await api('/api/conversations/'+r.conversation_id);
    $('pending').hidden=true;preview.remove();renderConversation(conversation);activeRun=null;
    if(r.status==='completed' && r.request.intent==='report') { const saved=current.artifacts.find(a=>a.run_id===r.id);if(saved){setReport(true,saved.id);saveDraft();} }
    lastSubmission=null;sessionStorage.removeItem(receiptKey);busy(false);
    if(r.status!=='completed')showFailure(r);else notice('');await refreshList();if(legacyPending)showLegacyReceipt();
  }catch(e){notice(e.message+' Status is unknown; reconnect before retrying.');polling=setTimeout(poll,2000);}};poll();
}
function uncertainSubmission(error) {
  busy(true);$('stop').hidden=true;notice(error.message + ' The outcome is unknown. Recover the same request before sending anything else.');
  const recover=node('button','Recover request');recover.onclick=()=>dispatchPending();$('notice').append(' ',recover);
}
async function dispatchPending() {
  if(!lastSubmission)return;
  try {
    const {cid,body}=lastSubmission;
    const r=lastSubmission.confirmation ? await api('/api/conversations/'+cid+'/confirmed',{message:body,confirmation:lastSubmission.confirmation}) : await api('/api/conversations/'+cid+'/runs',body);
    if(current?.id===cid && $('prompt').value===body.prompt){$('prompt').value='';setBranch(null);saveDraft();}
    $('consent').checked=false;notice();watch(r.id);
  }catch(e){
    if(e.requestNotAccepted || [400,403,404,413,415].includes(e.status)){lastSubmission=null;sessionStorage.removeItem(receiptKey);$('consent').checked=false;busy(false);notice(e.message);}
    else uncertainSubmission(e);
  }
}
function requestBody() {
  const body={prompt:$('prompt').value,intent:report?'report':'chat',source_ids:[...selected],revision_id:revision,request_id:crypto.randomUUID(),consent:true};
  if(branchFrom)body.branch_from=branchFrom;
  return body;
}
function requestKey(cid, body) {
  return JSON.stringify([cid,body.prompt,body.intent,body.source_ids,body.revision_id,body.branch_from||null,$('model-choice').value,displayedModelRevision]);
}
async function previewRequest() {
  if(editingBlocked()||current?.archived)return;
  mutation=true;busy(true);contextReview=null;$('consent').checked=false;
  try {
    const cid=await ensureConversation(), body=requestBody();
    const key=requestKey(cid,body), preview=await api('/api/conversations/'+cid+'/preview',body);
    if(key!==requestKey(current?.id,requestBody()))throw new Error('Draft changed. Preview again.');
    if(preview.model_revision!==displayedModelRevision){await refreshModels();throw new Error('Model changed. Preview again.');}
    contextReview={cid,body,confirmation:preview.confirmation,key};
    $('context-payload').textContent=JSON.stringify(preview.prepared_payload || {messages:[...preview.history,{role:'user',content:preview.message}]},null,2);
    $('context-manifest').textContent=JSON.stringify(preview.context_manifest || {note:'Legacy workspace: back up and upgrade for project context.'},null,2);
    $('context-preview').showModal();notice('Request previewed. Nothing has been sent.');
  }catch(e){notice(e.message);}finally{mutation=false;busy(false);}
}
function foundationStatus(text) { if($('foundation-status'))$('foundation-status').textContent=text; }
function renderMemory() {
  const list=$('memory-records');list.replaceChildren();
  for(const record of memoryState.records){
    const card=node('div',undefined,'memory-record');card.dataset.memoryId=record.memory_id;
    card.append(node('strong',record.admission_status+' · revision '+record.revision),node('p',record.content));
    for(const action of record.admission_status==='candidate'?['accept','revise','forget']:record.admission_status==='accepted'?['revise','retire','forget']:['forget']){
      const b=node('button',action[0].toUpperCase()+action.slice(1));
      b.onclick=()=>mutateMemory(action,record);card.append(b);
    }
    list.append(card);
  }
  if(!memoryState.records.length)list.append(node('p','No project guidance saved.','muted'));
}
async function refreshProject() {
  projectState=null;memoryState=null;
  foundationStatus('Reading project…');
  try {
    const project=await api('/api/project'), memory=await api('/api/memory');
    if(project.project_id!==memory.project_id)throw new Error('Project identity changed. Refresh before saving.');
    projectState=project;memoryState=memory;
    $('project-identity').textContent=project.project_id+' · instructions r'+project.instruction_revision+' · memory r'+project.memory_revision;
    $('project-instructions').value=project.instructions;$('memory-enabled').checked=project.memory_enabled;
    renderMemory();foundationStatus('Project loaded. Unsent context can be previewed.');
  }catch(e){$('memory-records').replaceChildren();foundationStatus(e.message+' Back up & upgrade an older workspace before using project controls.');throw e;}
}
async function foundationAction(action) {
  if(editingBlocked())return;
  foundationStatus('Working…');mutation=true;busy(true);
  try{await action();changedDraft();}catch(e){foundationStatus(e.message);}
  finally{changedDraft();mutation=false;busy(false);}
}
async function saveProject(field) {
  await foundationAction(async()=>{
    if(!projectState)throw new Error('Refresh the project before saving.');
    const body=field==='instructions'?{project_id:projectState.project_id,instruction_revision:projectState.instruction_revision,instructions:$('project-instructions').value}:{project_id:projectState.project_id,memory_revision:projectState.memory_revision,memory_enabled:$('memory-enabled').checked};
    await api('/api/project/'+field,body);
    try{await refreshProject();}catch(e){throw new Error('Project '+field+' saved, but refresh failed: '+e.message);}
    foundationStatus('Project '+field+' saved. Preview again before sending.');
  });
}
async function mutateMemory(action, record=null) {
  // Native confirmations describe actual writes; model text never triggers them.
  if(['accept','revise'].includes(action)&&!window.confirm('This is personal/project guidance, not a verified external fact. '+action+' this record?'))return;
  if(action==='forget'&&!window.confirm('Forget this live memory record? Old traces, exports and backups are historical copies and are NOT erased.'))return;
  let content=$('memory-content').value;
  if(action==='revise'){content=window.prompt('Propose revised guidance; it requires new acceptance.',record.content);if(content===null)return;}
  await foundationAction(async()=>{
    if(!memoryState)throw new Error('Refresh project memory before saving.');
    if(action==='propose'&&!$('memory-guidance').checked)throw new Error('Explicitly declare personal/project guidance before proposing.');
    const body={project_id:memoryState.project_id,store_sha256:memoryState.store_sha256};
    if(record){body.memory_id=record.memory_id;body.revision=record.revision;}
    if(['propose','accept','revise'].includes(action))body.personal_guidance=true;
    if(['propose','revise'].includes(action))body.content=content;
    if(action==='propose')body.category='project_decision';
    if(action==='forget')body.acknowledge_history=true;
    await api('/api/memory/'+action,body);
    try{await refreshProject();}catch(e){throw new Error('Memory '+action+' saved, but refresh failed: '+e.message);}
    $('memory-content').value='';$('memory-guidance').checked=false;foundationStatus('Memory '+action+' saved. Preview again before sending.');
  });
}
async function loadSummary() {
  await foundationAction(async()=>{
    if(!current)throw new Error('Open a conversation first.');
    summaryState=await api('/api/conversations/'+current.id+'/summary');summaryState.cid=current.id;
    const list=$('summary-originals');list.replaceChildren();
    for(const message of summaryState.original_messages){
      const label=node('label'),input=node('input');input.type='checkbox';input.dataset.messageId=message.id;
      label.append(input,node('span',message.role+': '+message.content));list.append(label);
    }
    $('summary-content').value=summaryState.summary?.content || '';$('summary-accept').checked=false;
    foundationStatus('Original messages loaded. Select evidence and explicitly accept a summary.');
  });
}
async function saveSummary() {
  await foundationAction(async()=>{
    if(!summaryState||summaryState.cid!==current?.id)throw new Error('Load this conversation’s originals first.');
    if(!$('summary-accept').checked)throw new Error('Explicitly accept the summary before saving.');
    const ids=new Set(Array.from($('summary-originals').querySelectorAll('input')).filter(i=>i.checked).map(i=>Number(i.dataset.messageId)));
    const refs=[];
    for(const message of summaryState.original_messages.filter(m=>ids.has(m.id))){
      const bytes=new TextEncoder().encode(message.content),digest=await crypto.subtle.digest('SHA-256',bytes);
      refs.push({message_id:message.id,run_id:message.run_id,start:0,end:Array.from(message.content).length,sha256:Array.from(new Uint8Array(digest)).map(b=>b.toString(16).padStart(2,'0')).join('')});
    }
    if(!refs.length)throw new Error('Select at least one original message.');
    const body={project_id:summaryState.project_id,revision:summaryState.summary?.revision || 0,content:$('summary-content').value,refs,accept:true};
    const saved=await api('/api/conversations/'+summaryState.cid+'/summary',body);saved.cid=summaryState.cid;summaryState=saved;
    $('summary-accept').checked=false;foundationStatus('Summary saved. Originals remain available; preview again before sending.');
  });
}
if($('project-settings')){
  $('refresh-project').onclick=()=>foundationAction(refreshProject);
  $('save-instructions').onclick=()=>saveProject('instructions');
  $('save-memory-preference').onclick=()=>saveProject('memory');
  $('propose-memory').onclick=()=>mutateMemory('propose');
  $('load-summary').onclick=loadSummary;$('save-summary').onclick=saveSummary;
  $('preview-request').onclick=previewRequest;$('close-context-preview').onclick=()=>$('context-preview').close();
}
$('composer').onsubmit=async event=>{event.preventDefault();try{
  if(mutation||opening||legacyPending||activeRun)return;
  notice();
  if(lastSubmission){await dispatchPending();return;}
  if(!$('consent').checked && !(branchFrom && !branchReview))throw new Error('Confirm sending this conversation and selected material to the provider.');
  if(current?.archived)throw new Error('Restore this conversation before sending.');
  if(contextReview){
    if(!$('consent').checked)throw new Error('Review the preview and confirm sending before dispatch.');
    if(contextReview.key!==requestKey(current?.id,requestBody())){changedDraft();throw new Error('Draft changed. Preview and confirm again.');}
    lastSubmission={cid:contextReview.cid,body:contextReview.body,confirmation:contextReview.confirmation};
    sessionStorage.setItem(receiptKey,JSON.stringify(lastSubmission));contextReview=null;
    await dispatchPending();return;
  }
  // Capture every content/consent dependency before creating a conversation.
  const body={prompt:$('prompt').value,intent:report?'report':'chat',source_ids:[...selected],revision_id:revision,request_id:crypto.randomUUID(),consent:true};
  if(branchFrom)body.branch_from=branchFrom;
  const displayedModel=$('model').textContent, displayedProvider=$('provider').textContent, displayedProfile=$('model-choice').value, modelRevision=displayedModelRevision;
  const reviewingBranch=Boolean(branchFrom && !branchReview && !contextReview);
  mutation=true;busy(true);
  const cid=await ensureConversation();
  if((!$('consent').checked && !reviewingBranch) || body.prompt!==$('prompt').value || body.intent!==(report?'report':'chat') || body.revision_id!==revision || (body.branch_from||null)!==branchFrom || JSON.stringify(body.source_ids)!==JSON.stringify([...selected]))
    throw new Error('The draft changed. Review and confirm it again.');
  const preview=await api('/api/conversations/'+cid+'/preview',body);
  if((preview.model_revision!==undefined && modelRevision!==preview.model_revision) || (preview.profile!==undefined && preview.profile!==displayedProfile) || preview.model!==displayedModel || new URL(preview.provider).origin!==displayedProvider){$('consent').checked=false;await refreshModels();throw new Error('Model changed in another client. Review and confirm again.');}
  if((!$('consent').checked && !reviewingBranch) || body.prompt!==$('prompt').value || body.intent!==(report?'report':'chat') || body.revision_id!==revision || (body.branch_from||null)!==branchFrom || JSON.stringify(body.source_ids)!==JSON.stringify([...selected]))throw new Error('The draft changed. Review and confirm it again.');
  if(branchFrom){
    const key=JSON.stringify([cid,body.prompt,body.intent,body.source_ids,body.revision_id,body.branch_from,preview.history,preview.message,preview.profile,preview.model_revision,preview.model,preview.provider]);
    if(branchReview!==key){
      branchReview=key;$('branch-history').textContent=preview.history.map(m=>m.role+': '+m.content).join('\n\n') || '(No earlier history.)';
      $('branch-message').textContent=preview.message;$('branch-preview').hidden=false;$('consent').checked=false;
      notice('Review the exact branch history and message below, then confirm sending. Nothing has been sent.');return;
    }
  }
  lastSubmission={cid,body,confirmation:preview.confirmation};sessionStorage.setItem(receiptKey,JSON.stringify(lastSubmission));await dispatchPending();
}catch(e){notice(e.message);}finally{mutation=false;busy(Boolean(activeRun||lastSubmission));}};
$('stop').onclick=()=>api('/api/runs/'+activeRun+'/stop',{}).catch(e=>notice(e.message));
$('new-chat').onclick=()=>{if(editingBlocked())return;saveDraft();current=null;restoreDraft();$('messages').replaceChildren();$('sources').replaceChildren();$('artifacts').replaceChildren();$('rename').textContent='New conversation';$('archive').hidden=true;notice();renderList();busy(false);$('prompt').focus();};
$('archive').onclick=async()=>{if(!current||editingBlocked())return;mutation=true;busy(true);try{
  saveDraft();const cid=current.id;
  renderConversation(await api('/api/conversations/'+cid+'/archive',{archived:!current.archived}));
  $('show-archived').checked=current.archived;await refreshList();await searchMessages();$('consent').checked=false;
}catch(e){notice(e.message);}finally{mutation=false;busy(false);}};
$('show-archived').onchange=async()=>{try{await refreshList();await searchMessages();}catch(e){notice(e.message);}};
$('upgrade').onclick=async()=>{if(editingBlocked())return;mutation=true;busy(true);try{const result=await api('/api/upgrade',{});$('upgrade').hidden=true;if($('project-settings'))await refreshProject();notice(result.upgraded?'Workspace upgraded. A verified backup was saved in the private workspace.':'Workspace is already current.');}catch(e){notice(e.message);}finally{mutation=false;busy(false);}};
$('prompt').oninput=changedDraft;
$('rename').onclick=async()=>{if(!current||editingBlocked()||current.archived)return;const title=window.prompt('Conversation title',current.title);if(title===null)return;mutation=true;busy(true);try{renderConversation(await api('/api/conversations/'+current.id+'/rename',{title}));await refreshList();}catch(e){notice(e.message);}finally{mutation=false;busy(false);}};
$('search').oninput=()=>{++searchSequence;clearTimeout(searchTimer);searchTimer=setTimeout(searchMessages,160);};$('report-toggle').onclick=()=>{if(editingBlocked()||current?.archived)return;setReport(!report);changedDraft();};
$('weekly').onclick=()=>{if(editingBlocked()||current?.archived)return;setReport(true);$('prompt').value='Create a concise weekly report from the selected material. Include progress, risks and next steps, with source references.';changedDraft();$('prompt').focus();};
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{if(editingBlocked()||current?.archived)return;$('prompt').value=b.dataset.prompt;changedDraft();$('prompt').focus();};
$('upload').onchange=async()=>{if(editingBlocked()||current?.archived)return;const files=Array.from($('upload').files);changedDraft();mutation=true;busy(true);try{const cid=await ensureConversation();for(const file of files){if(file.size>12*1024)throw new Error('Each source must be at most 12 KiB.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());const source=await api('/api/conversations/'+cid+'/sources',{name:file.name,text});selected.add(source.id);}changedDraft();renderConversation(await api('/api/conversations/'+cid));}catch(e){notice(e.message);}finally{$('upload').value='';changedDraft();mutation=false;busy(false);}};
$('close-material').onclick=closeMaterial;
$('material-preview').addEventListener('cancel',closeMaterial);
$('close-preview').onclick=()=>$('report-preview').close();
function showLegacyReceipt() {
  busy(true);notice('An older unbound request record is saved in this tab. Check its receipt before sending anything else. It will not be replayed.');
  const recover=node('button','Check saved receipt');
  recover.onclick=async()=>{if(mutation)return;mutation=true;busy(true);try{
    const saved=JSON.parse(sessionStorage.getItem('pocketchat.pending-request.v1')||'null');
    if(!saved || !/^[a-zA-Z0-9_-]{3,80}$/.test(saved.cid) || !saved.body || typeof saved.body.request_id!=='string')throw new Error('The old request record cannot be verified.');
    const conversation=await api('/api/conversations/'+saved.cid);
    let receipt=null;
    for(const run of conversation.runs){
      const status=await api('/api/runs/'+run.id);
      if(status.request_id===saved.body.request_id){receipt=status;break;}
    }
    const wire={prompt:saved.body.prompt,intent:saved.body.intent,source_ids:saved.body.source_ids,revision_id:saved.body.revision_id};
    const actual=receipt?.request?.wire;
    if(!receipt || receipt.conversation_id!==saved.cid || !actual || actual.prompt!==wire.prompt || actual.intent!==wire.intent || actual.revision_id!==wire.revision_id || JSON.stringify(actual.source_ids)!==JSON.stringify(wire.source_ids))
      throw new Error('No matching saved receipt was found in this workspace. No request was sent.');
    sessionStorage.setItem(receiptKey,JSON.stringify(saved));sessionStorage.removeItem('pocketchat.pending-request.v1');
    legacyPending=false;saveDraft();current=conversation;restoreDraft();renderConversation(conversation);lastSubmission=saved;watch(receipt.id);
  }catch(e){showLegacyReceipt();$('notice').prepend(node('p',e.message));}finally{mutation=false;busy(false);}};
  const discard=node('button','Discard old tab record');
  discard.onclick=()=>{if(mutation)return;if(!window.confirm('Remove only this tab record? Check the old workspace first; an unknown request must not be sent again.'))return;sessionStorage.removeItem('pocketchat.pending-request.v1');legacyPending=false;notice('Old tab record removed. No request was sent.');busy(false);};
  $('notice').append(' ',recover,' ',discard);
}
async function refreshModels() {
  const result=await api('/api/models'), choice=$('model-choice');
  choice.replaceChildren();
  for(const model of result.items){
    const option=node('option',model.profile+' · '+model.model);option.value=model.profile;
    choice.append(option);
  }
  choice.value=result.selected || '';displayedModelRevision=result.revision ?? null;
  const selectedModel=result.items.find(model=>model.profile===result.selected);
  $('model').textContent=selectedModel?.model || 'No model selected';
  $('provider').textContent=selectedModel?.provider ? new URL(selectedModel.provider).origin : 'the configured provider';
}
$('model-choice').onchange=async()=>{
  if(editingBlocked())return;
  const profile=$('model-choice').value;changedDraft();mutation=true;busy(true);
  try{await api('/api/models',{profile});await refreshModels();notice('Model changed for new requests. Review and confirm before sending.');}
  catch(e){notice(e.message);await refreshModels().catch(()=>{});}
  finally{mutation=false;busy(false);}
};
async function boot() {
  const data=await api('/api/bootstrap'); csrf=data.csrf;
  receiptKey='pocketchat.pending-request.v2:'+data.workspace_id;
  draftPrefix='pocketchat.draft.v1:'+data.workspace_id+':browser:';
  viewKey=draftPrefix+'current';
  $('model').textContent=data.model || 'No model selected';
  $('provider').textContent=data.provider ? new URL(data.provider).origin : 'the configured provider';
  await refreshModels();
  $('upgrade').hidden=![2,3,4].includes(data.database_version);
  if($('project-settings'))await refreshProject().catch(()=>{}); $('connect').close(); await refreshList();
  let saved=null, lastView=null;
  try { saved=JSON.parse(sessionStorage.getItem(receiptKey)||'null');lastView=sessionStorage.getItem(viewKey); }
  catch { notice('Saved browser state could not be read. No message was sent.'); }
  legacyPending=Boolean(sessionStorage.getItem('pocketchat.pending-request.v1'));
  if(saved){lastSubmission=saved;current=await api('/api/conversations/'+saved.cid);restoreDraft();renderConversation(current);uncertainSubmission(new Error('A previous request needs receipt recovery.'));}
  else if(lastView==='new'){restoreDraft();busy(false);}
  else if(lastView && !legacyPending){await openConversation(lastView);}
  else if(conversations.length && !legacyPending)await openConversation(conversations[0].id);
  else {restoreDraft();busy(false);}
  if(legacyPending && !saved)showLegacyReceipt();
}
$('connect-button').onclick=async event=>{event.preventDefault();try{await api('/api/login',{token:$('token').value});$('token').value='';await boot();}catch(e){$('connect-error').textContent=e.message;}};
(async()=>{try{const token=location.hash.slice(1);if(token){history.replaceState(null,'',location.pathname);await api('/api/login',{token});}await boot();}catch(e){$('connect-error').textContent=e.message;$('connect').showModal();}})();
window.addEventListener('beforeunload',()=>{saveDraft();if(polling)clearTimeout(polling);clearTimeout(searchTimer);});
