import React, { useState } from 'react';
import { Check, Copy, ExternalLink, KeyRound, LoaderCircle, PlugZap } from 'lucide-react';

export default function DriveSettings({ info, onUpdate, api, toast }) {
  const [path, setPath] = useState(info?.credentials_path || '');
  const [busy, setBusy] = useState(false);
  async function run(action) {
    setBusy(true);
    try {
      const next = await api(`/drive/${action}`, {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(action==='configure'?{credentials_path:path}:{})});
      onUpdate(next); toast(next.message);
    } catch(error) {toast(error.message,true);} finally {setBusy(false);}
  }
  async function copy() {
    try {await navigator.clipboard.writeText(info.email);toast('Service account email copied.');}
    catch {toast('Copy failed. Please select and copy the email manually.',true);}
  }
  return <div className="drive-settings">
    <div className="modal-icon"><KeyRound size={24}/></div>
    <h2>Connect Google Drive</h2>
    <p className="settings-intro">Create a service account, then share the source folder with it.</p>
    <ol className="setup-steps">
      <li><strong>Set up a Google Cloud project</strong><p>Choose or create a project and enable the Google Drive API.</p><a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank" rel="noreferrer">Enable Drive API <ExternalLink size={12}/></a></li>
      <li><strong>Create a service account and download its JSON key</strong><p>Open Keys → Add key → Create new key → JSON. Reading shared Drive folders does not require the project Owner or Editor role.</p><a href="https://console.cloud.google.com/iam-admin/serviceaccounts" target="_blank" rel="noreferrer">Manage service accounts <ExternalLink size={12}/></a></li>
      <li><strong>Enter the local credential path</strong><p>The backend reads the private key. Only the file path is saved here; the key is never sent to the browser.</p></li>
    </ol>
    <label className="credential-label" htmlFor="credential-path">Service account JSON path</label>
    <input className="credential-input" id="credential-path" value={path} onChange={e=>setPath(e.target.value)} placeholder="C:/credentials/service-account.json" autoComplete="off" spellCheck="false"/>
    <div className="credential-actions"><button className="button primary" disabled={busy||!path.trim()} onClick={()=>run('configure')}>{busy?<LoaderCircle className="spin" size={15}/>:<KeyRound size={15}/>}Save credential path</button><button className="button secondary" disabled={busy||!info?.configured} onClick={()=>run('test')}><PlugZap size={15}/>Test connection</button></div>
    {info?.email&&<div className="share-account"><span><Check size={14}/>Share your folder with this email and allow downloads</span><div><code>{info.email}</code><button className="icon-button" onClick={copy} aria-label="Copy service account email"><Copy size={15}/></button></div></div>}
    <p className={`credential-status ${info?.authenticated?'verified':''}`}>{info?.message||'Service account credentials are not configured.'}</p>
    <div className="info-callout">Drive sources need Viewer access with downloads enabled. Generated Markdown, transcripts, and reports are stored in GCS. Configure a bucket in Libraries and grant this account Storage Object User on that bucket. ZIP imports also use GCS for generated Markdown.</div>
    <details className="env-tip"><summary>Configure through .env</summary><p>Set <code>GOOGLE_SERVICE_ACCOUNT_FILE</code> to the absolute JSON path and restart. The path saved in this interface takes precedence.</p></details>
  </div>;
}
