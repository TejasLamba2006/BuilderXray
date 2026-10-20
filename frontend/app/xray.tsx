'use client';
import {useState} from 'react';

export type Trace={api:string;target_url?:string;latency_ms?:number|null;status:string;detail?:string};
export type DiffRow={field:string;brochure_promise:string;rera_filing:string;verdict:string;note?:string};
type R=Record<string,any>;

const CRICKET:Record<string,string>={high:'OUT',medium:'DRS',low:'NOT OUT'};
const CRICKET_TONE:Record<string,string>={'OUT':'red','DRS':'amber','NOT OUT':'green'};
const BANNER:Record<string,string>={'OUT':'Thoda ruk jao, bhai','DRS':'Check documents carefully (DRS review)','NOT OUT':'Clean track record (NOT OUT)'};
const MON=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
const MONTH_NAMES=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

const fmt=(d?:string|null)=>{const m=d?.match(/^(\d{4})-(\d{2})/);return m?`${MONTH_NAMES[+m[2]-1]} ${m[1]}`:'—'};
const ym=(d?:string|null)=>{const m=d?.match(/^(\d{4})-(\d{2})/);return m?+m[1]*12+ +m[2]-1:null};

export function cricketOf(r:R){return r.cricket_verdict||CRICKET[r.verdict]||''}
export function scoreOf(r:R):number|null{
  if(typeof r.risk_score==='number'&&r.risk_score>0)return r.risk_score;
  // ponytail: fallback for reports without risk_score; 100 minus 11 per risk point. Replace once backend always sets it.
  return r.verdict==='unknown'?null:Math.max(0,100-(r.score||0)*11);
}

export function ScoreCard({report}:{report:R}){
  const c=cricketOf(report),score=scoreOf(report),tone=CRICKET_TONE[c]||'';
  if(!c&&score===null)return null;
  return <section className={'card xr-score '+tone} aria-label="Cricket scorecard">
    <div className="xr-score-num"><strong>{score??'—'}</strong><span>/ 100</span><small>builder score · higher is safer</small></div>
    <div className="xr-score-mid"><div className="xr-track"><i style={{width:`${score??0}%`}}/></div><p className="xr-banner">{BANNER[c]||'Not assessed. A missing record is not a clean record.'}</p></div>
    {c&&<span className={'xr-cricket '+tone}>{c}</span>}
  </section>;
}

export function diffRows(r:R):DiffRow[]{
  if(r.brochure_diff?.length)return r.brochure_diff;
  const rows:DiffRow[]=[];
  for(const p of r.projects||[]){
    if(p.rera_no)rows.push({field:`RERA no. · ${p.name}`,brochure_promise:'Claimed as registered',rera_filing:p.rera_no,verdict:'MATCH'});
    if(p.original_possession&&p.current_possession)rows.push({field:`Possession · ${p.name}`,brochure_promise:fmt(p.original_possession),rera_filing:fmt(p.current_possession)+(p.revisions?` (${p.revisions} revisions)`:''),verdict:p.original_possession===p.current_possession?'MATCH':'MISMATCH'});
  }
  const pending=(r.complaints||[]).filter((c:R)=>c.status==='pending').length;
  if(rows.length||pending)rows.push({field:'Litigation / complaints',brochure_promise:'No litigation',rera_filing:pending?`${pending} pending complaint(s)`:'No pending complaints in checked sources',verdict:pending?'MISMATCH':'MATCH'});
  return rows;
}

