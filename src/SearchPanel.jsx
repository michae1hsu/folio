import React, { useEffect, useRef, useState } from 'react';
import { Search, LoaderCircle, AlertTriangle, FileText, SlidersHorizontal } from 'lucide-react';
import FileScores from './FileScores';
import GradingProvider,{gradingName} from './GradingProvider';

export default function SearchPanel({api,active,libraryId,onLibraryChange}) {
  const [libraries,setLibraries]=useState([]),[query,setQuery]=useState(''),[topK,setTopK]=useState(100),[judgeProvider,setJudgeProvider]=useState(()=>localStorage.getItem('folio-search-judge')==='openai_decisions'?'openai_decisions':'jev');
  const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[loadError,setLoadError]=useState('');
  const request=useRef(0),currentLibrary=useRef(libraryId);
  currentLibrary.current=libraryId;
  useEffect(()=>{
    if(!active)return;
    let alive=true;
    async function refresh(){
      try{
        const data=await api('/knowledge');
        if(!alive)return;
        setLibraries(data.libraries);setLoadError('');
        if(!data.libraries.some(l=>l.id===currentLibrary.current))onLibraryChange(data.libraries[0]?.id||'');
      }catch(e){if(alive)setLoadError(e.message);}
    }
    refresh();const timer=setInterval(refresh,5000);
    return()=>{alive=false;clearInterval(timer);};
  },[active]);
  useEffect(()=>{request.current+=1;setResult(null);setError('');setBusy(false);},[libraryId]);
  useEffect(()=>()=>{request.current+=1;},[]);
  const library=libraries.find(l=>l.id===libraryId);
  const shown=result?.library_id===libraryId?result:null;
  async function search(event){
    event.preventDefault();
    if(busy||!query.trim()||!library?.indexed_files)return;
    const id=++request.current,targetLibrary=libraryId;
    setBusy(true);setError('');setResult(null);
    try{
      const data=await api('/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({library_id:targetLibrary,query:query.trim(),top_k:Number(topK),judge_provider:judgeProvider})});
      if(id===request.current&&currentLibrary.current===targetLibrary)setResult(data);
    }catch(e){if(id===request.current&&currentLibrary.current===targetLibrary)setError(e.message);}
    finally{if(id===request.current)setBusy(false);}
  }
  return <div className="search-view">
    <section className="panel search-form-panel">
      <form onSubmit={search}>
        <div className="search-library"><label htmlFor="search-library">Search library</label><select id="search-library" value={libraryId} onChange={e=>onLibraryChange(e.target.value)}>{!libraries.length&&<option value="">Import a Drive folder or ZIP first</option>}{libraries.map(l=><option key={l.id} value={l.id}>{l.name} · {l.id.slice(0,6)}</option>)}</select><p>{library?`${library.indexed_files} searchable documents`:'No libraries yet'}</p></div>
        <label htmlFor="file-search-query" className="search-query-label">What would you like to find?</label>
        <textarea id="file-search-query" value={query} onChange={e=>setQuery(e.target.value)} maxLength={8000} rows={4} placeholder="For example: find documents describing a project schedule, budget, and technical requirements." disabled={!library} required/>
        <div className="search-grading"><GradingProvider value={judgeProvider} onChange={value=>{setJudgeProvider(value);localStorage.setItem('folio-search-judge',value);}} disabled={busy}/></div><div className="search-submit-row"><p>Search and score full documents, then browse files by relevance.</p><button className="button primary" type="submit" disabled={busy||!query.trim()||!library?.indexed_files}>{busy?<LoaderCircle className="spin" size={16}/>:<Search size={16}/>}Search files</button></div>
        <details className="search-options"><summary><SlidersHorizontal size={13}/>Search settings</summary><label htmlFor="search-top-k">Maximum retrieved chunks<input id="search-top-k" type="number" min={1} max={500} required value={topK} onChange={e=>setTopK(e.target.value)} disabled={busy}/></label><p>Chunks are retrieved, grouped by document, then scored using the full text. Search and scoring incur API usage.</p></details>
      </form>
      {loadError&&<p className="search-error" role="alert"><AlertTriangle size={16}/>{loadError}</p>}
      {library&&!library.indexed_files&&<p className="search-error" role="status"><AlertTriangle size={16}/>Finish indexing this library before searching.</p>}
    </section>
    <section className="panel search-results" aria-label="File search results" aria-busy={busy}>
      <header><h2>Files{shown&&<span className="count-pill">{shown.files.length}</span>}</h2><span>Highest relevance first</span></header>
      {busy?<div className="search-empty" role="status"><LoaderCircle className="spin" size={28}/><h3>Searching and scoring</h3><p>Scoring the complete candidate documents. Results will appear when ready.</p></div>:error?<div className="search-empty search-error" role="alert"><AlertTriangle size={28}/><h3>Search incomplete</h3><p>{error}</p></div>:shown?<>
        <div className="search-result-summary"><strong>「{shown.query}」</strong><p>{shown.library_name} · {shown.recalled_chunks} matching chunks · {shown.files.length} files · {gradingName(shown.judge_provider)} grading</p></div>
        {!!shown.failed_evaluations&&<p className="search-rating-warning" role="status"><AlertTriangle size={15}/>{shown.failed_evaluations} files could not be scored and remain at the end of the list.</p>}
        {shown.files.length?<FileScores files={shown.files} ranked/>:<div className="search-empty"><FileText size={28}/><h3>No results in this library</h3><p>Try another query or increase the retrieved chunk limit.</p></div>}
        <p className="search-score-note">Relevance scores range from 0 to 3. Scores and confidence do not measure factual accuracy. Unscored documents have unknown relevance. Drive timestamps refer to the indexed source version; reimport to include later changes. ZIP sources have no verified Drive timestamp.</p>
      </>:<div className="search-empty"><Search size={28}/><h3>Find files by what you need</h3><p>Choose a library and enter a query to see relevance, sources, and download links.</p></div>}
    </section>
  </div>;
}
