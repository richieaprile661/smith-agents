'use strict';
(() => {
 const tab=document.getElementById('allowance'),historyTab=document.querySelector('nav .nav-active'),main=document.querySelector('main');
 const panel=document.createElement('section');panel.id='planUsage';panel.hidden=true;main.before(panel);
 const make=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 const percent=v=>new Intl.NumberFormat('en',{maximumFractionDigits:1}).format(v)+'%';
 let busy=false,last=null;
 function showPlan(){main.hidden=true;panel.hidden=false;tab.classList.add('nav-active');tab.setAttribute('aria-current','page');historyTab.classList.remove('nav-active');historyTab.removeAttribute('aria-current');refresh();}
 function showHistory(){main.hidden=false;panel.hidden=true;historyTab.classList.add('nav-active');historyTab.setAttribute('aria-current','page');tab.classList.remove('nav-active');tab.removeAttribute('aria-current');if(window.smithDashboard)smithDashboard.refresh();}
 tab.onclick=showPlan;historyTab.onclick=showHistory;
 function render(data){
  panel.replaceChildren();panel.append(make('p','plan-eyebrow','YOUR CONNECTED ACCOUNTS'),make('h1','','How much room do you have?'),make('p','plan-intro','Your actual plan limits, when they reset, and how usage changes while this preview is open.'));
  const grid=make('div','plan-providers');
  for(const provider of data.providers){
   const card=make('section','plan-provider');const heading=make('div','plan-provider-heading');heading.append(make('h2','',provider.provider),make('span',provider.stale?'plan-stale':'plan-live',provider.stale?'Unavailable / stale':'Connected'));card.append(heading);
   if(provider.error)card.append(make('p','plan-error',provider.error));
   for(const limit of provider.limits){
    const row=make('section','plan-limit');row.append(make('h3','',limit.label));
    const headline=make('div','plan-headline');headline.append(make('strong','',percent(limit.remaining)),make('span','','remaining'));row.append(headline,make('p','',percent(limit.used)+' used'));
    const meter=make('progress');meter.max=100;meter.value=limit.used;meter.setAttribute('aria-label',limit.label+' used');row.append(meter);
    let resetText='Reset time unavailable';
    if(limit.resets_at){const reset=new Date(limit.resets_at),ms=reset-Date.now(),minutes=Math.max(0,Math.ceil(ms/60000));const hours=Math.floor(minutes/60),days=Math.floor(hours/24);const duration=days?days+'d '+hours%24+'h':hours?hours+'h '+minutes%60+'m':minutes+'m';resetText=ms<=0?'Reset time passed · awaiting an updated reading':'Resets in '+duration+' · '+reset.toLocaleString(undefined,{weekday:'short',hour:'2-digit',minute:'2-digit'});}
    row.append(make('p','plan-reset',resetText));
    const observation=make('div','plan-observation');
    if(provider.stale||limit.expired){observation.append(make('strong','','Waiting for a fresh reading'),make('p','','The figures above are the last available account reading.'));}
    else if(limit.elapsed_seconds<30){observation.append(make('strong','','First reading saved'),make('p','','Keep this preview open while you work. Changes will appear here after the next reading.'));}
    else{observation.append(make('strong','',percent(limit.baseline_used)+' → '+percent(limit.used)+' used'),make('p','',limit.change+' percentage points used over '+Math.max(1,Math.round(limit.elapsed_seconds/60))+' min of observed readings.'));}
    row.append(observation);card.append(row);
   }
   if(provider.credits)card.append(make('p','plan-credit',(provider.credits.on_credits?'Running on credits · ':'Credit balance · ')+provider.credits.text));
   if(provider.observed_at)card.append(make('p','plan-updated','Account reading: '+new Date(provider.observed_at*1000).toLocaleTimeString()));
   grid.append(card);
  }
  panel.append(grid,make('p','plan-note','These limits are account-wide: they include your other sessions, projects and devices. Project history does not show an allowance change per day, because no reading is recorded for a past day. Observations below start over when the widget restarts.'));
  const refreshButton=make('button','button','Refresh readings');refreshButton.onclick=()=>refresh();panel.append(refreshButton,make('p','plan-updated','Updates every 30 seconds while this tab is visible. A single reading cannot predict when you will run out.'));
 }
 async function refresh(){if(busy)return;busy=true;if(!last)panel.textContent='Reading your account limits…';try{last=await smithSession.api('/api/allowance');render(last);}catch(error){if(last)render({...last,providers:last.providers.map(p=>({...p,stale:true,error:'Connection interrupted. Showing the last reading.'}))});else panel.textContent=(error&&error.message)||'Could not read account usage. Reopen Plan usage to retry.';}finally{busy=false;}}
 setInterval(()=>{if(!panel.hidden&&!document.hidden)refresh();},30000);
 document.addEventListener('visibilitychange',()=>{if(!panel.hidden&&!document.hidden)refresh();});
 if(location.hash==='#plan-usage'||(window.smithSession&&smithSession.view==='plan-usage'))showPlan();
})();
