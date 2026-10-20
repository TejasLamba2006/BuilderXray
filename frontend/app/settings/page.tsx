'use client';
import {useEffect, useState} from 'react';
import type {FormEvent} from 'react';
import {Footer, Header} from '../components';

type Connection = {connected:boolean;hint:string|null};

export default function SettingsPage(){
  const [connection,setConnection]=useState<Connection|null>(null);
  const [apiKey,setApiKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const [loadError,setLoadError]=useState('');
  async function refresh(){
    try{const r=await fetch('/api/settings/tinyfish',{signal:AbortSignal.timeout(5000)});if(!r.ok)throw new Error('The local API is unavailable. Start FastAPI and try again.');setConnection(await r.json());setLoadError('')}
    catch(e){setLoadError(e instanceof Error?e.message:'Could not load connection status')}
  }
  useEffect(()=>{void refresh()},[]);
  async function save(e:FormEvent){
    e.preventDefault();setBusy(true);setError('');setMessage('');
    try{const r=await fetch('/api/settings/tinyfish',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:apiKey})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Could not save the key');setConnection(data);setApiKey('');setMessage('Key saved. The next live check will verify it with TinyFish.')}
    catch(e){setError(e instanceof Error?e.message:'Could not save the key')}
    finally{setBusy(false)}
  }
  async function disconnect(){
    setBusy(true);setError('');setMessage('');
    try{const r=await fetch('/api/settings/tinyfish',{method:'DELETE'});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Could not remove the key');setConnection(data);setMessage('Key removed from this local installation. New checks will use saved examples.')}
    catch(e){setError(e instanceof Error?e.message:'Could not remove the key')}
    finally{setBusy(false)}
  }
  return <main className="app"><Header/><div className="shell settings-shell"><div className="settings-heading"><span className="eyebrow">Connections</span><h1>Bring your own TinyFish key</h1><p>Connect the browser automation service for live RERA and news checks.</p></div>
    <div className="settings-grid"><section className="card connection-card"><div className="cardhead"><strong>TinyFish</strong><span className={'pill '+(connection?.connected?'green':'')}>{connection===null?'Checking…':connection.connected?'Key saved':'Not connected'}</span></div><p className="settings-copy">Your key is stored by this local BuilderXray server in the project’s <code>.env</code> file. The full key is never sent back to the browser or saved in browser storage.</p>
      {connection?.connected&&<div className="key-status"><span className="connection-dot"/><span>Saved key</span><code>{connection.hint}</code></div>}
      <form onSubmit={save} className="key-form"><label htmlFor="tinyfish-key">{connection?.connected?'Replace API key':'TinyFish API key'}</label><input id="tinyfish-key" type="password" autoComplete="new-password" spellCheck={false} value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder={connection?.connected?'Paste a new key to rotate it':'Paste your TinyFish API key'} required minLength={16} maxLength={500}/><div className="settings-actions"><button className="button primary" disabled={busy||apiKey.trim().length<16}>{busy?'Saving…':connection?.connected?'Save new key':'Save and connect'}</button>{connection?.connected&&<button type="button" className="button danger-quiet" onClick={disconnect} disabled={busy}>Disconnect</button>}</div>
      </form>{loadError&&<p className="form-message error" role="alert">{loadError}</p>}{error&&<p className="form-message error" role="alert">{error}</p>}{message&&<p className="form-message success" role="status">{message}</p>}
    </section><aside className="card setup-card"><div className="cardhead"><strong>How connection works</strong></div><ol className="setup-steps"><li><span>01</span><div><b>Paste your key</b><p>Use the API key from your TinyFish account.</p></div></li><li><span>02</span><div><b>Keep it on this machine</b><p>BuilderXray writes it to the local server’s environment file with owner-only permissions.</p></div></li><li><span>03</span><div><b>Run a check</b><p>The next check uses TinyFish automatically. If a public portal cannot be read, the report says “Not checked.”</p></div></li></ol></aside></div>
    <div className="settings-notice"><strong>Local setup</strong><p>This screen is for a single local installation, not a user account system. It is available only from localhost. For a hosted deployment, set <code>TINYFISH_API_KEY</code> in your host’s secret manager. TinyFish keys are API credentials, so this flow does not pretend to be OAuth.</p></div><Footer/></div></main>
}
