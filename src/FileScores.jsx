import React from 'react';
import {gradingName} from './GradingProvider';
import { Links } from './Knowledge';

const modifiedTimeFormat=new Intl.DateTimeFormat('en-US',{timeZone:'UTC',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});

function DriveModifiedTime({value}) {
  const date=value?new Date(value):null;
  return <p className="drive-modified-time">Drive last updated: {date&&!Number.isNaN(date.getTime())?<time dateTime={value} title={value}>{modifiedTimeFormat.format(date)} (UTC)</time>:'Not available'}</p>;
}

export default function FileScores({files=[],ranked=false}) {
  return <div className={`file-scores ${ranked?'search-file-list':''}`}>{files.map((file,index)=><article className="file-score" key={file.document_id}>
    <div className="file-score-heading"><strong>{ranked&&<span className="file-rank" aria-label={`Rank ${index+1}`}>{String(index+1).padStart(2,'0')}</span>}{file.name}</strong><span className={`grade grade-${file.grade??'unknown'}`}>{file.score==null?'Unscored':`${file.score.toFixed(2)} / 3`}</span></div>
    <p>{gradingName(file.judge_provider)} · {file.label} · {file.hit_count} hits{!ranked&&` · RRF ${file.rrf_score?.toFixed(4)}`}</p>
    {file.source_type==='zip'?<p className="drive-modified-time">ZIP upload · SHA-256 source version</p>:<DriveModifiedTime value={file.drive_modified_at}/>}
    {file.error&&<p className="error-text">{file.error}</p>}
    <Links links={file.links} documentId={file.document_id} libraryId={file.library_id}/>
    {file.probabilities&&<details><summary>Relevance score details</summary><div className="grade-probabilities">{Object.entries(file.probabilities).map(([key,p])=><span key={key}>Grade {key}<b>{(p*100).toFixed(1)}%</b></span>)}</div><small>Full text {file.input_chars?.toLocaleString()} characters · {file.model}{file.confidence!=null&&` · Scoring confidence ${Math.round(file.confidence*100)}%`} · RRF {file.rrf_score?.toFixed(4)}</small></details>}
  </article>)}</div>;
}
