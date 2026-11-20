"use strict";
const $ = id => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const finite = Number.isFinite;
let revision = "", cursor = 0, peers = new Map(), feed = [], meta, latest;
let selected = "", pending = false, connected = false, mock = false;
let newest = 0;
let recording = [], applied = 0, playhead = 0, timer = null, lastTick = 0;
const shown = n => finite(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 2}) : "—";
const pct = (n, d) => d > 0 ? Math.min(100, 100*n/d) : 0;

function peerFor(e) {
  if (e.type==="join" && peers.get(e.source)?.joined!=null) peers.delete(e.source);
  if (!peers.has(e.source)) {
    const parts = e.source.split("/");
    peers.set(e.source, {key:e.source, name:parts.length > 1 ? parts.slice(0,-1).join("/") : e.node,
      address:e.node, pieces:new Set(), pending:new Set(), failed:new Set(), signals:new Map(),
      links:new Map(), rates:[], initial:new Set(), joined:null, complete:false, summary:null});
  }
  return peers.get(e.source);
}
function sample(list, value) {
  list.push(value);
  if (list.length > 500) list.shift();
}
function ingest(e) {
  newest = Math.max(newest,e.t);
  if (e.type === "marker") {
    mock ||= e.label === "Synthetic fixture";
  } else if (["join","have","piece_select","hash_fail","rate","transport","complete","summary","choke","unchoke"].includes(e.type)) {
    const p = peerFor(e);
    switch(e.type) {
      case "join":
        p.seed = e.origin_seed; p.joined=e.t;
        if (Number.isInteger(e.piece_count)) p.dataset={pieces:e.piece_count,length:e.file_bytes,piece_length:e.piece_bytes};
        break;
      case "have":
        p.pieces.add(e.piece); p.pending.delete(e.piece); p.failed.delete(e.piece);
        if (e.initial) p.initial.add(e.piece);
        break;
      case "piece_select": if (!p.pieces.has(e.piece)) {p.pending.add(e.piece);p.failed.delete(e.piece);} break;
      case "hash_fail": p.failed.add(e.piece);p.pending.delete(e.piece);break;
      case "rate": sample(p.rates,e);break;
      case "choke": case "unchoke": p.signals.set(e.peer_id || [e.peer,e.peer_port].join(":"),e.type);break;
      case "transport": {
        const id=String(e.connection_id ?? [e.peer,e.peer_port].join(":"));
        if (!p.links.has(id)) p.links.set(id,[]);
        sample(p.links.get(id),e); break;
      }
      case "complete": p.complete=true;p.end=e.t;break;
      case "summary": p.summary=e;p.complete ||= e.complete===true;break;
    }
  }
  if (!e.initial && !["rate","transport","piece_select"].includes(e.type)) {
    feed.push(e);
    feed.sort((a,b)=>a.t-b.t);
    if (feed.length>12) feed.shift();
  }
}
function text(tag, value, cls="") {
  const el=document.createElement(tag); el.textContent=value; el.className=cls; return el;
}
function options(select, items, preferred) {
  const current=[...select.options];
  if (current.length!==items.length || items.some(([v,l],i)=>current[i]?.value!==v || current[i].text!==l)) {
    select.replaceChildren(...items.map(([v,l])=>new Option(l,v)));
  }
  if (items.some(([v])=>v===preferred)) select.value=preferred;
}
function svg(tag,attrs,label) {
  const el=document.createElementNS(NS,tag);
  for (const [k,v] of Object.entries(attrs)) el.setAttribute(k,v);
  if (label!==undefined) el.textContent=label;
  return el;
}
function draw(p) {
  const rate=$("metric").value==="rate", chart=$("chart");
  const overlay=$("metric").value==="overlay";
  const links=Array.from(p?.links || []);
  const choice=$("link");
  options(choice,links.map(([k,arr])=>[k,"Link "+k+" · "+arr.at(-1).peer]),choice.value);
  choice.hidden=rate || !links.length;
  const rows=rate ? p?.rates || [] : p?.links.get(choice.value) || [];
  const series=rate ? [["down_bps","#30d158"],["up_bps","#c1efd0"]] : [["cwnd_bytes","#30d158"]];
  if (overlay) series.push(["rtt_ms","#8cc8ff"],["retx","#ff8fa8"]);
  const unit=rate ? "Mbit/s" : "KiB", factor=rate ? 1e6 : 1024;
  const values=rows.flatMap(r=>series.map(([key])=>r[key]).filter(finite));
  $("series").textContent=rate ? "↓ Download   ↑ Upload" : "Congestion window";
  if (overlay) {
    const labels=["Window","RTT","Retransmissions"];
    $("series").replaceChildren(...series.map(([,color],i)=>{
      const label=text("span",labels[i],"legend");
      label.style.color=color;
      return label;
    }));
  }
  chart.replaceChildren();
  if (!values.length) {
    chart.append(svg("text",{x:320,y:125,"text-anchor":"middle",fill:"#a1a1aa"},rate?"Waiting for rate samples":"No transport samples reported"));
    $("scale").textContent="Awaiting measurements";return;
  }
  const peak=Math.max(...values)/factor, max=peak || 1, start=rows[0].t, end=rows.at(-1).t;
  for (const fraction of [0,.5,1]) {
    const y=203-176*fraction;
    chart.append(svg("line",{x1:18,y1:y,x2:555,y2:y,stroke:"#343439"}),
      svg("text",{x:566,y:y+4,fill:"#aaa","font-size":12},overlay?shown(100*fraction)+"%":shown(max*fraction)));
  }
  const peaks=[];
  for(const [key,color] of series) {
    const samples=rows.filter(r=>finite(r[key]));
    const high=samples.length?Math.max(...samples.map(r=>r[key])):null;
    if (overlay) peaks.push(high===null?"—":key==="cwnd_bytes"?shown(high/1024)+" KiB":key==="rtt_ms"?shown(high)+" ms":shown(high)+" retx");
    const divisor=overlay?(high || 1):factor*max;
    const points=samples.map(r=>[18+537*(r.t-start)/Math.max(end-start,.001),203-176*r[key]/divisor]);
    if (!points.length) continue;
    chart.append(svg("polyline",{points:points.map(pt=>pt.join(",")).join(" "),fill:"none",stroke:color,"stroke-width":3}));
    const [cx,cy]=points.at(-1);chart.append(svg("circle",{cx,cy,r:4,fill:color}));
  }
  chart.append(svg("text",{x:18,y:228,fill:"#aaa","font-size":12},shown(start)+" s"),
    svg("text",{x:555,y:228,"text-anchor":"end",fill:"#aaa","font-size":12},shown(end)+" s"));
  $("scale").textContent=overlay?"Peaks: "+peaks.join(" · "):"Peak "+shown(peak)+" "+unit;
}
function render() {
  if(!latest) return;
  const list=[...peers.values()].sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));
  const leechers=list.filter(p=>!p.seed);
  const p=peers.get(selected) || leechers[0] || list[0];
  if (!latest.replay && p) selected=p.key;
  options($("peer"),list.map(p=>[p.key,p.name]),p?.key);
  const data=meta || list.find(n=>n.dataset)?.dataset;
  const count=data?.pieces, complete=leechers.filter(n=>n.complete).length;
  const protocol=latest.protocol || list.flatMap(n=>[...n.links.values()]).find(a=>a.length)?.at(-1).proto;
  $("transport-choice").hidden=!latest.demo;
  const badge=$("recorded-transport");
  badge.hidden=latest.demo || !protocol;
  badge.textContent=protocol ? (latest.replay?"Recorded transport · ":"Transport · ")+(protocol==="tcp"?"TCP":"MurmurTP") : "";
  $("tcp").disabled=$("mtp").disabled=!latest.demo || pending || latest.status==="Running";
  $("run").hidden=!latest.demo;
  $("run").disabled=pending || latest.status==="Running";
  $("run").textContent=latest.status==="Running"?"Transferring…":"Run "+($("tcp").checked?"TCP":"MurmurTP")+" transfer";
  $("environment").textContent=latest.demo?"Localhost · real peers":mock?"Synthetic fixture":"Peer event logs";
  $("status").textContent=!connected?"Disconnected":latest.error || (latest.status==="Watching" && list.length && list.every(n=>n.summary)?"Ended":latest.status);
  if (latest.replay) {
    $("status").textContent=latest.more?"Loading replay":!recording.length?"No events":timer!==null?"Playing":
      applied===recording.length?"Replay ended":"Paused";
  }
  if(latest.invalid) $("status").textContent+=" · "+latest.invalid+" invalid rows";
  $("subtitle").textContent=[data ? shown(data.length/1048576)+" MiB · "+count+" pieces" : "Waiting for dataset metadata",
    leechers.length+" leechers",protocol || "Awaiting transport"].join("  ·  ");
  $("pieces").textContent=p ? p.pieces.size+(count!=null?" / "+count:"") : "—";
  const donePieces=leechers.reduce((a,n)=>a+n.pieces.size,0);
  const percent=count && leechers.length ? pct(donePieces,count*leechers.length) : null;
  const timed=leechers.filter(n=>finite(n.joined));
  const duration=timed.length ? Math.max(...timed.map(n=>n.end ?? (latest.replay?playhead:newest)))-Math.min(...timed.map(n=>n.joined)) : 0;
  let verifiedBytes=0;
  if(data && duration>0) for(const node of timed) for(const i of node.pieces) {
    if(!node.initial.has(i)) verifiedBytes+=Math.min(data.piece_length,data.length-i*data.piece_length);
  }
  $("goodput").textContent=data && duration>0 ? shown(verifiedBytes*8/duration/1e6)+" Mbit/s" : "—";
  $("finished").textContent=leechers.length?complete+" / "+leechers.length:"—";
  const rings=document.querySelectorAll("[pathLength], [pathlength]");
  [pct(p?.pieces.size||0,count),percent||0,pct(complete,leechers.length)].forEach((v,i)=>rings[i]?.setAttribute("stroke-dasharray",v+" "+(100-v)));
  const totals=list.map(n=>n.summary ? n.summary.retx : n.links.size ? [...n.links.values()].reduce((v,a)=>{
    const value=a.at(-1).retx; return finite(v)&&finite(value)?v+value:null;
  },0):null);
  $("retx").textContent=totals.length && totals.every(finite)?shown(totals.reduce((a,b)=>a+b,0)):"—";
  $("slots").textContent=p?.signals.size ? [...p.signals.values()].filter(v=>v==="unchoke").length+" / "+p.signals.size : "—";
  const observed=p ? Array.from(p.pieces).reduce((last,i)=>Math.max(last,i+1),0) : 0;
  const grid=$("map"), limit=Math.min(count ?? observed,4096);
  if (grid.children.length!==limit) grid.replaceChildren(...Array.from({length:limit},()=>document.createElement("i")));
  Array.from(grid.children).forEach((el,i)=>{
    const state=p?.pieces.has(i)?"Verified":p?.failed.has(i)?"Hash failed":p?.pending.has(i)?"Selected":"Missing";
    el.className=state.toLowerCase().replace(" ","-");el.title="Piece "+i+" · "+state;
  });
  grid.setAttribute("aria-label","Piece map for "+(p?.name || "peer"));
  $("peers").replaceChildren(...list.map(n=>{
    const tr=document.createElement("tr"), cell=document.createElement("td"), btn=text("button",n.name,"peer-button");
    btn.onclick=()=>{selected=n.key;render()};cell.append(btn);
    if(n.name!==n.address)cell.append(text("small",n.address));
    const state=n.seed?"Seed":n.complete?"Verified":n.summary?"Stopped":"Receiving";
    const samples=[...n.links.values()].map(a=>a.at(-1)).filter(a=>finite(a.rtt_ms)).sort((a,b)=>b.t-a.t);
    const frac=pct(n.pieces.size,count), progress=text("td",count?shown(frac)+"%":n.pieces.size+" pieces");
    const bar=text("div","","bar"), fill=document.createElement("b");fill.style.width=frac+"%";bar.append(fill);progress.append(bar);
    const status=document.createElement("td");status.append(text("span",state,"pill "+(n.complete?"g":"")));
    tr.append(cell,status,progress,text("td",samples.length?shown(samples[0].rtt_ms)+" ms":"—"));return tr;
  }));
  $("feed").replaceChildren(...feed.slice(-8).map(e=>{
    const line=document.createElement("div"), name=peers.get(e.source)?.name || e.node;
    line.append(text("span",e.t.toFixed(2).padStart(7)+" s  ","d"),text("span",e.type.padEnd(10),"a"),
      document.createTextNode("  "+name+(e.piece!=null?" · piece "+e.piece:e.label?" · "+e.label:"")));return line;
  }));
  if(!feed.length)$("feed").textContent="Waiting for peer events";
  draw(p);
}

