'use strict';
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const paths = {
  people:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  chapters:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6',
  calendar:'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2',
  code:'m8 5-6 7 6 7m8-14 6 7-6 7M14 3l-4 18',
  edit:'m16 3 5 5M3 21l5-1L21 7a2.1 2.1 0 0 0-5-5L3 15Z',
  search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  check:'m5 12 4 4L19 6M21 12a9 9 0 1 1-9-9',
  tokens:'M20 5c0 2-4 3-8 3S4 7 4 5s4-3 8-3 8 1 8 3ZM4 5v14c0 2 4 3 8 3s8-1 8-3V5M4 12c0 2 4 3 8 3s8-1 8-3',
  image:'M3 3h18v18H3ZM3 17l6-6 4 4 3-3 5 5M9 7h.01',
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
const sessionTitle=s=>s?.title||(s?`${s.helper?'Helper':s.provider} · ${s.id.slice(0,6)}`:'Recorded work');
const exact=v=>v.toLocaleString('en-GB');
const day=at=>at.slice(0,10);
const date=(d,year=false)=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',...(year?{year:'numeric'}:{}),timeZone:'UTC'}).format(new Date(d+'T12:00:00Z'));
const addDays=(d,n)=>{const t=new Date(d+'T12:00:00Z');t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10);};
const plural=(n,s)=>`${n} ${s}${n===1?'':'s'}`;
const counts=items=>{const c={};items.forEach(a=>c[a.label]=(c[a.label]||0)+1);return Object.entries(c).sort((a,b)=>b[1]-a[1]);};
let data=null, project=(window.smithSession&&smithSession.project)||'', range='week', mode='sessions', anchor=null, buckets=[], selected=null, loading=false, snapshotVersion=null;
let scopedEvents=[], scopedActions=[], scopedCommits=[], periodStart='',periodEnd='';
function scope(){
  // All time ends at the last recorded day, not today: padding the story with
  // empty days after the work stopped read as a verdict rather than coverage.
  const lastRecorded=[...data.events,...data.actions,...data.commits].map(e=>day(e.at)).filter(d=>d<=data.today).sort().at(-1);
  periodEnd=range==='all'?(lastRecorded||data.today):anchor;
  periodStart=range==='all'?(data.first?day(data.first):data.today):range==='week'?addDays(anchor,-6):anchor;
  const inside=e=>day(e.at)>=periodStart&&day(e.at)<=periodEnd;
  scopedEvents=data.events.filter(inside);scopedActions=data.actions.filter(inside);scopedCommits=data.commits.filter(inside);
}
function buildBuckets(){
  scope();const existing=selected;
  if(mode==='sessions'){
    // One card per main session; its helpers ride inside it.
    const ids=[...new Set([...scopedEvents,...scopedActions].map(e=>e.session))];
    const byId=Object.fromEntries(data.sessions.map(s=>[s.id,s]));
    const roots=ids.filter(id=>!byId[id]?.helper||!ids.includes(byId[id].parent));
    buckets=roots.map(id=>{
      const session=byId[id],helpers=ids.filter(h=>byId[h]?.parent===id).map(h=>byId[h]);
      const members=new Set([id,...helpers.map(h=>h.id)]);
      const events=scopedEvents.filter(e=>members.has(e.session)),actions=scopedActions.filter(a=>members.has(a.session));
      const times=[...events,...actions].map(e=>e.at).sort();
      return {key:id,title:sessionTitle(session),start:times[0],end:times.at(-1),events,actions,commits:[],sessions:[session,...helpers],helpers,icon:session?.helper?'people':'code'};
    }).sort((a,b)=>a.start.localeCompare(b.start));
  }else{
    const groups=[];
    if(range==='day'){
      for(let h=0;h<24;h++){
        const key=periodStart+'T'+String(h).padStart(2,'0');
        if([...scopedEvents,...scopedActions,...scopedCommits].some(e=>e.at.startsWith(key)))groups.push({key,start:key+':00:00',end:key+':59:59',label:String(h).padStart(2,'0')+':00'});
      }
    }else{
      const days=Math.round((new Date(periodEnd)-new Date(periodStart))/864e5)+1,step=days>45?7:1;
      for(let d=periodStart;d<=periodEnd;d=addDays(d,step))groups.push({key:d,start:d+'T00:00:00',end:(addDays(d,step-1)>periodEnd?periodEnd:addDays(d,step-1))+'T23:59:59',label:step===1?date(d):date(d)+' – '+date(addDays(d,step-1)>periodEnd?periodEnd:addDays(d,step-1))});
    }
    buckets=groups.map(g=>{
      const inside=e=>e.at.slice(0,19)>=g.start&&e.at.slice(0,19)<=g.end;
      const events=scopedEvents.filter(inside),actions=scopedActions.filter(inside),commits=scopedCommits.filter(inside).sort((a,b)=>b.at.localeCompare(a.at));
      const ids=new Set([...events,...actions].map(e=>e.session));
      const title=commits[0]?.title||(actions.length?'Recorded project work':events.length?'Recorded token traffic':'No recorded activity');
      const glyph=commits.length?'edit':counts(actions)[0]?.[0]==='Image-generation calls'?'image':'code';
      return {...g,title,events,actions,commits,sessions:data.sessions.filter(s=>ids.has(s.id)),icon:glyph};
    });
  }
  // Only chapters with recorded session activity. A commit alone is a
  // milestone without a session behind it here, and its card would only
  // say "No receipts".
  buckets=buckets.filter(b=>b.events.length||b.actions.length);
  buckets.forEach(b=>{
    b.total=total(b.events);b.work=work(b.events);b.cached=cached(b.events);b.summary=summaryFor(b);
    if(mode!=='sessions'){
      // Commits label the receipt, not the card: the card gets the session's own words.
      const named=b.sessions.filter(s=>s?.title&&!s.helper).map(s=>({s,w:work(b.events.filter(e=>e.session===s.id))})).sort((a,z)=>z.w-a.w);
      b.title=named[0]?named[0].s.title:b.title;
      b.helpers=b.sessions.filter(s=>s?.helper);
    }
  });
  selected=buckets.some(b=>b.key===existing)?existing:buckets.reduce((best,b)=>!best||b.total>best.total?b:best,null)?.key;
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
    const text=node('div');text.append(node('span','',p.name),node('strong','',fmt(p.tokens)),node('span','lavender',share.toFixed(share<10?1:0)+'% of new tokens'));
    text.querySelector('strong').title=exact(p.tokens)+' tokens';
    row.append(squares,text);
    if(!p.other){row.type='button';row.setAttribute('aria-label',`Open ${p.name}`);row.onclick=()=>selectProject(p.id);}
    host.append(row);
  });
  $('projectsNote').textContent=rows.length?`${plural(rows.length,'project')} with recorded traffic for ${periodLabel()}. ${plural(data.projects.length,'workspace')} found in local Codex and Claude Code history.`:`No recorded traffic for ${periodLabel()}.`;
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
  const sessions=new Set([...scopedEvents,...scopedActions].map(e=>e.session));
  setNumber('total',work(scopedEvents));setNumber('cachedTotal',cached(scopedEvents));$('projectName').textContent=data.project;renderShares();
  $('sessionCount').textContent=sessions.size;$('chapterCount').textContent=buckets.filter(b=>b.events.length||b.actions.length).length;
  $('chapterUnit').textContent=mode==='sessions'?'session chapters':range==='day'?'recorded hours':buckets.some(b=>day(b.start)!==day(b.end))?'recorded weeks':'recorded days';
  $('historyStart').textContent=data.first?date(day(data.first),true):'No receipts';
  $('summaryDates').textContent=periodLabel();$('dates').textContent=periodStart===periodEnd?date(periodEnd):date(periodStart)+' – '+date(periodEnd);
  $('previous').disabled=range==='all'||!data.first||periodStart<=day(data.first);
  $('next').disabled=range==='all'||anchor>=data.today;
  $('previous').style.visibility=$('next').style.visibility=range==='all'?'hidden':'';
  document.querySelectorAll('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.range===range)));
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
  $('storyHint').textContent=mode==='sessions'?'Select a session to inspect its recorded activity.':'Select a chapter to explore the work.';
  $('chapterExplanation').textContent=mode==='sessions'?'Sessions with recorded activity':'Chapters follow time · summaries and saved project changes';
  $('coverageLine').textContent=`${data.timezone} · ${(data.sources||[]).join(' and ')||'No'} receipts · Earlier or missing work is not reconstructed.`;
  const observed=new Date(data.generated).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
  $('updated').textContent='Read at '+observed+' · refreshes every 30s';
  renderTimeline();renderReceipt();
}
let calendarDay=null;
function renderTimeline(){
 const host=$('chapters');host.replaceChildren();$('connections').replaceChildren();
 $('timeline').classList.add('compact-calendar');$('timeline').style.width='100%';
 $('timelineScroll').setAttribute('aria-label','Activity calendar');
 $('storyHint').textContent='Choose a day to explore its sessions and usage.';
 $('chapterExplanation').textContent='New tokens · Darker days used more';
 $('timelineHint').textContent='Allowance change is not recorded per day';
 const records=[...scopedEvents,...scopedActions],days=[...new Set(records.map(e=>day(e.at)))].sort();
 if(!days.length){host.append(node('li','empty-state','No recorded activity in this period.'));return;}
 const chosen=buckets.find(b=>b.key===selected);
 if(!days.includes(calendarDay))calendarDay=chosen?day(chosen.start):days.at(-1);
 const totals=Object.fromEntries(days.map(d=>[d,work(scopedEvents.filter(e=>day(e.at)===d))]));
 const peak=Math.max(1,...Object.values(totals));
 const months=[...new Set(days.map(d=>d.slice(0,7)))].reverse();
 months.forEach(month=>{
  const section=node('li','cal-month');section.append(node('h3','',new Date(month+'-01T12:00:00Z').toLocaleDateString('en-GB',{month:'long',year:'numeric',timeZone:'UTC'})));
  const grid=node('div','cal-grid');['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(d=>grid.append(node('span','cal-weekday',d)));
  const start=new Date(month+'-01T12:00:00Z'),offset=(start.getUTCDay()+6)%7,last=new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+1,0)).getUTCDate();
  for(let i=0;i<offset;i++)grid.append(node('span'));
  for(let i=1;i<=last;i++){
   const key=month+'-'+String(i).padStart(2,'0'),active=days.includes(key),cell=node('button','cal-cell',String(i));cell.disabled=!active;cell.dataset.day=key;cell.setAttribute('aria-pressed',String(key===calendarDay));
   if(active){cell.style.background=`rgb(190 153 91 / ${.07+.25*totals[key]/peak})`;cell.append(node('strong','cal-tokens',fmt(totals[key])),node('span','cal-allowance','—'),node('small','cal-sample','allowance not recorded'));cell.title=`${date(key)} · ${exact(totals[key])} new tokens. No account allowance reading was recorded for this day, so no change is shown.`;}
   cell.onclick=()=>{calendarDay=key;const matches=calendarBuckets(key);if(matches.length)selected=matches[0].key;renderTimeline();renderReceipt();};grid.append(cell);
  }
  section.append(grid);host.append(section);
 });
 const list=node('li','cal-sessions');list.append(node('h3','',date(calendarDay)+' · Highest usage first'));
 calendarBuckets(calendarDay).forEach(b=>{const button=node('button','cal-session');button.setAttribute('aria-pressed',String(b.key===selected));button.append(node('span','',b.title),node('strong','',fmt(work(b.events.filter(e=>day(e.at)===calendarDay)))+' tokens'));button.onclick=()=>select(b.key);list.append(button);});host.append(list);
}
function calendarBuckets(d){return buckets.filter(b=>[...b.events,...b.actions].some(e=>day(e.at)===d)).sort((a,b)=>work(b.events.filter(e=>day(e.at)===d))-work(a.events.filter(e=>day(e.at)===d)));}
function select(key){selected=key;renderTimeline();renderReceipt();}
function current(){return buckets.find(b=>b.key===selected);}
function summaryFor(b){
  // A factual description of what the activity log recorded. The dashboard
  // does not summarize your sessions and makes no claim about outcomes.
  const highlights=counts(b.actions).filter(([label])=>label!=='Helper launches').slice(0,4).map(([label,n])=>({title:`${exact(n)} ${NOUNS[label]||label.toLowerCase()}`,body:`The activity log records ${exact(n)} ${NOUNS[label]||label.toLowerCase()} in this chapter. Their results stay in the original session.`}));
  const helpers=(b.helpers||b.sessions.filter(s=>s?.helper)).length;
  if(helpers)highlights.splice(Math.min(3,highlights.length),0,{title:plural(helpers,'helper session'),body:'Helper sessions were spawned by the main session; their tokens are included here.'});
  if(!highlights.length&&b.events.length)highlights.push({title:'AI activity was recorded',body:'Usage records are available, but the log does not describe what was produced.'});
  if(!highlights.length)highlights.push({title:'No activity summary available',body:b.commits.length?'Saved project changes are available in the source notes. There are no matching session receipts for this chapter.':'No session receipts were found in this period.'});
  return {highlights:highlights.slice(0,3),extra:[],evidence:[]};
}
function renderReceipt(){
  const b=current();$('openActivity').disabled=!b;
  $('selectedLabel').textContent=mode==='sessions'?'SELECTED SESSION':range==='day'?'SELECTED HOUR':'SELECTED CHAPTER';
  $('selectedTitle').textContent=b?.title||'No recorded activity';
  $('selectedDate').textContent=b?(day(b.start)===day(b.end)?date(day(b.start),true):date(day(b.start))+' – '+date(day(b.end),true))+(mode==='sessions'?' · '+(b.sessions[0]?.provider||''):''):'Choose another period';
  const commits=b?.commits||[];$('selectedCommits').hidden=!commits.length;
  $('selectedCommits').textContent=commits.length?`Saved: ${commits[0].title}${commits.length>1?` · +${commits.length-1} more`:''}`:'';
  const values=[0,1,2].map(j=>sum((b?.events||[]).map(e=>e.tokens[j])));
  ['fresh','cached','output'].forEach((id,i)=>setNumber(id,values[i]));setNumber('selectedTotal',values[0]+values[2]);setNumber('receiptTotal',values[0]+values[2]);setNumber('receiptTraffic',sum(values));
  $('selectedCached').textContent=values[1]?fmt(values[1])+' cached context':'';
  $('activity').replaceChildren();
  $('activity').classList.add('plain-activity');
  (b?.summary.highlights||[{title:'No recorded activity'}]).forEach(item=>{const li=node('li');li.append(icon('chapters'),node('span','',item.title));$('activity').append(li);});
  if(b?.helpers?.length){const li=node('li','helper-list');const ul=node('ul');b.helpers.slice(0,5).forEach(h=>{const w=work(b.events.filter(e=>e.session===h.id));ul.append(node('li','',`${sessionTitle(h)} · ${fmt(w)}`));});if(b.helpers.length>5)ul.append(node('li','',`+${b.helpers.length-5} more`));li.append(ul);$('activity').append(li);}
  document.querySelector('.activity-bottom small').textContent='Observed activity · outcome not reviewed';
}
function dialog(title,eyebrow){$('dialogTitle').textContent=title;$('dialogEyebrow').textContent=eyebrow;$('dialogContent').replaceChildren();$('dialogContent').className='';if(!$('details').open)$('details').showModal();return $('dialogContent');}
function paragraph(parent,text,cls){parent.append(node('p',cls,text));}
function table(parent,headers,rows){const t=node('table'),head=node('thead'),hr=node('tr'),body=node('tbody');headers.forEach(s=>hr.append(node('th','',s)));head.append(hr);rows.forEach(row=>{const r=node('tr');row.forEach(s=>r.append(node('td','',s)));body.append(r);});t.append(head,body);parent.append(t);}
function disclosure(parent,label,id){const d=node('details','summary-disclosure');if(id)d.id=id;d.append(node('summary','',label));const body=node('div','disclosure-body');d.append(body);parent.append(d);return {details:d,body};}
function ledger(parent,rows){const dl=node('dl','plain-ledger');rows.forEach(([name,value])=>{const row=node('div');row.append(node('dt','',name),node('dd','',value));dl.append(row);});parent.append(dl);}
function openActivity(section){
  const b=current();if(!b)return;
  const summary=b.summary,content=dialog('What the records show','WHAT WE CAN SEE');
  content.classList.add('plain-summary');
  const meta=node('div','summary-meta');meta.append(node('span','',date(day(b.start),true)),node('span','',plural(b.sessions.length,'recorded session')),node('span','summary-provenance','Activity only'));content.append(meta);
  const list=node('ul','outcome-list');
  summary.highlights.forEach(item=>{const li=node('li');li.append(icon('chapters'));const text=node('div');text.append(node('strong','',item.title),node('p','',item.body));li.append(text);list.append(li);});content.append(list);
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
  if(b.commits.length){evidence.body.append(node('h3','','Saved project changes in this period'));const ul=node('ul');b.commits.forEach(c=>ul.append(node('li','',`${date(day(c.at))} · ${c.title}`)));evidence.body.append(ul);paragraph(evidence.body,'These changes provide context for the time period. Token usage is not assigned to a particular change.');}
  const sessions=disclosure(content,plural(b.sessions.length,'session')+' in this chapter','dialogSessions');
  b.sessions.forEach(s=>{const row=node('div','session-note');row.append(node('strong','',(s.helper?`${s.provider} helper · `:`${s.provider} · `)+sessionTitle(s)),node('span','',s.started?date(day(s.started))+' · '+s.started.slice(11,16):'Start not recorded'));const technical=disclosure(row,'Session details');ledger(technical.body,[['Session',s.id],['Model',s.model||'Not recorded'],['Tokens in this selection',exact(total(b.events.filter(e=>e.session===s.id)))]]);sessions.body.append(row);});
  const log=disclosure(sessions.body,'Recorded tool requests');if(b.actions.length)ledger(log.body,counts(b.actions).map(([l,c])=>[l,exact(c)]));else paragraph(log.body,'No tool requests were recorded in this selection.');
  if(section==='commits'){evidence.details.open=true;evidence.details.scrollIntoView({block:'start'});}
  if(section==='sessions'){sessions.details.open=true;sessions.details.scrollIntoView({block:'start'});}
}
function coverage(){
  const c=dialog('What this preview can see','LOCAL RECORDING COVERAGE');
  paragraph(c,`Project history reads retained Codex and Claude Code transcripts for ${data.project} and Git commit metadata from that folder, on this computer only. Hermes, other computers and missing historical logs are outside this snapshot. Account limits live under Plan usage.`);
  const files=data.coverage.sourceFiles||{};table(c,['Source','Log files read'],Object.entries(files).map(([k,v])=>[k,String(v)]));
  table(c,['Coverage','Recorded'],[['History starts',data.first?date(day(data.first),true):'No receipts'],['Latest receipt',data.last?date(day(data.last),true)+' '+data.last.slice(11,16):'None'],['Sessions',exact(data.sessions.length)],['Response receipts',exact(data.events.length)],['Git milestones',exact(data.commits.length)],['Missing indexed log files',String(data.coverage.missingFiles||0)],['Excluded internal review sessions',String(data.coverage.excludedInternalSessions||0)],['Duplicate response records removed',String(data.coverage.duplicateResponses||0)],['Unreadable / invalid records',String(data.coverage.unreadableRecords||0)],['Undated counter tokens excluded',exact(data.coverage.undatedTokens||0)]]);
  paragraph(c,'All time means all available receipts for this project on this computer. A date with no receipts means no activity was recorded here; it does not prove that no work happened. Another computer can hold additional history.');
  paragraph(c,'The Story view groups receipts by day (by hour in Day view, by week for long histories). Chapter titles use a session’s own opening words, then saved Git milestones. They are navigation labels, not token attribution to a particular change.');
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
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{if(!data)return;mode=b.dataset.mode;selected=null;render();});
$('previous').onclick=()=>{anchor=addDays(anchor,range==='day'?-1:-7);selected=null;render();};
$('next').onclick=()=>{anchor=addDays(anchor,range==='day'?1:7);if(anchor>data.today)anchor=data.today;selected=null;render();};
$('openActivity').onclick=()=>openActivity();$('coverageButton').onclick=coverage;
document.querySelectorAll('[data-info]').forEach(b=>b.onclick=tokenInfo);
$('closeDialog').onclick=()=>$('details').close();$('details').onclick=e=>{if(e.target===$('details')){const r=$('details').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('details').close();}};
$('refresh').onclick=()=>refresh(true);
$('project').onchange=e=>selectProject(e.target.value);
const planActive=()=>{const panel=document.getElementById('planUsage');return !!panel&&!panel.hidden;};
const poll=()=>{if(!document.hidden&&!planActive())refresh();};
document.addEventListener('visibilitychange',poll);
setInterval(poll,30000);
refresh();
// Read-only state for validation; raw transcripts never enter the browser.
window.smithDashboard={refresh,get snapshot(){return data;},get buckets(){return buckets;},get selected(){return selected;},get range(){return range;},get mode(){return mode;},get events(){return scopedEvents;},get project(){return project;}};