type Hit={clause:string;verdict:'MISMATCH'|'MATCH'|'UNVERIFIED';msg:string};
export function checkClaims(text:string,r:R):Hit[]{
  const projects:R[]=r.projects||[];
  const latest=projects.map(p=>p.current_possession).filter(Boolean).sort().at(-1) as string|undefined;
  const revisions=projects.reduce((n,p)=>n+(p.revisions||0),0);
  const pending=(r.complaints||[]).filter((c:R)=>c.status==='pending').length+[...(r.court_records||[]),...(r.ncdrc_records||[])].filter((c:R)=>c.status==='pending').length;
  const reraNos=projects.map(p=>(p.rera_no||'').toLowerCase()).filter(Boolean);
  const monthRe=new RegExp(`\\b(${MON.join('|')})[a-z]*\\.?[\\s,'-]*(20\\d\\d)\\b`,'i');
  return text.split(/[,;\n]+/).map(s=>s.trim()).filter(Boolean).map((clause):Hit=>{
    const date=clause.match(monthRe),year=clause.match(/\b(20\d\d)\b/);
    if(date||(year&&/possession|handover|delivery|ready|completion/i.test(clause))){
      const claimed=date?+date[2]*12+MON.indexOf(date[1].toLowerCase().slice(0,3)):+year![1]*12+11;
      const rera=ym(latest);
      if(rera===null)return {clause,verdict:'UNVERIFIED',msg:'This report has no RERA possession date to compare. Ask for the RERA certificate.'};
      if(date?claimed===rera:Math.floor(claimed/12)===Math.floor(rera/12))return {clause,verdict:'MATCH',msg:`RERA filing shows ${fmt(latest)}.`};
      return {clause,verdict:'MISMATCH',msg:`RERA filing shows ${fmt(latest)}${revisions?` after ${revisions} date revision(s)`:''}; brochure says ${date?date[0]:year![1]}.`};
    }
    if(/\b(0|zero|no|nil|without)\b[^,;]*\b(litigation|cases?|complaints?|disputes?)\b/i.test(clause))
      return pending?{clause,verdict:'MISMATCH',msg:`Red flag: ${pending} pending complaint/case record(s) found in RERA, consumer and court sources.`}:{clause,verdict:'MATCH',msg:'No pending complaints in the checked sources (court search is manual, so not conclusive).'};
    const no=clause.match(/\b([A-Z]{2,}[\w]*[\/-][\w\/-]{3,})\b/);
    if(no)return reraNos.includes(no[1].toLowerCase())?{clause,verdict:'MATCH',msg:'RERA number appears in the filing.'}:{clause,verdict:'MISMATCH',msg:`${no[1]} is not among the RERA numbers on record (${reraNos.join(', ')||'none'}).`};
    const amen=diffRows(r).find(x=>/amenit/i.test(x.field));
    if(/pool|clubhouse|gym|garden|park|school|mall|club/i.test(clause)&&amen?.verdict==='MISMATCH')return {clause,verdict:'MISMATCH',msg:`RERA/sanctioned plan: ${amen.rera_filing}.`};
    return {clause,verdict:'UNVERIFIED',msg:'RERA data has no matching field. Ask the builder for the sanctioned plan or agreement clause in writing.'};
  });
}

export function Doglapan({report}:{report:R}){
  const rows=diffRows(report),[text,setText]=useState(''),[hits,setHits]=useState<Hit[]|null>(null);
  const flags=hits?.filter(h=>h.verdict==='MISMATCH').length||0;
  return <section className="card xr-wide" id="doglapan">
    <div className="cardhead"><strong>Doglapan Detector · Brochure vs RERA filing</strong><span className={'pill '+(rows.some(r=>r.verdict==='MISMATCH')?'red':'green')}>{rows.filter(r=>r.verdict==='MISMATCH').length} mismatch</span></div>
    {rows.length?<div className="xr-table-wrap"><table className="xr-table"><thead><tr><th>Field</th><th>Brochure Promise</th><th>RERA Official Filing</th><th>Verdict</th></tr></thead><tbody>{rows.map((row,i)=><tr key={i} className={row.verdict==='MISMATCH'?'bad':''}><td>{row.field}</td><td>{row.brochure_promise}</td><td>{row.rera_filing}{row.note&&<small>{row.note}</small>}</td><td><span className={'pill '+(row.verdict==='MISMATCH'?'red':'green')}>{row.verdict}</span></td></tr>)}</tbody></table></div>:<p className="empty-detail">No brochure comparison data for this report.</p>}
    <div className="xr-check">
      <label htmlFor="xr-claim"><strong>Test Brochure Claim (Doglapan Check)</strong><span>Paste any brochure line. Claims are split on commas and checked against this report's RERA data.</span></label>
      <textarea id="xr-claim" value={text} onChange={e=>setText(e.target.value)} placeholder="Possession Dec 2025, Olympic Pool included, 0 litigation" rows={2}/>
      <div className="xr-check-actions"><button className="button primary" disabled={!text.trim()} onClick={()=>setHits(checkClaims(text,report))}>Diff vs RERA</button><button className="button" onClick={()=>{setText('Possession Dec 2025, Olympic Pool included, 0 litigation');setHits(null)}}>Try example</button></div>
      {hits&&<div className="xr-hits" role="status"><p className={flags?'statusbad':'statusok'}><b>{flags?`${flags} red flag(s) found`:'No red flags found'}</b> in {hits.length} claim(s).</p>{hits.map((h,i)=><div className={'xr-hit '+h.verdict} key={i}><span className={'pill '+(h.verdict==='MISMATCH'?'red':h.verdict==='MATCH'?'green':'amber')}>{h.verdict}</span><div><strong>{h.clause}</strong><p>{h.msg}</p></div></div>)}</div>}
    </div>
  </section>;
}

