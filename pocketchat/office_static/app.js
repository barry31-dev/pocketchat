'use strict';
const $ = id => document.getElementById(id);
let csrf = '', current = null, selected = new Set(), report = false, revision = null, activeRun = null, polling = null;
let conversations = [], lastSubmission = null;
const receiptKey = 'pocketchat.pending-request.v1';
const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
function notice(message = '') { $('notice').textContent = message; }
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : {'Content-Type':'application/json','X-CSRF-Token':csrf}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { const e=new Error(data.error || 'Request failed.');e.status=response.status;throw e; }
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
function busy(value) { $('send').disabled = value; $('stop').hidden = !value; $('new-chat').disabled = value; $('upload').disabled = value; }
function setReport(value, old = null) { report = value; revision = old; $('report-toggle').setAttribute('aria-pressed', String(value)); $('revision-label').textContent = old ? 'Revising version ' + ((current?.artifacts.length || 0) - (current?.artifacts.findIndex(a=>a.id===old) ?? 0)) : ''; $('prompt').placeholder = value ? 'What should this report cover?' : 'Ask anything, or tell me what you need…'; }
async function refreshList() {
  conversations = (await api('/api/conversations')).items;
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
  current = c; $('rename').textContent = c.title; renderList();
  if (c.messages.length) {
    $('messages').replaceChildren();
    for (const m of c.messages) {
      const section = node('section', undefined, 'message ' + m.role); section.append(node('div', m.role === 'user' ? 'You' : 'PocketChat', 'role'));
      const text = node('div'); markdown(text, m.content); section.append(text);
      const copy = node('button','Copy','copy'); copy.onclick=()=>navigator.clipboard.writeText(m.content).then(()=>notice('Copied.')).catch(()=>notice('Copy unavailable. Select the text instead.'));
      section.append(copy); $('messages').append(section);
    }
  } else if (!$('messages').querySelector('.welcome')) { $('messages').replaceChildren(node('p','This is a fresh conversation. Start with a question or add some material.','empty-note')); }
  $('sources').replaceChildren();
  if (!c.sources.length) $('sources').append(node('p','Add notes to give your work a starting point.','empty-note'));
  for (const s of c.sources) {
    const label = node('label',undefined,'source'), checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=selected.has(s.id);
    checkbox.onchange=()=>{if(checkbox.checked)selected.add(s.id);else selected.delete(s.id);};
    const detail=node('span',s.name); detail.append(node('small',`${s.bytes} bytes · ${s.sha256.slice(0,10)}`)); label.append(checkbox,detail); $('sources').append(label);
  }
  $('artifacts').replaceChildren();
  if (!c.artifacts.length) $('artifacts').append(node('p','Finished reports appear here. Earlier versions stay yours.','empty-note'));
  for (const [index,a] of c.artifacts.entries()) {
    const card=node('div',undefined,'artifact');card.append(node('strong','Report · version '+(c.artifacts.length-index)),node('small',new Date(a.created*1000).toLocaleString()));
    const actions=node('div',undefined,'artifact-actions');
    for (const [label,action] of [['Preview',async()=>{const d=await api(`/api/conversations/${c.id}/artifacts/${a.id}/view`);markdown($('preview-content'),d.content);$('report-preview').showModal();}],['Revise',async()=>{setReport(true,a.id);$('prompt').focus();}],['Word',()=>download(c.id,a.id,'docx')],['Markdown',()=>download(c.id,a.id,'md')]]) {
      const b=node('button',label);b.onclick=()=>Promise.resolve(action()).catch(e=>notice(e.message));actions.append(b);
    }card.append(actions);$('artifacts').append(card);
  }
  $('messages').scrollTop=$('messages').scrollHeight;
}
async function download(cid,aid,format) {
  const response=await fetch(`/api/conversations/${cid}/artifacts/${aid}/${format}`);
  if(!response.ok)throw new Error((await response.json()).error);
  const url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download=`report-${aid.slice(0,8)}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function openConversation(cid) {
  if((activeRun||lastSubmission) && current?.id!==cid)throw new Error('Wait for this request to finish before switching conversations.');
  selected=new Set();setReport(false);const c=await api('/api/conversations/'+cid);renderConversation(c);
  const running=c.runs.find(r=>['running','stopping'].includes(r.status));
  if(running)watch(running.id);
  else if(c.runs[0] && ['failed','cancelled','unknown'].includes(c.runs[0].status))showFailure(await api('/api/runs/'+c.runs[0].id));
}
async function ensureConversation() {
  if(!current){const c=await api('/api/conversations',{title:'New conversation'});await refreshList();renderConversation(c);}return current.id;
}
function showFailure(r) {
  notice(r.error || 'The request did not finish.');const retry=node('button','Review & retry');
  retry.onclick=()=>{ $('prompt').value=r.request.prompt;selected=new Set(r.request.source_ids);setReport(r.request.intent==='report',r.request.revision_id);renderConversation(current);$('consent').checked=false;notice('Review the request, then send to retry.'); };$('notice').append(' ',retry);
}
function watch(rid) {
  activeRun=rid;busy(true);if(polling)clearTimeout(polling);
  const poll=async()=>{try{
    const r=await api('/api/runs/'+rid);$('pending').hidden=false;$('pending').textContent=r.status==='stopping'?'Stopping… waiting for the running request to exit.':'Working on your request…';
    let preview=$('stream-preview');if(!preview){preview=node('div',undefined,'message streaming');preview.id='stream-preview';$('messages').append(preview);}preview.textContent=r.preview || 'Thinking…';
    if(['running','stopping'].includes(r.status)){polling=setTimeout(poll,350);return;}
    activeRun=null;busy(false);$('pending').hidden=true;preview.remove();renderConversation(await api('/api/conversations/'+r.conversation_id));
    if(r.status==='completed' && r.request.intent==='report') { const saved=current.artifacts.find(a=>a.run_id===r.id);if(saved)setReport(true,saved.id); }
    lastSubmission=null;sessionStorage.removeItem(receiptKey);
    if(r.status!=='completed')showFailure(r);else notice('');await refreshList();
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
    const r=await api('/api/conversations/'+cid+'/runs',body);
    $('prompt').value='';$('consent').checked=false;notice();watch(r.id);
  }catch(e){
    if([400,403,404,413,415].includes(e.status)){lastSubmission=null;sessionStorage.removeItem(receiptKey);busy(false);notice(e.message);}
    else uncertainSubmission(e);
  }
}
$('composer').onsubmit=async event=>{event.preventDefault();notice();try{
  if(lastSubmission){await dispatchPending();return;}
  if(!$('consent').checked)throw new Error('Confirm sending this conversation and selected material to the provider.');
  const cid=await ensureConversation();
  const body={prompt:$('prompt').value,intent:report?'report':'chat',source_ids:[...selected],revision_id:revision,request_id:crypto.randomUUID(),consent:true};
  lastSubmission={cid,body};sessionStorage.setItem(receiptKey,JSON.stringify(lastSubmission));busy(true);await dispatchPending();
}catch(e){busy(false);notice(e.message);}};
$('stop').onclick=()=>api('/api/runs/'+activeRun+'/stop',{}).catch(e=>notice(e.message));
$('new-chat').onclick=()=>{if(activeRun||lastSubmission)return;current=null;selected=new Set();setReport(false);$('messages').replaceChildren();$('sources').replaceChildren();$('artifacts').replaceChildren();$('rename').textContent='New conversation';notice();renderList();$('prompt').focus();};
$('rename').onclick=async()=>{if(!current)return;const title=window.prompt('Conversation title',current.title);if(title===null)return;try{renderConversation(await api('/api/conversations/'+current.id+'/rename',{title}));await refreshList();}catch(e){notice(e.message);}};
$('search').oninput=renderList;$('report-toggle').onclick=()=>setReport(!report);
$('weekly').onclick=()=>{setReport(true);$('prompt').value='Create a concise weekly report from the selected material. Include progress, risks and next steps, with source references.';$('prompt').focus();};
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{$('prompt').value=b.dataset.prompt;$('prompt').focus();};
$('upload').onchange=async()=>{try{const cid=await ensureConversation();for(const file of $('upload').files){if(file.size>12*1024)throw new Error('Each source must be at most 12 KiB.');const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());const s=await api('/api/conversations/'+cid+'/sources',{name:file.name,text});selected.add(s.id);}renderConversation(await api('/api/conversations/'+cid));}catch(e){notice(e.message);}finally{$('upload').value='';}};
$('close-preview').onclick=()=>$('report-preview').close();
async function boot() {const data=await api('/api/bootstrap');csrf=data.csrf;$('model').textContent=data.model;$('provider').textContent=new URL(data.provider).origin;$('connect').close();await refreshList();
  const saved=sessionStorage.getItem(receiptKey);
  if(saved){lastSubmission=JSON.parse(saved);renderConversation(await api('/api/conversations/'+lastSubmission.cid));uncertainSubmission(new Error('A previous request needs receipt recovery.'));}
  else if(conversations.length)await openConversation(conversations[0].id);
}
$('connect-button').onclick=async event=>{event.preventDefault();try{await api('/api/login',{token:$('token').value});$('token').value='';await boot();}catch(e){$('connect-error').textContent=e.message;}};
(async()=>{try{const token=location.hash.slice(1);if(token){history.replaceState(null,'',location.pathname);await api('/api/login',{token});}await boot();}catch(e){$('connect-error').textContent=e.message;$('connect').showModal();}})();
window.addEventListener('beforeunload',()=>{if(polling)clearTimeout(polling);});
