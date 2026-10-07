import React, { useEffect, useRef, useState } from 'react';
import { Coins, RefreshCw, LoaderCircle } from 'lucide-react';
import './usage.css';

const count = n => n == null ? '—' : n.toLocaleString('en-US');
const usd = n => n == null ? 'Needs review' : `US$${n === 0 ? '0.00' : n.toFixed(n < .0001 ? 8 : 6).replace(/0+$/, '').replace(/\.$/, '')}`;
const labels = {document:'Document transcription',transcription:'Audio transcription',report:'Media report',index_embedding:'Index embeddings',query_embedding:'Query embeddings',jev:'JEV relevance scoring',openai_decisions:'OpenAI Decisions relevance scoring',agent:'Agent conversation',answer_eval:'Answer evaluation',optimization:'Agent suggestions'};

export default function UsagePanel({api,jobId,fileId,chatId,turnId,running=false,compact=false,title='Usage & cost'}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[syncError,setSyncError]=useState('');
  const [mode,setMode]=useState('turn'),[syncing,setSyncing]=useState(false);
  const currentId=useRef(chatId), syncBusy=useRef(false);
  currentId.current=chatId;
  const thisTurn=!!(chatId&&turnId&&mode==='turn');
  const query=new URLSearchParams(chatId?{chat_id:chatId,...(thisTurn?{turn_id:turnId}:{})}:jobId?{job_id:jobId,...(fileId?{file_id:fileId}:{})}:{}).toString();
  useEffect(()=>{
    let alive=true,loading=false;
    setData(null);setError('');
    async function refresh(){if(loading)return;loading=true;try{const value=await api(`/usage?${query}`);if(alive){setData(value);setError('');}}catch(e){if(alive)setError('Usage updates are unavailable. Showing the last recorded data.');}finally{loading=false;}}
    refresh();const timer=setInterval(refresh,2000);
    return()=>{alive=false;clearInterval(timer);};
  },[query]);
  async function sync(id=chatId){
    if(!id||syncBusy.current)return;
    syncBusy.current=true;setSyncing(true);setSyncError('');
    try{await api(`/chats/${id}/usage/refresh`,{method:'POST'});}catch(e){if(currentId.current===id)setSyncError(e.message);}
    finally{syncBusy.current=false;setSyncing(false);}
  }
  useEffect(()=>{
    if(!chatId)return;
    setSyncError('');sync(chatId);
    // Provider counts can arrive after completion. These reads never send a new turn.
    const timer=running?setInterval(()=>sync(chatId),15000):null;
    const late=running?null:setTimeout(()=>sync(chatId),15000);
    return()=>{clearInterval(timer);clearTimeout(late);};
  },[chatId,running]);
  const summary=data?.summary;
  const awaiting=running&&(!summary||summary.entries===0);
  return <section className={`usage-panel ${compact?'compact':'panel'}`} aria-label={title}>
    <div className="usage-heading"><h3><Coins size={16}/>{title}</h3>{chatId&&<button onClick={()=>sync()} disabled={syncing} title="Refresh usage without resending your question" aria-label="Refresh Agent usage">{syncing?<LoaderCircle size={14} className="spin"/>:<RefreshCw size={14}/>}</button>}{running&&<span className="usage-live"><i/>Live updates</span>}</div>
    {chatId&&<div className="usage-tabs"><button className={thisTurn?'selected':''} disabled={!turnId} onClick={()=>setMode('turn')}>This turn</button><button className={!thisTurn?'selected':''} onClick={()=>setMode('all')}>Entire conversation</button></div>}
    <div className="usage-totals"><div><span>Reported tokens</span><strong>{awaiting?'—':count(summary?.total_tokens)}</strong></div><div><span>Known cost estimate · USD</span><strong className="usage-money">{awaiting?'Awaiting usage':data?usd(summary?.cost_usd):'—'}</strong></div></div>
    {summary&&<><div className="usage-token-details"><span>Input <b>{count(summary.input_tokens)}</b></span><span>Output <b>{count(summary.output_tokens)}</b></span><span>Cached input <b>{count(summary.cached_tokens)}</b></span>{summary.reasoning_tokens>0&&<span>Includes reasoning <b>{count(summary.reasoning_tokens)}</b></span>}</div>
      {(summary.unknown_costs>0||summary.unknown_tokens>0||running)&&<p className="usage-pending">{summary.pending>0?`${summary.pending} requests running; awaiting usage.`:running?'Agent usage may arrive after completion.':''}{summary.unknown_costs-summary.pending>0&&` ${summary.unknown_costs-summary.pending} unpriced requests excluded from the total.`}{summary.unknown_tokens>0&&' tokens are a subtotal of reported usage.'}</p>}
      {data.groups.length>0?<div className="usage-groups">{data.groups.map(g=><div className="usage-group" key={g.operation+g.model}><div><strong>{g.label}</strong><small>{g.model} · {g.entries} {g.operation==='agent'?'turns':'items'}</small></div><div><b>{usd(g.cost_usd)}</b><small>{count(g.total_tokens)} tokens{g.unknown_costs>0?' · Incomplete usage':''}</small></div></div>)}</div>:<p className="usage-empty">{awaiting?'Running; waiting for the first usage report.':'No model calls recorded yet. Usage will appear as work completes.'}</p>}
      <details className="usage-details"><summary>Usage details & pricing basis{data.entries.length>0&&`（${data.entries.length}${summary.entries>100?' recent records':' items'}）`}</summary><p>{data.note}</p><p>Pricing checked: {data.price_date} · <a href={data.sources[0]} target="_blank" rel="noreferrer">OpenAI</a> · <a href={data.sources[1]} target="_blank" rel="noreferrer">TypeSafe</a></p><p>Agent cost uses base short-context rates. Unreported cache writes and long-context premiums are excluded. Cached and reasoning tokens are already included in input/output totals.</p>{data.entries.map(e=><article key={e.id}><div><strong>{labels[e.operation]||e.operation}</strong><b>{usd(e.cost_usd)}</b></div><p>{e.label||e.model}</p><small>{new Date(e.at).toLocaleString('en-US')} · {e.status==='pending'?'Running':e.total_tokens==null?'tokens Not reported':`${count(e.total_tokens)} tokens`}{e.duration_seconds!=null&&` · ${(e.duration_seconds/60).toFixed(2)} minutes`}</small><p>Model {e.model} · Input {count(e.input_tokens)} · Output {count(e.output_tokens)} · Cache read {count(e.cached_tokens)}{e.cache_write_tokens!=null&&` · Cache write ${count(e.cache_write_tokens)}`}</p>{e.rates&&<p className="usage-rate">{e.rates.per_minute!=null?`Per minute US$${e.rates.per_minute}`:`Per million tokens：Input ${e.rates.input}／Cache read ${e.rates.cached}／Cache write ${e.rates.cache_write}／Output ${e.rates.output} USD`}</p>}{(e.note||e.price_note)&&<p>{e.note||e.price_note}</p>}</article>)}</details>
      {!fileId&&<div className="usage-cumulative"><span>Folio recorded total</span><b>{usd(data.cumulative.cost_usd)}</b><small>{count(data.cumulative.total_tokens)} tokens{data.cumulative.unknown_costs>0?' · Includes unpriced items':''}</small></div>}
    </>}
    {(error||syncError)&&<p className="usage-pending" role="status">{error||syncError}</p>}
    <p className="usage-caption">Provider-reported usage × published rates. Unreported usage and external services are excluded. Your provider bill is authoritative.</p>
  </section>;
}
