'use strict';
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const paths = {
  people:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  chapters:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6',
  calendar:'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2',
};
function icon(name) {const s=document.createElementNS(NS,'svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width','1.65');s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');s.setAttribute('aria-hidden','true');const p=document.createElementNS(NS,'path');p.setAttribute('d',paths[name]||paths.chapters);s.append(p);return s;}
document.querySelectorAll('[data-icon]').forEach(n=>n.append(icon(n.dataset.icon)));
const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const sum=a=>a.reduce((s,v)=>s+v,0);
const total=events=>sum(events.map(e=>sum(e.tokens)));
const fmt=v=>v>=1e9?(v/1e9).toFixed(v<1e10?1:0)+'B':v>=1e6?(v/1e6).toFixed(v<1e8?1:0)+'M':v>=1e3?(v/1e3).toFixed(v<1e4?1:0)+'K':v.toLocaleString('en-GB');
const work=events=>sum(events.map(e=>e.tokens[0]+e.tokens[2]));
const cached=events=>sum(events.map(e=>e.tokens[1]));
const NOUNS={'Command calls':'commands','File reads':'file reads','File-edit calls':'edits','Helper launches':'helper launches','Clarification calls':'questions to you','Image-generation calls':'images generated','Image inspections':'images viewed','Web lookups':'web lookups','Other tool calls':'other calls'};
const SOURCES=[['Claude Code','claude'],['Codex','codex'],['Hermes','hermes']];
let sessionById={};
const sourceOf=id=>sessionById[id]?.provider||'Codex';
const bySource=events=>{const t=Object.fromEntries(SOURCES.map(([n])=>[n,0]));events.forEach(e=>{const p=sourceOf(e.session);t[p]=(t[p]||0)+e.tokens[0]+e.tokens[2];});return t;};
const sourceClass=name=>(SOURCES.find(([n])=>n===name)||[,'codex'])[1];
// The widget's own glowing provider logos, served by the dashboard.
const mark=(name,cls='mark')=>{const img=node('img',cls);img.src='/source/'+sourceClass(name)+'.png';img.alt=name;img.title=name;return img;};
const sessionTitle=s=>s?.title||(s?`${s.helper?'Helper':s.provider} · ${s.id.slice(0,6)}`:'Recorded work');
const exact=v=>v.toLocaleString('en-GB');
const day=at=>at.slice(0,10);
const date=(d,year=false)=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',...(year?{year:'numeric'}:{}),timeZone:'UTC'}).format(new Date(d+'T12:00:00Z'));
const addDays=(d,n)=>{const t=new Date(d+'T12:00:00Z');t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10);};
const plural=(n,s)=>`${n} ${s}${n===1?'':'s'}`;
const counts=items=>{const c={};items.forEach(a=>c[a.label]=(c[a.label]||0)+1);return Object.entries(c).sort((a,b)=>b[1]-a[1]);};
let data=null, project=(window.smithSession&&smithSession.project)||'', range='week', anchor=null, buckets=[], selected=null, loading=false, snapshotVersion=null;
let scopedEvents=[], scopedActions=[], periodStart='',periodEnd='';
function scope(){
  // All time ends at the last recorded day, not today: padding the story with
  // empty days after the work stopped read as a verdict rather than coverage.
  const lastRecorded=[...data.events,...data.actions,...data.commits].map(e=>day(e.at)).filter(d=>d<=data.today).sort().at(-1);
  periodEnd=range==='all'?(lastRecorded||data.today):anchor;
  periodStart=range==='all'?(data.first?day(data.first):data.today):range==='week'?addDays(anchor,-6):anchor;
  const inside=e=>day(e.at)>=periodStart&&day(e.at)<=periodEnd;
  scopedEvents=data.events.filter(inside);scopedActions=data.actions.filter(inside);
}
function buildBuckets(){
  scope();
  // One entry per main session; its helpers ride inside it.
  const ids=[...new Set([...scopedEvents,...scopedActions].map(e=>e.session))];
  const byId=sessionById=Object.fromEntries(data.sessions.map(s=>[s.id,s]));
  const roots=ids.filter(id=>!byId[id]?.helper||!ids.includes(byId[id].parent));
  buckets=roots.map(id=>{
    const session=byId[id],helpers=ids.filter(h=>byId[h]?.parent===id).map(h=>byId[h]);
    const members=new Set([id,...helpers.map(h=>h.id)]);
    const events=scopedEvents.filter(e=>members.has(e.session)),actions=scopedActions.filter(a=>members.has(a.session));
    const times=[...events,...actions].map(e=>e.at).sort();
    return {key:id,title:sessionTitle(session),start:times[0],end:times.at(-1),events,actions,sessions:[session,...helpers],helpers};
  }).filter(b=>b.events.length||b.actions.length).sort((a,b)=>a.start.localeCompare(b.start));
}
function renderSources(main,helperCount){
  const list=$('bySource');list.replaceChildren();const tokens=bySource(scopedEvents);
  SOURCES.forEach(([name])=>{const n=main.filter(s=>s.provider===name).length,li=node('li',n?'':'none');
    li.append(mark(name),node('span','source-name',name),node('span','source-count',plural(n,'session')));
    const t=node('strong','',tokens[name]?fmt(tokens[name]):'—');if(tokens[name])t.title=exact(tokens[name])+' new tokens';li.append(t);list.append(li);});
  if(helperCount)list.append(node('li','source-helpers',`+ ${plural(helperCount,'helper session')}, counted in the session that launched them`));
}
function setNumber(id,v){$(id).textContent=fmt(v);$(id).title=exact(v)+' tokens';}
const TONES=['lavender','sage','gold','peach'];let host_signature=null;
function renderShares(){
  // Every discovered project's traffic inside the selected period, largest first.
  const rows=(data.projects||[]).map(p=>({id:p.id,name:p.name,tokens:sum(Object.entries(p.days).filter(([d])=>d>=periodStart&&d<=periodEnd).map(([,t])=>t[0]+t[2]))})).filter(p=>p.tokens>0).sort((a,b)=>b.tokens-a.tokens);
  const signature=rows.map(r=>r.name+r.tokens).join('|'),fresh=host_signature!==signature;host_signature=signature;
  const all=sum(rows.map(p=>p.tokens)),shown=rows.slice(0,4),rest=rows.slice(4);
  if(rest.length)shown.push({name:plural(rest.length,'other project'),tokens:sum(rest.map(p=>p.tokens)),other:true});
  const host=$('projectShares');host.replaceChildren();
  shown.forEach((p,i)=>{
    const share=p.tokens/all*100,row=node(p.other?'div':'button','project-share'+(fresh?' arrive':''));
    if(fresh)row.style.animationDelay=(i*40)+'ms';
    row.dataset.tone=p.id===data.id?'lavender':TONES[1+(i%3)];
    if(p.id===data.id)row.setAttribute('aria-current','true');
    const squares=node('div','share-squares');squares.setAttribute('aria-hidden','true');
    const lit=Math.max(1,Math.round(share/100*32));
    for(let k=0;k<32;k++)squares.append(node('b',k<lit?'':'dim'));
    const text=node('div'),amount=node('strong','',fmt(p.tokens));amount.title=exact(p.tokens)+' tokens';
    text.append(node('span','',p.name),amount,node('span','lavender',share.toFixed(share<10?1:0)+'% of new tokens'));
    row.append(squares,text);
    if(!p.other){row.type='button';row.setAttribute('aria-label',`Open ${p.name}`);row.onclick=()=>selectProject(p.id);}
    host.append(row);
  });
  $('projectsNote').textContent=rows.length?`${plural(rows.length,'project')} with recorded traffic for ${periodLabel()}. ${plural(data.projects.length,'workspace')} found in local Codex, Claude Code and Hermes history.`:`No recorded traffic for ${periodLabel()}.`;
}
function fillProjects(list){
  const select=$('project');
  const signature=list.map(p=>p.id+':'+p.name).join('|');
  if(select.dataset.signature===signature)return;
  select.dataset.signature=signature;select.replaceChildren();
  list.forEach(p=>{const o=node('option','',p.sessions?`${p.name} · ${plural(p.sessions,'session')}`:p.name);o.value=p.id;select.append(o);});
}
function selectProject(id){if(!id||id===project||loading)return;project=id;selected=null;snapshotVersion=null;refresh(true);}
function periodLabel(){return periodStart===periodEnd?date(periodEnd,true):date(periodStart)+' – '+date(periodEnd,true);}
function render(){
  buildBuckets();$('dashboard').hidden=false;$('projectCoverage').hidden=false;
  const records=[...scopedEvents,...scopedActions],active=[...new Set(records.map(e=>e.session))].map(id=>sessionById[id]).filter(Boolean);
  const main=active.filter(s=>!s.helper),helperCount=active.length-main.length;
  setNumber('total',work(scopedEvents));setNumber('cachedTotal',cached(scopedEvents));$('projectName').textContent=data.project;renderShares();
  renderSources(main,helperCount);$('sessionCount').textContent=main.length;$('sessionUnit').textContent=main.length===1?'session':'sessions';$('chapterCount').textContent=new Set(records.map(e=>day(e.at))).size;
  $('historyStart').textContent=data.first?date(day(data.first),true):'No receipts';
  $('summaryDates').textContent=periodLabel();$('dates').textContent=periodStart===periodEnd?date(periodEnd):date(periodStart)+' – '+date(periodEnd);
  $('previous').disabled=range==='all'||!data.first||periodStart<=day(data.first);
  $('next').disabled=range==='all'||anchor>=data.today;
  $('previous').style.visibility=$('next').style.visibility=range==='all'?'hidden':'';
  document.querySelectorAll('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.range===range)));
  $('coverageLine').textContent=`${data.timezone} · ${(data.sources||[]).join(' and ')||'No'} receipts · Earlier or missing work is not reconstructed.`;
  const observed=new Date(data.generated).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
  $('updated').textContent='Read at '+observed+' · refreshes every 30s';
  renderTimeline();
}
let calendarDay=null;
function renderTimeline(){
 const host=$('chapters');host.replaceChildren();
 const days=[...new Set([...scopedEvents,...scopedActions].map(e=>day(e.at)))].sort();
 if(!days.length){host.append(node('li','empty-state','No recorded activity in this period.'));calendarDay=null;renderDay();return;}
 // Each day's receipts once, for its total, its shading and its source split.
 const byDay={};scopedEvents.forEach(e=>(byDay[day(e.at)]??=[]).push(e));
 const totals=Object.fromEntries(days.map(d=>[d,work(byDay[d]||[])]));
 const busiest=(list,start=list[0])=>list.reduce((best,d)=>totals[d]>totals[best]?d:best,start);
 if(!days.includes(calendarDay))calendarDay=busiest(days,days.at(-1));
 // One month at a time. The pager steps between months with recorded
 // activity, and a new month opens on its busiest day.
 const months=[...new Set(days.map(d=>d.slice(0,7)))];
 const month=calendarDay.slice(0,7),at=months.indexOf(month);
 const peak=Math.max(1,...days.filter(d=>d.startsWith(month)).map(d=>totals[d]));
 const turn=step=>{const next=months[at+step];if(!next)return;calendarDay=busiest(days.filter(d=>d.startsWith(next)));selected=null;renderTimeline();};
 {
  const section=node('li','cal-month'),head=node('div','cal-head');
  const back=node('button','cal-page','‹'),ahead=node('button','cal-page','›');
  back.setAttribute('aria-label','Previous month with activity');ahead.setAttribute('aria-label','Next month with activity');
  back.disabled=at<=0;ahead.disabled=at>=months.length-1;back.onclick=()=>turn(-1);ahead.onclick=()=>turn(1);
  head.append(node('h3','',new Date(month+'-01T12:00:00Z').toLocaleDateString('en-GB',{month:'long',year:'numeric',timeZone:'UTC'})));
  if(months.length>1){const pager=node('div','cal-pager');pager.append(back,node('span','cal-page-count',`${at+1} of ${months.length}`),ahead);head.append(pager);}
  section.append(head);
  const grid=node('div','cal-grid');['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(d=>grid.append(node('span','cal-weekday',d)));
  const start=new Date(month+'-01T12:00:00Z'),offset=(start.getUTCDay()+6)%7,last=new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+1,0)).getUTCDate();
  for(let i=0;i<offset;i++)grid.append(node('span'));
  for(let i=1;i<=last;i++){
   const key=month+'-'+String(i).padStart(2,'0'),active=days.includes(key),cell=node('button','cal-cell',String(i));cell.disabled=!active;cell.dataset.day=key;cell.setAttribute('aria-pressed',String(key===calendarDay));
   if(active){cell.style.background=`rgb(190 153 91 / ${.07+.25*totals[key]/peak})`;const split=bySource(byDay[key]||[]),bar=node('span','cal-sources');bar.setAttribute('aria-hidden','true');SOURCES.forEach(([name,cls])=>{if(split[name]){const seg=node('i','src-'+cls);seg.style.flex=String(split[name]);bar.append(seg);}});const marks=node('span','cal-marks');SOURCES.forEach(([name])=>{if(split[name])marks.append(mark(name,'mark tiny'));});cell.append(marks,node('strong','cal-tokens',fmt(totals[key])),bar,node('small','cal-sample','allowance not recorded'));cell.title=`${date(key)} · ${exact(totals[key])} new tokens\n`+SOURCES.filter(([n])=>split[n]).map(([n])=>`${n}: ${exact(split[n])}`).join('\n')+'\nNo account allowance reading was recorded for this day.';cell.setAttribute('aria-label',`${date(key)}, ${fmt(totals[key])} new tokens: `+SOURCES.filter(([n])=>split[n]).map(([n])=>`${n} ${fmt(split[n])}`).join(', '));}
   cell.onclick=()=>{calendarDay=key;selected=null;renderTimeline();};grid.append(cell);
  }
  section.append(grid);host.append(section);
 }
 renderDay();
}
function calendarBuckets(d){return buckets.map(b=>onDay(b,d)).filter(b=>b.events.length||b.actions.length).sort((a,b)=>b.work-a.work);}
// A session seen through one calendar day: only that day's receipts and calls.
function onDay(b,d){
  const events=b.events.filter(e=>day(e.at)===d),actions=b.actions.filter(a=>day(a.at)===d);
  const present=new Set([...events,...actions].map(e=>e.session));
  const view={...b,start:d+'T00:00:00',end:d+'T23:59:59',events,actions,helpers:b.helpers.filter(h=>present.has(h.id)),sessions:b.sessions.filter(s=>s&&present.has(s.id))};
  view.total=total(events);view.work=work(events);view.cached=cached(events);view.summary=summaryFor(view);
  return view;
}
function receiptList(events,cls){
  const values=[0,1,2].map(j=>sum(events.map(e=>e.tokens[j])));
  const dl=node('dl',cls);
  [['fresh','Fresh input',values[0]],['output','Output',values[2]],['cached','Cached context',values[1]]].forEach(([dot,label,v])=>{
    const row=node('div',dot==='cached'?'dim':'');const dt=node('dt');dt.append(node('i','dot '+dot),document.createTextNode(label));const dd=node('dd','',fmt(v));dd.title=exact(v)+' tokens';row.append(dt,dd);dl.append(row);});
  return dl;
}
function renderDay(){
  const panel=$('dayPanel');panel.replaceChildren();
  if(!calendarDay){panel.append(node('p','day-empty','Choose another period to see recorded sessions.'));return;}
  const list=calendarBuckets(calendarDay),events=scopedEvents.filter(e=>day(e.at)===calendarDay);
  const newTokens=work(events),reused=cached(events);
  const head=node('div','day-head');
  head.append(node('div','eyebrow',new Intl.DateTimeFormat('en-GB',{weekday:'long',day:'numeric',month:'long',timeZone:'UTC'}).format(new Date(calendarDay+'T12:00:00Z')).toUpperCase()));
  const big=node('div','day-total');const n=node('strong','',fmt(newTokens));n.title=exact(newTokens)+' tokens';big.append(n,node('span','','new tokens'));head.append(big);
  head.append(node('p','day-sub',`${plural(list.length,'session')} · ${fmt(reused)} cached context · allowance change not recorded`));
  if(events.some(e=>e.estimated))head.append(node('p','day-note','Hermes tokens are estimated: Hermes saves totals per session, split here by the days it replied.'));
  const fresh=sum(events.map(e=>e.tokens[0])),bar=node('div','day-split');bar.setAttribute('aria-hidden','true');
  const f=node('i','fresh');f.style.flex=String(fresh||0);const o=node('i','output');o.style.flex=String(newTokens-fresh||0);bar.append(f,o);
  head.append(bar,receiptList(events,'day-receipt'));panel.append(head);
  panel.append(node('h3','day-list-title','Sessions · highest usage first'));
  const ul=node('ul','day-sessions');
  list.forEach(b=>{
    const li=node('li'),open=b.key===selected,button=node('button','day-session');
    button.setAttribute('aria-expanded',String(open));
    const text=node('span','day-session-text');const started=[...b.events,...b.actions].map(e=>e.at).sort()[0];
    const provider=b.sessions[0]?.provider||'Codex',tag=mark(provider,'mark inline');const meta=node('small','',` ${started?started.slice(11,16):''}${b.helpers.length?' · '+plural(b.helpers.length,'helper'):''}${b.events.some(e=>e.estimated)?' · estimated':''}`);meta.prepend(tag);text.append(node('span','day-session-title',b.title),meta);
    const amount=node('strong','',fmt(b.work));amount.title=exact(b.work)+' new tokens';
    const share=node('span','day-share');share.setAttribute('aria-hidden','true');const fill=node('i');fill.style.width=(newTokens?b.work/newTokens*100:0).toFixed(1)+'%';share.append(fill);
    button.append(text,amount,share);button.onclick=()=>{selected=open?null:b.key;renderDay();};li.append(button);
    if(open){
      const detail=node('div','day-session-detail');
      detail.append(receiptList(b.events,'day-receipt small'));
      const acts=node('ul','day-activity');b.summary.highlights.forEach(h=>{const item=node('li');item.append(icon('chapters'),node('span','',h.title));acts.append(item);});detail.append(acts);
      if(b.helpers.length){const hl=node('ul','day-helpers');b.helpers.slice(0,5).forEach(h=>hl.append(node('li','',`${sessionTitle(h)} · ${fmt(work(b.events.filter(e=>e.session===h.id)))}`)));if(b.helpers.length>5)hl.append(node('li','',`+${b.helpers.length-5} more`));detail.append(hl);}
      const more=node('button','text-action','Open activity');more.append(node('span','','→'));more.onclick=()=>openActivity(b);detail.append(more);
      li.append(detail);
    }
    ul.append(li);
  });
  panel.append(ul);
}
function summaryFor(b){
  // A factual description of what the activity log recorded. The dashboard
  // does not summarize your sessions and makes no claim about outcomes.
  const highlights=counts(b.actions).filter(([label])=>label!=='Helper launches').slice(0,4).map(([label,n])=>({title:`${exact(n)} ${NOUNS[label]||label.toLowerCase()}`,body:`The activity log records ${exact(n)} ${NOUNS[label]||label.toLowerCase()} on this day. Their results stay in the original session.`}));
  const helpers=b.helpers.length;
  if(helpers)highlights.splice(Math.min(3,highlights.length),0,{title:plural(helpers,'helper session'),body:'Helper sessions were spawned by the main session; their tokens are included here.'});
  if(!highlights.length&&b.events.length)highlights.push({title:'AI activity was recorded',body:'Usage records are available, but the log does not describe what was produced.'});
  if(!highlights.length)highlights.push({title:'No activity summary available',body:'No session receipts were found in this period.'});
  return {highlights:highlights.slice(0,3)};
}
function dialog(title,eyebrow){$('dialogTitle').textContent=title;$('dialogEyebrow').textContent=eyebrow;$('dialogContent').replaceChildren();$('dialogContent').className='';if(!$('details').open)$('details').showModal();return $('dialogContent');}
function paragraph(parent,text,cls){parent.append(node('p',cls,text));}
function table(parent,headers,rows){const t=node('table'),head=node('thead'),hr=node('tr'),body=node('tbody');headers.forEach(s=>hr.append(node('th','',s)));head.append(hr);rows.forEach(row=>{const r=node('tr');row.forEach(s=>r.append(node('td','',s)));body.append(r);});t.append(head,body);parent.append(t);}
function disclosure(parent,label,id){const d=node('details','summary-disclosure');if(id)d.id=id;d.append(node('summary','',label));const body=node('div','disclosure-body');d.append(body);parent.append(d);return {details:d,body};}
function ledger(parent,rows){const dl=node('dl','plain-ledger');rows.forEach(([name,value])=>{const row=node('div');row.append(node('dt','',name),node('dd','',value));dl.append(row);});parent.append(dl);}
function openActivity(b){
  const content=dialog('What the records show','WHAT WE CAN SEE');
  content.classList.add('plain-summary');
  const meta=node('div','summary-meta');meta.append(node('span','',date(day(b.start),true)),node('span','',plural(b.sessions.length,'recorded session')),node('span','summary-provenance','Activity only'));content.append(meta);
  const list=node('ul','outcome-list');
  b.summary.highlights.forEach(item=>{const li=node('li');li.append(icon('chapters'));const text=node('div');text.append(node('strong','',item.title),node('p','',item.body));li.append(text);list.append(li);});content.append(list);
  paragraph(content,'This is a factual view of the activity log. It is not a summary of what the sessions produced.','summary-caveat');
  const values=[0,1,2].map(j=>sum(b.events.map(e=>e.tokens[j])));
  const usage=disclosure(content,'Explain the AI usage','summaryUsage');
  if(b.total){const share=values[1]/b.total*100;
    paragraph(usage.body,share>=50?`About ${share.toFixed(1)}% of the recorded traffic was material the AI had already seen and reused.`:share>0?`${share.toFixed(1)}% of the recorded traffic was reused material. The rest was new input and AI responses.`:'The available receipts contain no cached input. Recorded traffic is made up of fresh input and AI responses.');
    paragraph(usage.body,'Repeated material counts toward the total. The number describes traffic, not unique work produced, money spent, or subscription allowance.');
  }else paragraph(usage.body,'No token receipts were found in this period. That does not establish that no work happened.');
  const exactDetails=disclosure(usage.body,'Show exact numbers','summaryExact');
  ledger(exactDetails.body,[['New input',exact(values[0])],['Reused input',exact(values[1])],['AI output',exact(values[2])],['Total tokens',exact(b.total)]]);
  paragraph(exactDetails.body,plural(b.events.length,'recorded response receipt')+'. A token is a small piece of content processed by the model.');
  const evidence=disclosure(content,'What supports this summary?','summaryEvidence');
  paragraph(evidence.body,'The highlights above count recorded tool requests. They do not establish that a feature was finished or that checks passed, and no session content is read or summarized to produce them.');
  const sessions=disclosure(content,plural(b.sessions.length,'session')+' on this day','dialogSessions');
  b.sessions.forEach(s=>{const row=node('div','session-note');row.append(node('strong','',(s.helper?`${s.provider} helper · `:`${s.provider} · `)+sessionTitle(s)),node('span','',s.started?date(day(s.started))+' · '+s.started.slice(11,16):'Start not recorded'));const technical=disclosure(row,'Session details');ledger(technical.body,[['Session',s.id],['Model',s.model||'Not recorded'],['Tokens in this selection',exact(total(b.events.filter(e=>e.session===s.id)))]]);sessions.body.append(row);});
  const log=disclosure(sessions.body,'Recorded tool requests');if(b.actions.length)ledger(log.body,counts(b.actions).map(([l,c])=>[l,exact(c)]));else paragraph(log.body,'No tool requests were recorded in this selection.');
}
function coverage(){
  const c=dialog('What this preview can see','LOCAL RECORDING COVERAGE');
  paragraph(c,`Project history reads retained Codex and Claude Code transcripts and saved Hermes sessions for ${data.project} and Git commit metadata from that folder, on this computer only. Hermes saves running totals per session rather than per response, so its tokens are split across the days its replies were written and marked as estimated. Other computers and missing historical logs are outside this snapshot. Account limits live under Plan usage.`);
  const files=data.coverage.sourceFiles||{};table(c,['Source','Log files read'],Object.entries(files).map(([k,v])=>[k,String(v)]));
  table(c,['Coverage','Recorded'],[['History starts',data.first?date(day(data.first),true):'No receipts'],['Latest receipt',data.last?date(day(data.last),true)+' '+data.last.slice(11,16):'None'],['Sessions',exact(data.sessions.length)],['Response receipts',exact(data.events.length)],['Git milestones',exact(data.commits.length)],['Missing indexed log files',String(data.coverage.missingFiles||0)],['Excluded internal review sessions',String(data.coverage.excludedInternalSessions||0)],['Duplicate response records removed',String(data.coverage.duplicateResponses||0)],['Unreadable / invalid records',String(data.coverage.unreadableRecords||0)],['Undated counter tokens excluded',exact(data.coverage.undatedTokens||0)]]);
  paragraph(c,'All time means all available receipts for this project on this computer. A date with no receipts means no activity was recorded here; it does not prove that no work happened. Another computer can hold additional history.');
  paragraph(c,'The calendar shows new tokens per day. Choosing a day lists its sessions, with helper sessions counted inside the session that launched them. Session titles use each session’s own opening words.');
  paragraph(c,'Activity highlights count recorded tool requests. Nothing in your sessions is read, summarized or sent anywhere to produce them, and no model is called.');
  paragraph(c,'Token receipts are deduplicated by response identity. Fresh input = input minus cached input; output includes reasoning tokens. Cached input is included once in total recorded traffic. Response receipts can differ from account or session summary counters. No allowance percentage, API price or cost estimate is inferred.');
}
function tokenInfo(){const c=dialog('Recorded tokens, counted once','TOKEN RECEIPTS');paragraph(c,'Fresh input + cached input + output = recorded token traffic. Codex input already includes cached tokens, so cached input is subtracted before calculating fresh input. Reasoning is already included in output.');paragraph(c,'Claude Code reports input in three parts. Cache reads count as cached input; tokens written to the cache are new material and count as fresh input.');paragraph(c,'Repeated cached context can make traffic totals much larger than the project’s unique content. These counts are not subscription allowance, API cost, or a measure of completed work.');paragraph(c,'Display values are rounded. Open activity to see exact token counts. Project history uses response receipts, not account usage percentages.');}
async function refresh(manual=false){
  if(loading)return;loading=true;$('refresh').disabled=true;
  try{
    const incoming=await smithSession.api('/api/snapshot?project='+encodeURIComponent(project));
    if(!Array.isArray(incoming.events))throw Error(incoming.error||'Invalid local snapshot');
    project=incoming.id;fillProjects(incoming.projects||[]);$('project').value=project;
    if(!anchor||anchor===data?.today)anchor=incoming.today;
    if(data&&data.project!==incoming.project&&range!=='all'&&!incoming.events.some(e=>day(e.at)>=periodStart&&day(e.at)<=periodEnd)&&incoming.last)anchor=day(incoming.last);
    const changed=snapshotVersion!==incoming.version;data=incoming;snapshotVersion=incoming.version;
    if(changed)render();
    $('status').textContent='';
    if(manual&&!changed)$('updated').textContent='Up to date · shared 30s snapshot';
  }catch(error){$('status').textContent=data?'Refresh failed. Showing the last reading of your local history.':(error&&error.message)||'Local history is unavailable. Try Refresh, or reopen the dashboard from the widget.';}
  finally{loading=false;$('refresh').disabled=false;}
}
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{if(!data)return;range=b.dataset.range;selected=null;render();});
$('previous').onclick=()=>{anchor=addDays(anchor,range==='day'?-1:-7);selected=null;render();};
$('next').onclick=()=>{anchor=addDays(anchor,range==='day'?1:7);if(anchor>data.today)anchor=data.today;selected=null;render();};
$('coverageButton').onclick=coverage;
document.querySelectorAll('[data-info]').forEach(b=>b.onclick=tokenInfo);
$('closeDialog').onclick=()=>$('details').close();$('details').onclick=e=>{if(e.target===$('details')){const r=$('details').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('details').close();}};
$('refresh').onclick=()=>refresh(true);
$('project').onchange=e=>selectProject(e.target.value);
const planActive=()=>{const panel=$('planUsage');return !!panel&&!panel.hidden;};
const poll=()=>{if(!document.hidden&&!planActive())refresh();};
document.addEventListener('visibilitychange',poll);
setInterval(poll,30000);
refresh();
// Read-only state for validation; raw transcripts never enter the browser.
window.smithDashboard={refresh,get snapshot(){return data;},get buckets(){return buckets;},get selected(){return selected;},get range(){return range;},get day(){return calendarDay;},get events(){return scopedEvents;},get project(){return project;}};
