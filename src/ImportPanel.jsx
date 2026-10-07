import React, { useRef, useState } from 'react';
import { CloudDownload, FileArchive, Link2, LoaderCircle, ShieldCheck, ChevronRight, Upload } from 'lucide-react';

export default function ImportPanel({api, driveInfo, onDriveUpdate, onSettings, onImported, onBusyChange, toast}) {
  const [source, setSource] = useState('drive');
  const [folder, setFolder] = useState('');
  const [recursive, setRecursive] = useState(true);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const input = useRef(null);

  function chooseFile(candidate) {
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith('.zip')) return toast('Choose a .zip file.', true);
    if (candidate.size > 512 * 1024 * 1024) return toast('ZIP upload exceeds the 512 MiB limit.', true);
    setFile(candidate);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); onBusyChange(true);
    try {
      const created = source === 'drive'
        ? await api('/drive/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({folder, recursive})})
        : await api(`/zip/import?filename=${encodeURIComponent(file.name)}`, {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: file});
      if (source === 'drive') onDriveUpdate(info => ({...info, authenticated: true, message: 'Google Drive connected.'}));
      await onImported(created);
      toast(`${created.files.length} files ready. Select Start processing to send their content to OpenAI.`);
      if (source === 'zip') { setFile(null); if (input.current) input.current.value = ''; }
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); onBusyChange(false); }
  }

  return <section className="drive-card panel" id="source-intake" tabIndex={-1}>
    <div className="section-head"><h2><span className="step-number">01</span>Add your files</h2><span className="subtle-label">SOURCE → MARKDOWN</span></div>
    <div className="source-switch" role="group" aria-label="Import source">
      <button type="button" aria-pressed={source === 'drive'} disabled={busy} onClick={() => setSource('drive')}><CloudDownload size={17}/>Google Drive</button>
      <button type="button" aria-pressed={source === 'zip'} disabled={busy} onClick={() => setSource('zip')}><FileArchive size={17}/>Upload ZIP</button>
    </div>
    <form onSubmit={submit}>
      {source === 'drive' ? <>
        <div className="drive-identity"><span className="drive-cloud"><CloudDownload size={26} strokeWidth={1.5}/></span><div><strong>Connect a folder</strong><span>{driveInfo?.email || 'Read folders shared with your service account'}</span></div><button type="button" className="button secondary small-button" onClick={onSettings}>Settings<ChevronRight size={13}/></button></div>
        <label className="folder-label" htmlFor="folder-link">Google Drive folder link or ID</label>
        <div className="folder-input"><Link2 size={17}/><input id="folder-link" value={folder} onChange={e => setFolder(e.target.value)} placeholder="https://drive.google.com/drive/folders/…" autoComplete="off" spellCheck="false" disabled={busy}/></div>
        <div className="folder-options"><label><input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)} disabled={busy}/>Include subfolders</label><span>Up to 500 files</span></div>
      </> : <>
        <div className="zip-dropzone" onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); if (!busy) chooseFile(e.dataTransfer.files[0]);}}>
          <Upload size={26} strokeWidth={1.5}/><strong>{file ? file.name : 'Choose a ZIP archive'}</strong>
          <p>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MiB · ready to import` : 'Drop a file here, or browse your computer.'}</p>
          <input ref={input} id="zip-file" type="file" accept=".zip,application/zip" disabled={busy} onChange={e => chooseFile(e.target.files[0])}/>
          <label className="sr-only" htmlFor="zip-file">ZIP archive</label>
        </div>
        <p className="zip-limits">512 MiB upload · 2 GiB expanded · up to 500 source files. Folder structure is preserved.</p>
      </>}
      <div className="drive-submit"><span><ShieldCheck size={14}/>{source === 'drive' ? 'Source files stay in Google Drive.' : 'Original files stay on this server.'}</span><button className="button primary" type="submit" disabled={busy || (source === 'drive' ? !folder.trim() || !driveInfo?.configured : !file)}>{busy ? <LoaderCircle size={16} className="spin"/> : source === 'drive' ? <CloudDownload size={16}/> : <Upload size={16}/>} {busy ? 'Importing…' : source === 'drive' ? 'Read folder' : 'Import ZIP'}</button></div>
    </form>
    <div className="drive-formats"><span>{source === 'drive' ? 'Supports Google Docs, Sheets, Slides, documents, images, audio, and video.' : 'No Drive folder needed. Cloud Markdown and library search use your configured GCS bucket.'}</span></div>
  </section>;
}
