'use strict';
const $ = id => document.getElementById(id);
let csrf = '', current = null, selected = new Set(), report = false, revision = null, activeRun = null, polling = null;
let conversations = [], lastSubmission = null, displayedModelRevision = null;
let receiptKey = '', draftPrefix = '', viewKey = '', draftReady = false, opening = false, searchSequence = 0;
let searchTimer = null, mutation = false, legacyPending = false;
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
  for (const id of ['send','upload','prompt','consent','report-toggle']) $(id).disabled = readOnly;
  $('stop').hidden = !value || !activeRun; $('new-chat').disabled = value;
  $('archive').disabled = value; $('rename').disabled = readOnly;
  for (const input of $('sources').querySelectorAll('input')) input.disabled = readOnly;
  const weekly = $('weekly'); if(weekly)weekly.disabled = readOnly;
  for (const button of document.querySelectorAll('[data-prompt]')) button.disabled = readOnly;
}
function draftValue() { return {prompt:$('prompt').value, report, revision, sources:[...selected]}; }
function saveDraft() {
  if (!draftPrefix || !draftReady) return;
  try { sessionStorage.setItem(draftPrefix + (current?.id || 'new'), JSON.stringify(draftValue())); }
  catch { notice('Draft could not be saved in this browser. Copy it before leaving.'); }
}
function restoreDraft() {
  let draft = null;
  try { draft = JSON.parse(sessionStorage.getItem(draftPrefix + (current?.id || 'new')) || 'null'); }
  catch { notice('Saved draft could not be read; no message was sent.'); }
  if (!draft || typeof draft.prompt !== 'string' || draft.prompt.length > 12000 ||
      typeof draft.report !== 'boolean' || !Array.isArray(draft.sources)) draft = {prompt:'',report:false,revision:null,sources:[]};
  $('prompt').value = draft.prompt;
  selected = new Set(draft.sources.filter(id => current?.sources.some(source => source.id === id)));
  setReport(draft.report, current?.artifacts.some(a => a.id === draft.revision) ? draft.revision : null);
  $('consent').checked = false; draftReady = true;
  try { sessionStorage.setItem(viewKey, current?.id || 'new'); } catch { /* Draft save reports unavailable storage. */ }
}
function changedDraft() { $('consent').checked = false; saveDraft(); }
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
        message.classList.add('search-hit'); message.focus(); message.scrollIntoView({block:'center'});
      } catch(e) { notice(e.message); } };
      $('search-results').append(button);
    }
  } catch(e) { if (sequence === searchSequence) notice(e.message); }
}
function setReport(value, old = null) { report = value; revision = old; $('report-toggle').setAttribute('aria-pressed', String(value)); $('revision-label').textContent = old ? 'Revising version ' + ((current?.artifacts.length || 0) - (current?.artifacts.findIndex(a=>a.id===old) ?? 0)) : ''; $('prompt').placeholder = value ? 'What should this report cover?' : 'Ask anything, or tell me what you need…'; }
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
    for (const m of c.messages) {
      const section = node('section', undefined, 'message ' + m.role); section.dataset.messageId=String(m.id); section.tabIndex=-1; section.append(node('div', m.role === 'user' ? 'You' : 'PocketChat', 'role'));
      const text = node('div'); markdown(text, m.content); section.append(text);
      const copy = node('button','Copy','copy'); copy.onclick=()=>navigator.clipboard.writeText(m.content).then(()=>notice('Copied.')).catch(()=>notice('Copy unavailable. Select the text instead.'));
      section.append(copy); $('messages').append(section);
    }
  } else if (!$('messages').querySelector('.welcome')) { $('messages').replaceChildren(node('p','This is a fresh conversation. Start with a question or add some material.','empty-note')); }
  $('sources').replaceChildren();
  if (!c.sources.length) $('sources').append(node('p','Add notes to give your work a starting point.','empty-note'));
  for (const s of c.sources) {
    const label = node('label',undefined,'source'), checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=selected.has(s.id);
    checkbox.onchange=()=>{if(checkbox.checked)selected.add(s.id);else selected.delete(s.id);changedDraft();};
    const detail=node('span',s.name); detail.append(node('small',`${s.bytes} bytes · ${s.sha256.slice(0,10)}`)); label.append(checkbox,detail); $('sources').append(label);
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
async function openConversation(cid) {
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
  retry.onclick=()=>{ if(editingBlocked())return; $('prompt').value=r.request.prompt;selected=new Set(r.request.source_ids);setReport(r.request.intent==='report',r.request.revision_id);renderConversation(current);changedDraft();notice('Review the request, then send to retry.'); };$('notice').append(' ',retry);
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
    if(current?.id===cid && $('prompt').value===body.prompt){$('prompt').value='';saveDraft();}
    $('consent').checked=false;notice();watch(r.id);
  }catch(e){
    if(e.requestNotAccepted || [400,403,404,413,415].includes(e.status)){lastSubmission=null;sessionStorage.removeItem(receiptKey);$('consent').checked=false;busy(false);notice(e.message);}
    else uncertainSubmission(e);
  }
}
$('composer').onsubmit=async event=>{event.preventDefault();try{
  if(mutation||opening||legacyPending||activeRun)return;
  notice();
  if(lastSubmission){await dispatchPending();return;}
  if(!$('consent').checked)throw new Error('Confirm sending this conversation and selected material to the provider.');
  if(current?.archived)throw new Error('Restore this conversation before sending.');
  // Capture every content/consent dependency before creating a conversation.
  const body={prompt:$('prompt').value,intent:report?'report':'chat',source_ids:[...selected],revision_id:revision,request_id:crypto.randomUUID(),consent:true};
  const displayedModel=$('model').textContent, displayedProvider=$('provider').textContent, displayedProfile=$('model-choice').value, modelRevision=displayedModelRevision;
  mutation=true;busy(true);
  const cid=await ensureConversation();
  if(!$('consent').checked || body.prompt!==$('prompt').value || body.intent!==(report?'report':'chat') || body.revision_id!==revision || JSON.stringify(body.source_ids)!==JSON.stringify([...selected]))
    throw new Error('The draft changed. Review and confirm it again.');
  const preview=await api('/api/conversations/'+cid+'/preview',body);
  if((preview.model_revision!==undefined && modelRevision!==preview.model_revision) || (preview.profile!==undefined && preview.profile!==displayedProfile) || preview.model!==displayedModel || new URL(preview.provider).origin!==displayedProvider){$('consent').checked=false;await refreshModels();throw new Error('Model changed in another client. Review and confirm again.');}
  if(!$('consent').checked || body.prompt!==$('prompt').value || body.intent!==(report?'report':'chat') || body.revision_id!==revision || JSON.stringify(body.source_ids)!==JSON.stringify([...selected]))throw new Error('The draft changed. Review and confirm it again.');
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
$('upgrade').onclick=async()=>{if(editingBlocked())return;mutation=true;busy(true);try{const result=await api('/api/upgrade',{});$('upgrade').hidden=true;notice(result.upgraded?'Workspace upgraded. A verified backup was saved in the private workspace.':'Workspace is already current.');}catch(e){notice(e.message);}finally{mutation=false;busy(false);}};
$('prompt').oninput=changedDraft;
$('rename').onclick=async()=>{if(!current||editingBlocked()||current.archived)return;const title=window.prompt('Conversation title',current.title);if(title===null)return;mutation=true;busy(true);try{renderConversation(await api('/api/conversations/'+current.id+'/rename',{title}));await refreshList();}catch(e){notice(e.message);}finally{mutation=false;busy(false);}};
$('search').oninput=()=>{++searchSequence;clearTimeout(searchTimer);searchTimer=setTimeout(searchMessages,160);};$('report-toggle').onclick=()=>{if(editingBlocked()||current?.archived)return;setReport(!report);changedDraft();};
$('weekly').onclick=()=>{if(editingBlocked()||current?.archived)return;setReport(true);$('prompt').value='Create a concise weekly report from the selected material. Include progress, risks and next steps, with source references.';changedDraft();$('prompt').focus();};
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{if(editingBlocked()||current?.archived)return;$('prompt').value=b.dataset.prompt;changedDraft();$('prompt').focus();};
$('upload').onchange=async()=>{if(editingBlocked()||current?.archived)return;const files=Array.from($('upload').files);changedDraft();mutation=true;busy(true);try{const cid=await ensureConversation();for(const file of files){if(file.size>12*1024)throw new Error('Each source must be at most 12 KiB.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());const source=await api('/api/conversations/'+cid+'/sources',{name:file.name,text});selected.add(source.id);}changedDraft();renderConversation(await api('/api/conversations/'+cid));}catch(e){notice(e.message);}finally{$('upload').value='';changedDraft();mutation=false;busy(false);}};
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
  $('upgrade').hidden=data.database_version!==2; $('connect').close(); await refreshList();
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
