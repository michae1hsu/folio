import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowUpRight, ArrowRight, ArrowDownToLine, CloudDownload, Link2, FileArchive, FileText, Files,
  AudioLines, Video, Image as ImageIcon, Check, CheckCheck, CircleHelp, X, Search,
  SlidersHorizontal, LayoutDashboard, History, FolderOpen, ChevronRight, ChevronLeft,
  Plus, LoaderCircle, ShieldCheck, ScanText, Layers3, AlertTriangle, Square, Copy,
  ExternalLink, RotateCcw, Columns2, Code2, ListChecks, Zap, Monitor, Inbox, Database, MessagesSquare } from 'lucide-react';
import Knowledge from './Knowledge';
import ChatPanel from './ChatPanel';
import EvaluationPanel from './EvaluationPanel';
import SearchPanel from './SearchPanel';
import Markdown from './Markdown';
import UsagePanel from './UsagePanel';
import './style.css';
import './knowledge.css';
import './search.css';
import DriveSettings from './DriveSettings';
import ImportPanel from './ImportPanel';

const active = ['queued', 'running', 'cancelling'];
const successful = ['completed', 'review'];
const statusLabels = { ready: 'Ready', downloading: 'Downloading', blocked: 'Unavailable', queued: 'Queued', running: 'Processing', converting: 'Converting', parsing: 'Parsing',
  auditing: 'Checking', summarizing: 'Writing report', completed: 'Completed', review: 'Needs review', failed: 'Failed', partial: 'Partially failed',
  cancelled: 'Cancelled', cancelling: 'Cancelling', interrupted: 'Interrupted' };