const PRIMS=[
  {label:'Web Agent',re:/agent/i,url:'https://haryanarera.gov.in/',what:'Navigates the RERA portal'},
  {label:'Cloud Browser',re:/browser/i,url:'https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html',what:'Renders MCA company data'},
  {label:'Fetch API',re:/fetch/i,url:'https://api.fetch.tinyfish.ai',what:'Reads project pages as clean text'},
  {label:'Search API',re:/search/i,url:'https://api.search.tinyfish.ai',what:'Finds official pages and news'}];
const TONE:Record<string,string>={ok:'green',running:'amber',failed:'red'};
const WORD:Record<string,string>={ok:'OK',running:'RUNNING',failed:'FAILED',skipped:'SKIPPED'};

export function TracePanel({traces,note}:{traces?:Trace[];note?:string}){
  const rows=PRIMS.map(p=>({p,t:traces?.find(t=>p.re.test(t.api))}));
  const ms=rows.reduce((n,{t})=>n+(t?.latency_ms||0),0);
  return <section className="card xr-wide" id="tinyfish-trace">
    <div className="cardhead"><strong>TinyFish Agent Execution Trace</strong><span className="pill">{rows.filter(r=>r.t?.status==='ok').length}/4 primitives{ms?` · ${(ms/1000).toFixed(1)}s total`:''}</span></div>
    {note&&<p className="small xr-note">{note}</p>}
    {rows.map(({p,t})=><article className="xr-trace" key={p.label}><div className="xr-trace-top"><strong>{p.label}</strong><span className={'pill '+(TONE[t?.status||'']||'')}>{t?WORD[t.status]||t.status.toUpperCase():'NOT RUN'}</span><span className="mono">{t?.latency_ms?`${t.latency_ms.toLocaleString()} ms`:'— ms'}</span></div><a className="mono xr-url" href={t?.target_url||p.url} target="_blank" rel="noreferrer">{t?.target_url||p.url}</a><p>{t?.detail||p.what}</p></article>)}
  </section>;
}

export function Pricing(){
  const tiers=[
    {name:'Homebuyer',price:'₹999',unit:'/ report',fresh:(999-170)/999,cached:(999-15)/999,body:'One cited builder X-ray before you pay a booking amount.'},
    {name:'Bank / HFC Underwriting API',price:'₹299',unit:'/ call',fresh:(299-170)/299,cached:(299-15)/299,body:'Machine-readable risk score and RERA diff inside the loan workflow.'},
    {name:'Broker Pro',price:'₹9,999',unit:'/ month',fresh:null,cached:null,body:`Unlimited fair-use reports. Pays for itself at ${Math.ceil(9999/170)} fresh scans or ${Math.ceil(9999/15)} cached kundli lookups a month.`}];
  const pct=(n:number)=>`${(n*100).toFixed(1).replace(/\.0$/,'')}%`;
  return <div className="xr-pricing">
    <section className="card xr-econ"><div className="cardhead"><strong>Unit economics</strong><span className="pill green">83% to 98.5% gross margin</span></div>
      <div className="xr-econ-grid"><div><span className="eyebrow">Fresh TinyFish scan</span><strong className="xr-big">₹170</strong><p>Search, Fetch, Web Agent and Cloud Browser runs on a never-seen builder.</p></div><div><span className="eyebrow">Cached kundli</span><strong className="xr-big">₹15</strong><p>Repeat lookups served from the stored report with a freshness check.</p></div><div><span className="eyebrow">Margin on ₹999 report</span><strong className="xr-big">83% → 98.5%</strong><p>(₹999 − ₹170) / ₹999 fresh; (₹999 − ₹15) / ₹999 cached.</p></div></div></section>
    <div className="xr-tiers">{tiers.map(t=><section className="card xr-tier" key={t.name}><span className="eyebrow">{t.name}</span><div className="xr-price"><strong>{t.price}</strong><span>{t.unit}</span></div><p>{t.body}</p>{t.fresh!==null&&t.cached!==null&&<div className="xr-margins"><span>Fresh <b>{pct(t.fresh)}</b></span><span>Cached <b>{pct(t.cached)}</b></span></div>}</section>)}</div>
    <p className="small">Costs are team estimates for the hackathon model, not audited figures.</p>
  </div>;
}