function pause() {
  clearInterval(timer);
  timer=null;
  $("play").textContent=recording.length && applied===recording.length?"Replay":"Play";
}
function seek(time) {
  if (!recording.length) return;
  const end=recording.at(-1).t;
  time=Math.max(Number($("timeline").min),Math.min(time,end));
  if (time<playhead) {
    peers.clear(); feed=[]; newest=0; mock=false; applied=0;
  }
  while (applied<recording.length && recording[applied].t<=time) ingest(recording[applied++]);
  playhead=time;
  $("timeline").value=time;
  const clock=shown(time)+" / "+shown(end)+" s";
  $("clock").textContent=clock;
  $("timeline").setAttribute("aria-valuetext",clock);
  if (time>=end) pause();
  else if (timer===null) $("play").textContent="Play";
  render();
}
function readyReplay() {
  if (!recording.length) {
    $("clock").textContent="No recorded events";
    render();
    return;
  }
  const slider=$("timeline"), start=recording[0].t, end=recording.at(-1).t;
  slider.min=start; slider.max=end;
  slider.disabled=$("play").disabled=end<=start;
  seek(start);
}
$("timeline").oninput=e=>{
  pause();
  seek(Number(e.target.value));
};
$("play").onclick=()=>{
  if (timer!==null) {pause();render();return;}
  if (applied===recording.length) seek(Number($("timeline").min));
  lastTick=performance.now();
  $("play").textContent="Pause";
  timer=setInterval(()=>{
    const now=performance.now();
    seek(playhead+(now-lastTick)*Number($("speed").value)/1000);
    lastTick=now;
  },125);
  render();
};
async function poll() {
  try {
    const res=await fetch("/api/state?since="+cursor+"&revision="+encodeURIComponent(revision),{cache:"no-store"});
    if(!res.ok) throw Error(res.status);
    const s=await res.json();
    if(revision!==s.revision){
      peers.clear();feed=[];selected="";mock=false;newest=0;
      pause();recording=[];applied=0;playhead=0;
      $("replay").hidden=!s.replay;
      if(s.protocol) {
        $("metric").value=s.protocol==="tcp"?"rate":"cwnd";
        $("tcp").checked=s.protocol==="tcp";$("mtp").checked=s.protocol==="murmurtp";
      }
    }
    revision=s.revision;cursor=s.cursor;latest=s;meta=s.dataset;connected=true;
    if (s.replay) {
      recording.push(...s.events);
      if (!s.more) {readyReplay();return;}
      $("status").textContent="Loading replay";
    } else {
      s.events.forEach(ingest);render();
    }
  } catch(err){connected=false;$("status").textContent="Disconnected · retrying";console.error(err);}
  setTimeout(poll,connected && latest?.more ? 0 : 1000);
}
$("peer").onchange=e=>{selected=e.target.value;render()};
$("metric").onchange=$("link").onchange=$("tcp").onchange=$("mtp").onchange=render;
$("run").onclick=async()=>{
  pending=true;render();
  try {
    const res=await fetch("/api/demo/"+($("tcp").checked?"tcp":"murmurtp"),{method:"POST",headers:{"X-Murmur-Review":"1"}});
    if(!res.ok)throw Error((await res.json()).error || res.statusText);
    latest.status="Running";
  }catch(e){latest.error=e.message;}
  finally{pending=false;render();}
};
poll();