const kinds = {document:'Document', image:'Image', audio:'Audio', video:'Video', unsupported:'Unsupported'};
const kindIcons = { document: FileText, image: ImageIcon, audio: AudioLines, video: Video, unsupported: AlertTriangle };
const date = value => new Intl.DateTimeFormat('en-US', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
const size = value => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;

async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers: { 'X-Folio-Request':'1', ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The operation could not be completed. Please try again.');
  return data;
}

function Badge({status}) {
  return <span className={`badge ${status}`}><i />{statusLabels[status] || status}</span>;
}

function FileIcon({kind,small=false}) {
  const Icon = kindIcons[kind] || FileText;
  return <span className={`file-icon ${kind} ${small?'small':''}`}><Icon size={small?17:21} strokeWidth={1.7}/></span>;
}

function saveText(text, name) {
  const url = URL.createObjectURL(new Blob([text], {type:'text/markdown;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function App() {
  const [health, setHealth] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState('workbench');
  const [searchVisited, setSearchVisited] = useState(false);
  const [searchLibraryId, setSearchLibraryId] = useState(()=>localStorage.getItem('folio-library')||'');
  const [concurrency, setConcurrency] = useState(4);
  const [scanning, setScanning] = useState(false);
  const [driveInfo, setDriveInfo] = useState(null);
  const [notice, setNotice] = useState(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [inspector, setInspector] = useState(null);
  const [modal, setModal] = useState(null);
  const [busy, setBusy] = useState(false);
  const noticeTimer = useRef();
  const job = jobs.find(j => j.id === selected) || jobs[0];
  const isActive = job && active.includes(job.status);
  const allFiles = jobs.flatMap(j => j.files.map(f => ({...f,job:j})));

  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement;
    document.querySelector('.info-modal button')?.focus();
    const handler = e => dialogKey(e, () => setModal(null));
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, [modal]);

  function toast(text, error=false) { setNotice({text,error}); clearTimeout(noticeTimer.current); noticeTimer.current = setTimeout(()=>setNotice(null),7000); }
  async function refresh() {
    const data = await api('/jobs'); setJobs(data); return data;
  }
  useEffect(() => {
    api('/health').then(setHealth).catch(e=>toast(e.message,true));
    api('/drive/status').then(setDriveInfo).catch(e=>toast(e.message,true));
    refresh().catch(e=>toast(e.message,true));
    const timer = setInterval(() => refresh().catch(()=>{}),1800);
    return () => {clearInterval(timer); clearTimeout(noticeTimer.current);};
  }, []);

  async function imported(created) {
    await refresh(); setSelected(created.id); setView('workbench'); setFilter('all'); setQuery('');
  }
  function newBatch() {
    setView('workbench');
    requestAnimationFrame(()=>{const panel=document.getElementById('source-intake');panel?.focus();panel?.scrollIntoView({behavior:'smooth',block:'center'});});
  }
  function changeSearchLibrary(id) {
    setSearchLibraryId(id);
    if(id)localStorage.setItem('folio-library',id);
  }
  function showSearch(id=localStorage.getItem('folio-library')||'') {
    changeSearchLibrary(id);setSearchVisited(true);setView('search');
  }
  async function action(type) {
    if(!job || busy) return;
    setBusy(true);
    try {
      await api(`/jobs/${job.id}/${type}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({concurrency})});
      await refresh(); toast(type==='cancel'?'Cancellation requested. Processing stops after the current API calls finish.':'Processing started. You can switch views and return to check progress.');
    } catch(e) {toast(e.message,true);} finally {setBusy(false);}
  }
  async function downloadFile(j,f) {
    try {const data=await api(`/jobs/${j.id}/files/${f.id}/markdown`);saveText(data.text,f.name.split('/').pop()+'.md');}
    catch(e){toast(e.message,true);}
  }
  const headings = {workbench:['Workbench','Document workbench','Turn Google Drive folders or ZIP archives into complete, organized Markdown.'],history:['Batch history','Every import, in one place.','Track progress and return to any batch to continue your work.'],outputs:['Outputs','Your Markdown collection','Preview, review, and download your files. Incomplete content stays clearly marked.'],knowledge:['Libraries','Your documents, connected.','Track cloud storage, source documents, and searchable indexes.'],search:['Document search','Find the right document.','Search a library, compare relevance, and open the sources.'],chat:['Document chat','Ask your documents.','Let AI search and read your documents, with sources and a visible activity log.']};
  headings.evaluation=['Evaluations','Understand every answer.','Review answer quality, conversation history, model costs, and improvement suggestions.'];
  const heading = headings[view];
  const jobFiles = job?.files || [];
  const completed = jobFiles.filter(f=>successful.includes(f.status)).length;
  const pages = jobFiles.filter(f=>!['audio','video'].includes(f.kind)).reduce((n,f)=>n+f.page_count,0);
  const donePages = jobFiles.filter(f=>!['audio','video'].includes(f.kind)).reduce((n,f)=>n+f.completed_pages,0);
  const issueCount = jobFiles.filter(f=>['review','failed','unsupported'].includes(f.status)||f.kind==='unsupported').length;
  const filtered = jobFiles.filter(f => (filter==='all'||(filter==='media'?['audio','video'].includes(f.kind):filter==='attention'?['review','failed'].includes(f.status)||f.kind==='unsupported':['document','image'].includes(f.kind))) && f.name.toLowerCase().includes(query.toLowerCase()));

  return <div className={`app-shell view-${view}`}>
    <aside className="sidebar">
      <a className="brand" href="#" onClick={e=>{e.preventDefault();setView('workbench');}} aria-label="Folio home"><span className="brand-symbol"><i/><i/><i/></span><span>folio<span className="brand-zh"></span></span></a>
      <div className="workspace-switch"><span className="workspace-avatar"><Layers3 size={17}/></span><div><strong>My workspace</strong><small>Local workbench</small></div><span className="workspace-dot"/></div>
      <div className="nav-label">Workspace</div>
      <nav aria-label="Main navigation">
        <button className={view==='workbench'?'selected':''} onClick={()=>setView('workbench')}><LayoutDashboard size={19}/>Workbench</button>
        <button className={view==='history'?'selected':''} onClick={()=>setView('history')}><History size={19}/>Batch history<span className="nav-count">{jobs.length}</span></button>
        <button className={view==='outputs'?'selected':''} onClick={()=>setView('outputs')}><FolderOpen size={19}/>Outputs</button>
        <button className={view==='knowledge'?'selected':''} onClick={()=>setView('knowledge')}><Database size={19}/>Libraries</button>
        <button className={view==='search'?'selected':''} onClick={()=>showSearch()}><Search size={19}/>Document search</button>
        <button className={view==='chat'?'selected':''} onClick={()=>setView('chat')}><MessagesSquare size={19}/>Document chat</button>
        <button className={view==='evaluation'?'selected':''} onClick={()=>setView('evaluation')}><ListChecks size={19}/>Evaluations</button>
      </nav>
      <div className="sidebar-note"><span className="tiny-icon"><ScanText size={19}/></span><strong>Every page accounted for.</strong><p>Preserve the source. Make it<br/>readable and reusable.</p><div className="mini-pages"><i/><i/><i/><i/><i/><i/></div></div>
      <div className="sidebar-bottom"><button onClick={()=>setModal('help')}><CircleHelp size={18}/>How it works<ArrowUpRight size={15}/></button><button onClick={()=>setModal('settings')}><SlidersHorizontal size={18}/>Connections & models</button><div className="local-profile"><span><Monitor size={19}/></span><div><strong>Local workspace</strong><small>Markdown stored in the cloud</small></div><i/></div></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div><span className="mobile-brand">folio</span>Workspace<ChevronRight size={14}/><strong>{heading[0]}</strong></div><button className="connection" onClick={()=>setModal('settings')}><span className={driveInfo?.configured?'online':'offline'}/>{driveInfo?.authenticated?'Drive connected':driveInfo?.configured?'Drive credentials loaded':'Connect Google Drive'}<ChevronRight size={13}/></button></header>

      <nav className="mobile-nav" aria-label="Mobile navigation">
        <button className={view==='workbench'?'selected':''} onClick={()=>setView('workbench')}><LayoutDashboard size={16}/>Workbench</button>
        <button className={view==='history'?'selected':''} onClick={()=>setView('history')}><History size={16}/>Batch history</button>
        <button className={view==='outputs'?'selected':''} onClick={()=>setView('outputs')}><FolderOpen size={16}/>Outputs</button>
        <button className={view==='knowledge'?'selected':''} onClick={()=>setView('knowledge')}><Database size={16}/>Libraries</button>
        <button className={view==='search'?'selected':''} onClick={()=>showSearch()}><Search size={16}/>Search</button>
        <button className={view==='chat'?'selected':''} onClick={()=>setView('chat')}><MessagesSquare size={16}/>Chat</button>
        <button className={view==='evaluation'?'selected':''} onClick={()=>setView('evaluation')}><ListChecks size={16}/>Evaluations</button>
      </nav>
      <main>
        <div className="page-heading"><div><div className="eyebrow">DOCUMENT INTELLIGENCE WORKSPACE</div><h1>{heading[1]}</h1><p>{heading[2]}</p></div><button className="button primary" onClick={newBatch} disabled={scanning}><Plus size={17}/>New batch</button></div>
        {['search','chat'].includes(view)&&<div className="discovery-modes" role="group" aria-label="Mode"><button aria-pressed={view==='search'} onClick={()=>showSearch()}><Search size={16}/>Search only</button><button aria-pressed={view==='chat'} onClick={()=>setView('chat')}><MessagesSquare size={16}/>AI chat</button></div>}

        {view==='workbench' && <>
          <div className="intake-grid">
            <ImportPanel api={api} driveInfo={driveInfo} onDriveUpdate={setDriveInfo} onSettings={()=>setModal('settings')} onImported={imported} onBusyChange={setScanning} toast={toast}/>
            <section className="pipeline-card panel"><div className="section-head"><h2>From source to text</h2><span className="spark"><Zap size={16}/></span></div><p className="pipeline-intro">A complete workflow for every file type.</p><div className="route"><span className="route-icon"><Files size={18}/></span><div><strong>Documents & images</strong><p>Text, images, and relationships → full transcription</p><span className="model-chip">{health?.document_model||'gpt-6-luna'}</span></div></div><div className="route"><span className="route-icon violet"><AudioLines size={18}/></span><div><strong>Audio & video</strong><p>Full transcription → Complete report</p><span className="model-chip">{health?.transcription_model||'gpt-transcribe'} → {health?.report_model||'gpt-6-luna'}</span></div></div><div className="quality-note"><ShieldCheck size={18}/><div><strong>Completeness first</strong><span>Full content · layout and charts · uncertainty markers</span></div></div></section>
          </div>

          <section className="batch-panel panel">
            <div className="batch-header"><div><h2><span className="step-number">02</span>Processing queue {job&&<span className="count-pill">{jobFiles.length}</span>}</h2><p>{job?job.name:'Import a Drive folder or ZIP archive to fill this queue.'}</p></div><div className="batch-controls">{jobs.length>1&&<select aria-label="Switch batch" value={job.id} onChange={e=>{setSelected(e.target.value);setQuery('');}}>{jobs.map(j=><option key={j.id} value={j.id}>{j.name}</option>)}</select>}<label className="parallel-control"><Layers3 size={15}/>Parallel files<select aria-label="Parallel file count" value={isActive?job.concurrency:concurrency} onChange={e=>setConcurrency(Number(e.target.value))} disabled={isActive}><option value={2}>2</option><option value={4}>4</option><option value={6}>6</option></select></label>{isActive?<button className="button secondary" onClick={()=>action('cancel')} disabled={busy||job.status==='cancelling'}><Square size={13}/>{job.status==='cancelling'?'Cancelling':'Stop processing'}</button>:<button className="button primary" onClick={()=>action('start')} disabled={!job||successful.includes(job.status)||busy||!health?.key_configured}>{busy?<LoaderCircle size={16} className="spin"/>:job&&job.status!=='ready'?<RotateCcw size={16}/>:<Zap size={16}/>} {job&&job.status!=='ready'&&!successful.includes(job.status)?'Retry incomplete':'Start processing'}</button>}</div></div>
            {job?.source?.type==='google_drive'&&<div className="batch-source"><FolderOpen size={13}/><a href={job.source.url} target="_blank" rel="noreferrer">Open source folder <ExternalLink size={12}/></a><span>{job.source.recursive?'Include subfolders':'This folder only'} · Files at import time</span></div>}{job&&<div className="batch-metrics"><div><span>File progress</span><strong>{completed}<small> / {jobFiles.length}</small></strong><div className="progress-track"><i style={{width:`${completed/jobFiles.length*100}%`}}/></div></div><div><span>Pages transcribed</span><strong>{donePages}<small> / {pages||'—'}</small></strong></div><div><span>Files to review</span><strong className={issueCount?'amber-text':''}>{issueCount.toString().padStart(2,'0')}</strong></div><div className="batch-state"><Badge status={job.status}/><small>{isActive?`Up to ${job.concurrency} files in parallel`:'Progress saved automatically'}</small></div></div>}
            {job&&<UsagePanel api={api} jobId={job.id} running={!!isActive} title="Batch usage & cost"/>}
            <div className="table-toolbar"><div className="tabs" role="group" aria-label="Filter files">{[['all','All files'],['document','Documents & images'],['media','Media'],['attention','Needs review']].map(([key,label])=><button key={key} className={filter===key?'active':''} onClick={()=>setFilter(key)}>{label}{key==='all'&&<span>{jobFiles.length}</span>}</button>)}</div><label className="search-box"><Search size={15}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search files…" aria-label="Search files"/></label></div>
            {!job?<div className="empty-queue"><span className="empty-icon"><Inbox size={28} strokeWidth={1.3}/></span><strong>Your next collection starts here.</strong><p>Paste a shared Drive folder link or upload a ZIP archive to review the file list.</p><div className="empty-steps"><span>Import files</span><ArrowRight size={13}/><span>Process in parallel</span><ArrowRight size={13}/><span>Download Markdown</span></div></div>:<div className="file-table-wrap"><table className="file-table"><thead><tr><th>Filename</th><th>Method</th><th>Pages / tracks</th><th>Status</th><th><span className="sr-only">Preview</span></th></tr></thead><tbody>{filtered.map(f=><tr key={f.id} onClick={()=>setInspector({jobId:job.id,fileId:f.id})}><td><div className="file-name"><FileIcon kind={f.kind}/><div><strong title={f.name}>{f.name.split('/').pop()}</strong><small>{f.name.includes('/')?f.name.slice(0,f.name.lastIndexOf('/'))+' · ':''}{f.size?size(f.size):f.drive?.export_mime?'Google Workspace file':f.drive?'Size pending':size(f.size)}</small></div></div></td><td><span className="method-label">{['audio','video'].includes(f.kind)?'Media report':f.kind==='unsupported'?'Check format or access':'Whole document'}</span></td><td><span className="page-count">{f.page_count?`${f.completed_pages} / ${f.page_count}`:'—'}</span>{f.page_count>0&&<div className="page-dots" aria-label={`${f.completed_pages} units completed`}>{f.pages.slice(0,16).map(p=><i key={p.number} className={p.status}/>)}{f.pages.length>16&&<small>+{f.pages.length-16}</small>}</div>}</td><td><Badge status={f.source_error&&f.status==='ready'?'blocked':f.status}/>{f.status==='downloading'&&<small className="download-stage">Download, then parse in full</small>}</td><td><button className="icon-button" aria-label={`Preview ${f.name}`} onClick={e=>{e.stopPropagation();setInspector({jobId:job.id,fileId:f.id});}}><ChevronRight size={17}/></button></td></tr>)}</tbody></table>{!filtered.length&&<div className="no-results">No matching files.</div>}</div>}
            <div className="batch-footer"><span><ShieldCheck size={14}/>{job?.ignored?.length?`${job.ignored.length} system metadata files listed as ignored.`:'Starting processing sends documents and audio to OpenAI.'}</span>{job&&!active.includes(job.status)&&job.status!=='ready'?<a className="button secondary small-button" href={`/api/jobs/${job.id}/download`}><ArrowDownToLine size={15}/>Download all .md</a>:<span className="footer-format">.md + integrity manifest</span>}</div>
          </section>
          <div className="page-bottom"><span>Keep the original detail. Make knowledge easier to use.</span><span><span className="status-dot"/>Cloud Markdown · 6 concurrent API calls</span></div>
        </>}

        {view==='history'&&<section className="panel history-panel"><div className="section-head"><h2>All batches <span className="count-pill">{jobs.length}</span></h2><span className="subtle-label">Newest first</span></div>{jobs.length?jobs.map(j=><button className="history-row" key={j.id} onClick={()=>{setSelected(j.id);setView('workbench');setQuery('');setFilter('all');}}><span className="history-zip">{j.source?.type==='google_drive'?<FolderOpen size={23}/>:<FileArchive size={23}/>}</span><div className="history-name"><strong>{j.name}</strong><small>{date(j.created_at)} · {j.files.length} files · {j.concurrency} in parallel</small></div><Badge status={j.status}/><ChevronRight size={18}/></button>):<Empty title="No batches yet" description="Imported batches, progress, and outputs will appear here."/>}</section>}

        {view==='outputs'&&<section className="panel history-panel"><div className="section-head"><h2>Markdown Output</h2><span className="subtle-label">Original names and folders preserved</span></div>{allFiles.filter(f=>f.has_output).length?allFiles.filter(f=>f.has_output).map(f=><div className="output-row" key={f.job.id+f.id}><FileIcon kind={f.kind}/><button className="output-name" onClick={()=>setInspector({jobId:f.job.id,fileId:f.id})}><strong>{f.name}.md</strong><small>{f.job.name}</small></button><Badge status={f.status}/><button className="icon-button" title="Download Markdown" aria-label={`Download ${f.name}`} onClick={()=>downloadFile(f.job,f)}><ArrowDownToLine size={18}/></button></div>):<Empty title="Your outputs will appear here" description="After processing, preview and download Markdown here."/>}</section>}
        {view==='knowledge'&&<Knowledge api={api} toast={toast} onSearch={showSearch} onChat={id=>{localStorage.setItem('folio-library',id);localStorage.removeItem('folio-chat');setView('chat');}}/>}
        <div hidden={view!=='search'}>{searchVisited&&<SearchPanel api={api} active={view==='search'} libraryId={searchLibraryId} onLibraryChange={changeSearchLibrary}/>}</div>
        {view==='chat'&&<ChatPanel api={api} toast={toast} onOpenEvaluations={record=>{localStorage.setItem('folio-evaluation',record.id);setView('evaluation');}}/>}
        {view==='evaluation'&&<EvaluationPanel api={api} toast={toast} onOpenChat={record=>{localStorage.setItem('folio-chat',record.chat_id);localStorage.setItem('folio-library',record.library_id);setView('chat');}}/>}
      </main>
    </div>
    {notice&&<div className={`toast ${notice.error?'error':''}`} role="status">{notice.error?<AlertTriangle size={18}/>:<CheckCheck size={18}/>}<span>{notice.text}</span><button aria-label="Dismiss notification" onClick={()=>setNotice(null)}><X size={16}/></button></div>}
    {inspector&&<Inspector job={jobs.find(j=>j.id===inspector.jobId)} fileId={inspector.fileId} onClose={()=>setInspector(null)} toast={toast}/>}
    {modal&&<div className="modal-backdrop" onClick={()=>setModal(null)}><section className="info-modal" role="dialog" aria-modal="true" aria-label={modal==='help'?'How it works':'Connections & models'} onClick={e=>e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close" onClick={()=>setModal(null)}><X size={21}/></button>{modal==='settings'?<><DriveSettings info={driveInfo} onUpdate={setDriveInfo} api={api} toast={toast}/><h3 className="model-settings-heading">Processing environment</h3><div className="setting-row"><span>OpenAI API key</span><Badge status={health?.key_configured?'completed':'failed'}/></div><div className="setting-row"><span>Document model</span><code>{health?.document_model}</code></div><div className="setting-row"><span>Transcription model</span><code>{health?.transcription_model}</code></div><div className="setting-row"><span>Report model</span><code>{health?.report_model}</code></div><div className="setting-row"><span>Default chat model</span><code>{health?.agent_model}</code></div><div className="setting-row"><span>LibreOffice</span><Badge status={health?.libreoffice?'completed':'failed'}/></div><div className="setting-row"><span>FFmpeg</span><Badge status={health?.ffmpeg?'completed':'failed'}/></div><div className="info-callout">Process 2, 4, or 6 files in parallel. Each document is sent whole; each audio track is transcribed whole and followed by a complete report. Processing shares six API slots.</div></>:<><div className="modal-icon"><CircleHelp size={24}/></div><h2>Every page accounted for</h2><div className="help-step"><b>1</b><div><strong>Choose a Drive folder or ZIP archive</strong><p>Choose Google Drive and share a folder with your service account as a Viewer with downloads enabled, then paste its link. Or choose Upload ZIP and select a local archive. Both create an isolated library.</p></div></div><div className="help-step"><b>2</b><div><strong>Process your files</strong><p>Each document is processed whole, preserving page text, layout, tables, and visual relationships. Audio is transcribed in full, then organized into a report with the original transcript saved separately. Different files run in parallel.</p></div></div><div className="help-step"><b>3</b><div><strong>Review and download</strong><p>Open a file to compare each page with its source. Download Markdown, manifest.json, parsing records, and source metadata. Failed pages are clearly marked.</p></div></div><div className="info-callout">Complete page counts do not guarantee perfect meaning. Review unclear scans, Office layouts, and important figures. Video processing covers the audio track only.</div><div className="help-step"><b>4</b><div><strong>Search and chat with your library</strong><p>After parsing, Markdown is uploaded to your private GCS bucket and indexed for search. Retry storage or indexing from Libraries. Start a conversation and inspect searches, scores, file reads, and activity.</p></div></div><details><summary>Supported formats</summary><p className="extensions">{health?.supported.join(' · ')}</p></details></>}</section></div>}
  </div>;
}

function Empty({title,description}) {return <div className="empty-queue"><span className="empty-icon"><FolderOpen size={27}/></span><strong>{title}</strong><p>{description}</p></div>;}

function Inspector({job,fileId,onClose,toast}) {
  const file=job?.files.find(f=>f.id===fileId);
  const [page,setPage]=useState(1);
  const [mode,setMode]=useState(['audio','video'].includes(file?.kind)?'integration':'compare');
  const [data,setData]=useState(null);
  const [md,setMd]=useState('');
  const [loading,setLoading]=useState(false);
  const closeRef=useRef();
  const base=`/jobs/${job?.id}/files/${fileId}`;
  const media=['audio','video'].includes(file?.kind);
  const pageStatus=file?.pages[page-1]?.status;
  useEffect(()=>{const previous=document.activeElement;closeRef.current?.focus();const handler=e=>dialogKey(e,onClose);document.addEventListener('keydown',handler);const before=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.removeEventListener('keydown',handler);document.body.style.overflow=before;previous?.focus();};},[]);
  useEffect(()=>{
    let current=true;setData(null);setLoading(true);
    if(!file?.page_count){setLoading(false);return;}
    api(`${base}/pages/${page}`).then(r=>{if(current)setData(r);}).catch(e=>toast(e.message,true)).finally(()=>{if(current)setLoading(false);});
    return()=>{current=false;};
  },[page,pageStatus]);
  useEffect(()=>{if(file?.has_output)api(`${base}/markdown`).then(r=>setMd(r.text)).catch(e=>toast(e.message,true));},[file?.status,file?.has_output]);
  if(!file)return null;
  const pageContent = data?.layout?`### Layout & reading order\n\n${data.layout}\n\n### Complete content\n\n${data.markdown}`:data?.markdown||'';
  const content = mode==='raw'?md:mode==='integration'?file.integration||'':pageContent;
  async function copy(){try{await navigator.clipboard.writeText(content);toast('Markdown copied.');}catch{toast('Clipboard access is unavailable. Please download the file.',true);}}
  return <div className="inspector-backdrop" onClick={onClose}><section className="inspector" role="dialog" aria-modal="true" aria-label="Document preview & review" onClick={e=>e.stopPropagation()}>
    <header className="inspector-header"><FileIcon kind={file.kind}/><div><strong>{file.name}</strong><small>{kinds[file.kind]} · {media?'Complete report · transcript saved separately':'Complete document parsing · Page comparison'}</small></div><Badge status={file.status}/><button ref={closeRef} className="icon-button" aria-label="Close preview" onClick={onClose}><X size={21}/></button></header>
    <details className="inspector-cost"><summary>File usage & cost</summary><UsagePanel api={api} jobId={job.id} fileId={fileId} compact title="File usage"/></details>
    <div className="inspector-tools"><div className="segmented"><button className={mode==='compare'?'active':''} onClick={()=>setMode('compare')}><Columns2 size={15}/>{media?'Full transcript':'Page comparison'}</button><button className={mode==='integration'?'active':''} onClick={()=>setMode('integration')}><ListChecks size={15}/>{media?'Complete report':'Document overview'}</button><button className={mode==='raw'?'active':''} onClick={()=>setMode('raw')}><Code2 size={15}/>Full Markdown</button></div><div className="inspector-actions"><button className="icon-button" aria-label="Copy current content" onClick={copy} disabled={!content}><Copy size={16}/></button><button className="button secondary small-button" disabled={!md} onClick={()=>saveText(media&&mode==='compare'?content:md,file.name.split('/').pop()+(media?(mode==='compare'?'.transcript.md':'.report.md'):'.md'))}><ArrowDownToLine size={15}/>Download .md</button></div></div>
    {(file.error||file.source_error||file.warnings.length>0)&&<div className="inspector-alert"><AlertTriangle size={17}/><div>{(file.error||file.source_error)&&<p>{file.error||file.source_error}</p>}{file.warnings.map((w,i)=><p key={i}>{w}</p>)}</div></div>}
    {mode==='integration'?<div className="raw-wrap"><Markdown text={content||(media?'The complete report will appear after transcription.':'An overview will appear after the entire document is parsed.')}/></div>:mode==='raw'?<div className="raw-wrap"><pre>{md||'Full Markdown will be available after processing.'}</pre></div>:<div className="inspector-body"><aside className="page-rail"><span>{media?'tracks':'pages'}</span>{file.pages.map(p=><button key={p.number} className={`${page===p.number?'selected':''} ${p.status}`} onClick={()=>setPage(p.number)} aria-label={`View page ${p.number} ${media?'tracks':'page'}`}><span>{String(p.number).padStart(2,'0')}</span><i/></button>)}</aside><div className="comparison"><div className="preview-pane"><div className="pane-label"><span>{media?'Audio range':'Source PDF'}</span>{!media&&file.page_count>0&&<a href={`/api${base}/pdf#page=${page}`} target="_blank" rel="noreferrer">Open PDF <ExternalLink size={13}/></a>}</div><div className="source-preview">{file.page_count>0?(media?<div className="audio-source"><AudioLines size={46} strokeWidth={1}/><strong>tracks {page}</strong><p>{formatTime(file.pages[page-1]?.start)} — {formatTime(file.pages[page-1]?.end)}</p><small>Full audio track · One API call</small></div>:<img src={`/api${base}/pages/${page}/image`} alt={`Source page ${page}`} key={page}/>):<Empty title="Waiting for source" description={file.error||'Start processing to generate page previews.'}/>}</div></div><div className="preview-pane markdown-pane"><div className="pane-label"><span>MARKDOWN</span>{pageStatus&&<Badge status={pageStatus}/>}</div><div className="markdown-scroll">{loading?<div className="preview-wait"><LoaderCircle className="spin" size={24}/><span>Loading page…</span></div>:content?<>{data?.purpose&&<div className="purpose-note"><strong>Page purpose & context</strong><p>{data.purpose}</p></div>}<Markdown text={content}/></>:<div className="preview-wait"><ScanText size={30} strokeWidth={1.3}/><strong>{pageStatus==='failed'?'Page processing failed':'Content pending'}</strong><p>{file.pages[page-1]?.error||'The transcription will appear after parsing and page validation.'}</p></div>}{data?.issues?.length>0&&<div className="page-issues"><strong><AlertTriangle size={16}/>Manual review needed</strong>{data.issues.map((issue,i)=><p key={i}>{issue}</p>)}</div>}{data?.checks&&<div className="checklist"><span><ListChecks size={16}/>Transcription integrity checks</span>{Object.entries(data.checks).map(([key,value])=><span className={value?'passed':'unpassed'} key={key}>{value?<Check size={13}/>:<AlertTriangle size={13}/>}{{text:'Text',tables:'Tables',visuals:'Images',footnotes:'Footnotes'}[key]}</span>)}<small>Includes model self-checks and local source validation. Review images and meaning where needed.</small></div>}</div></div></div></div>}
    <footer className="inspector-footer"><span><ShieldCheck size={14}/>{file.completed_pages} / {file.page_count} {media?'tracks':'page'} transcribed · {file.review_pages} pages to review</span>{mode==='compare'&&<div><button className="icon-button" aria-label="Previous" disabled={page<=1} onClick={()=>setPage(page-1)}><ChevronLeft size={17}/></button><span>{file.page_count?page:0} / {file.page_count}</span><button className="icon-button" aria-label="Next" disabled={page>=file.page_count} onClick={()=>setPage(page+1)}><ChevronRight size={17}/></button></div>}</footer>
  </section></div>;
}
function dialogKey(event, close) {
  if (event.key === 'Escape') { event.preventDefault(); close(); }
  if (event.key !== 'Tab') return;
  const dialog = document.querySelector('[role="dialog"]');
  const nodes = [...(dialog?.querySelectorAll('button:not(:disabled),a[href],input,select,summary') || [])].filter(el=>el.getClientRects().length);
  const first = nodes[0], last = nodes[nodes.length-1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function formatTime(s=0){return new Date(s*1000).toISOString().slice(11,19);}

createRoot(document.getElementById('root')).render(<App/>);
