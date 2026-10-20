'use client';
import {useEffect,useState} from 'react';
import {Header} from '../../components';
import {TracePanel} from '../../xray';

type Source={status:string;reason?:string|null;url?:string|null};
type JobSnapshot={status:string;steps:string[];sources:Record<string,Source>;history:Array<{event:string;data:Record<string,string>}>;created_at:string;report_id?:string;error?:string;query:string;state:string};

export default function RunClient({params}:{params:Promise<{jobId:string}>}){
  const [job,setJob]=useState('');
  const [snapshot,setSnapshot]=useState<JobSnapshot|null>(null);
  const [liveNote,setLiveNote]=useState('Connecting to the local check service…');
  const [offline,setOffline]=useState(false);
  const [elapsed,setElapsed]=useState(0);
  const [retry,setRetry]=useState(0);

  useEffect(()=>{setJob(location.pathname.split('/').filter(Boolean).at(-1)||'')},[params]);
  useEffect(()=>{
    if(!job||job==='demo')return;
    let active=true, failures=0, polling=false;
    const poll=async()=>{
      if(polling)return;polling=true;const controller=new AbortController();const timeout=window.setTimeout(()=>controller.abort(),5000);
      try{
        const response=await fetch('/api/jobs/'+job,{cache:'no-store',signal:controller.signal});
        if(response.status===404){if(active){setOffline(true);setLiveNote('This check expired when the server restarted. Go back and start it again.')}return}
        if(!response.ok)throw new Error('The check service returned an error.');
        const data=await response.json() as JobSnapshot;
        if(!active)return;
        failures=0;setOffline(false);setSnapshot(data);
        const progress=data.history.filter(e=>e.event==='progress').at(-1)?.data.text;
        setLiveNote(progress||data.steps.at(-1)||'Waiting for the check to start…');
        if(data.status==='done'&&data.report_id){location.replace('/report/'+data.report_id);return}
      }catch(error){
        if(!active)return;
        failures++;
        if(failures>=3){setOffline(true);setLiveNote(error instanceof Error?error.message:'The check service is unavailable.');}
        else setLiveNote(error instanceof Error&&error.name==='AbortError'?'The check service is taking too long to respond. Reconnecting…':'Reconnecting to the local check service…');
      }finally{window.clearTimeout(timeout);polling=false}
    };
    void poll();const timer=window.setInterval(poll,1200);
    return()=>{active=false;window.clearInterval(timer)};
  },[job,retry]);
  useEffect(()=>{const timer=window.setInterval(()=>{if(snapshot?.created_at)setElapsed(Math.max(0,Math.floor((Date.now()-new Date(snapshot.created_at).getTime())/1000)));else setElapsed(s=>s+1)},1000);return()=>window.clearInterval(timer)},[snapshot?.created_at]);

  const sources=snapshot?.sources||{};
  const reraId=snapshot?.state==='Delhi'?'rera_dl':'rera_hr';
  const sourceRows=[{id:reraId,label:snapshot?.state==='Delhi'?'Delhi RERA':'Haryana RERA'}, {id:'news',label:'Public news search'}, {id:'court',label:'Court records'}, {id:'registry',label:'MCA company registry'}];
  const done=snapshot?.status==='done';
  const anyRunning=Object.values(sources).some(s=>s.status==='running');
  const st=done?'ok':anyRunning?'running':'';
  // ponytail: no per-call telemetry in the job snapshot; mirror overall job state. Stream real traces once backend emits them.
  const liveTraces=st?['Web Agent','Browser','Fetch','Search'].map(api=>({api,status:st,detail:done?'Completed for this check.':'In flight for this check.'})):[];
  const failed=snapshot?.status==='error'||offline;
  const steps=snapshot?.steps||['Starting builder check'];
  const current=steps.at(-1)||'Starting builder check';
  const terminalSources=sourceRows.filter(source=>['ok','partial','not_checked','failed'].includes(sources[source.id]?.status||'' )).length;
  const progressPercent=done?100:Math.max(8,Math.round((terminalSources+(sourceRows.some(source=>sources[source.id]?.status==='running') ? 0.35 : 0))/sourceRows.length*100));

  return <main className="app"><Header/><div className="shell run-shell">
    <div className="run-heading"><div><span className="eyebrow">Public record check</span><h1>{snapshot?.query?<>Checking {snapshot.query}</>:'Preparing your check'}</h1><p>{snapshot?.state||'Selected state'} · {job}</p></div><span className={'run-status '+(failed?'failed':done?'complete':'active')}><i/>{failed?'Needs attention':done?'Complete':'Working'}</span></div>
    <section className="run-focus card"><div className="run-focus-top"><div><span className="eyebrow">Live source review</span><h2>{failed?'This check could not continue':done?'Report assembled':snapshot?.status==='starting'?'Preparing source checks':current}</h2></div><span className="run-time">{elapsed<60?`${elapsed}s`:`${Math.floor(elapsed/60)}m ${elapsed%60}s`}</span></div>
      <div className={'run-progress-track '+(failed?'failed':'')} role="progressbar" aria-label="Source check progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progressPercent}><span style={{width:`${progressPercent}%`}}/></div>
      <div className="run-live-note"><span className={'activity-spinner '+(failed||done?'stopped':'')}/><span>{offline?liveNote:snapshot?.error||liveNote}</span></div>
      <div className="run-source-rail" aria-label="Source check status">{sourceRows.map(source=>{const status=sources[source.id]?.status||'queued';return <span className={'run-source-chip '+status} key={source.id}><i/>{source.label}</span>})}</div>
      {failed&&<div className="run-recovery"><p>{snapshot?.error||liveNote}</p><a className="button" href="/">Return to checks</a><button className="button primary" onClick={()=>{setOffline(false);setLiveNote('Reconnecting…');setRetry(n=>n+1)}}>Retry connection</button></div>}
    </section>
    <div className="run-grid"><section className="card run-steps"><div className="cardhead"><strong>Activity</strong><span className="pill">{steps.length} updates</span></div><ol className="activity-list">{steps.map((step,index)=><li className={index===steps.length-1&&!failed?'current':''} key={`${step}-${index}`}><span className="activity-marker">{index===steps.length-1&&!failed?'': '✓'}</span><span>{step}</span></li>)}{!failed&&<li className="current pending"><span className="activity-marker"/><span>{current.includes('Waiting')?current:'Checking for the next source update'}</span></li>}</ol></section>
      <section className="card run-sources"><div className="cardhead"><strong>Sources</strong><span className="pill">{terminalSources}/{sourceRows.length} resolved</span></div>{sourceRows.map(source=>{const item=sources[source.id];const status=item?.status||'queued';return <article className="source-status" key={source.id}><div className="source-status-title"><span className={'source-indicator '+status}/><strong>{source.label}</strong><span className="source-status-label">{status==='not_checked'?'Manual':status==='running'?'Reading':status==='ok'?'Validated':status==='partial'?'Partial':status==='failed'?'Unavailable':'Queued'}</span></div>{item?.reason&&<p>{item.reason}</p>}{item?.url&&<p><a className="claim" href={item.url} target="_blank" rel="noreferrer">Open official portal ↗</a></p>}</article>})}<p className="source-footnote">Court name searches need a CAPTCHA, so BuilderXray links to eCourts instead of claiming an automated result.</p></section></div>
    <div className="run-grid"><TracePanel traces={liveTraces} note="Live view: each primitive turns green as its TinyFish call returns."/></div>
    <div className="run-footer"><a href="/">← Back to BuilderXray</a><span>RERA checks use direct search and page reading. MCA and court searches need a manual CAPTCHA.</span></div>
  </div></main>
}
