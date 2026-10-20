import asyncio, ipaddress, json, os, re, uuid
from typing import Literal
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .models import CheckRequest, Report
from .sources.brochure_diff import diff_report
from . import jobs

app = FastAPI(title='BuilderXray', version='0.2.0')
# Read local development secrets without adding a dotenv runtime dependency.
env_file = Path(__file__).resolve().parents[2] / '.env'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
jobs.load_reports()

class TinyFishSettings(BaseModel):
    api_key: str = Field(min_length=16, max_length=500)

def _require_local_settings(request: Request, check_origin: bool = True):
    host = request.client.host if request.client else ''
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == 'testclient'
    if not local:
        raise HTTPException(403, 'Provider keys can only be changed from this local installation.')
    if check_origin:
        origin = urlparse(request.headers.get('origin',''))
        if origin.hostname not in {'localhost','127.0.0.1','::1'}:
            raise HTTPException(403, 'Open BuilderXray from localhost to change its provider key.')

def _write_env_value(key: str, value: str | None):
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    lines = [line for line in lines if not line.lstrip().startswith(f'{key}=')]
    if value is not None:
        lines.append(f'{key}={value}')
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.touch(mode=0o600, exist_ok=True)
    env_file.chmod(0o600)
    env_file.write_text('\n'.join(lines) + ('\n' if lines else ''))
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value

@app.get('/api/health')
def health(): return {'status':'ok','service':'BuilderXray'}

@app.get('/api/status')
def status(): return {'status':'ok','demo_mode':os.environ.get('DEMO_MODE','auto'),'live_configured':bool(os.environ.get('TINYFISH_API_KEY')),'reports':len(jobs.reports)}

@app.get('/api/settings/tinyfish')
def tinyfish_settings(request: Request):
    _require_local_settings(request, check_origin=False)
    key = os.getenv('TINYFISH_API_KEY', '')
    return {'connected':bool(key), 'hint':f'••••{key[-4:]}' if key else None, 'storage':'local_env'}

@app.put('/api/settings/tinyfish')
def save_tinyfish_settings(body: TinyFishSettings, request: Request):
    _require_local_settings(request)
    key = body.api_key.strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,500}', key):
        raise HTTPException(422, 'That key format is not valid.')
    _write_env_value('TINYFISH_API_KEY', key)
    return {'connected':True, 'hint':f'••••{key[-4:]}', 'storage':'local_env'}

@app.delete('/api/settings/tinyfish')
def remove_tinyfish_settings(request: Request):
    _require_local_settings(request)
    _write_env_value('TINYFISH_API_KEY', None)
    return {'connected':False, 'hint':None, 'storage':'local_env'}

@app.get('/api/reports')
def recent_reports():
    jobs.load_reports()
    return [{'id':r.id,'builder_name':r.builder_name,'state':r.state,'verdict':r.verdict,'generated_at':r.generated_at,'mode':r.mode,'risk_score':r.risk_score,'cricket_verdict':r.cricket_verdict,'revisions':sum(p.revisions or 0 for p in r.projects)} for r in sorted(jobs.reports.values(),key=lambda x:x.generated_at,reverse=True)[:20]]

@app.get('/api/reports/{report_id}')
def get_report(report_id: str):
    jobs.load_reports()
    report = jobs.reports.get(report_id)
    if not report: raise HTTPException(404,'Report not found')
    return report

class DiffRequest(BaseModel):
    builder_name: str = Field(min_length=2, max_length=200)
    brochure_text: str = Field(default='', max_length=20000)
    state: Literal['Haryana', 'Delhi'] = 'Haryana'

@app.post('/api/diff')
def brochure_diff(body: DiffRequest):
    """Doglapan Detector: brochure text vs the newest known RERA-backed report for this builder."""
    jobs.load_reports()
    report = jobs.find_report(body.builder_name, body.state)
    if not report:
        raise HTTPException(404, f'No {body.state} report for "{body.builder_name}" yet. Run a check first, then diff the brochure.')
    return {'builder_name': report.builder_name, 'report_id': report.id, 'mode': report.mode,
            'brochure_diff': diff_report(report, body.brochure_text or None)}

@app.post('/api/check')
async def create_check(body: CheckRequest, request: Request):
    job_id = uuid.uuid4().hex[:12]
    jobs.jobs[job_id] = {'queue':asyncio.Queue(), 'history':[], 'steps':[], 'sources':{}, 'status':'starting', 'created_at':datetime.now(timezone.utc).isoformat(), 'query':body.query, 'state':body.state, 'brochure_text':body.brochure_text}
    asyncio.create_task(jobs.run_job_safe(job_id,body,request.client.host if request.client else 'unknown'))
    return {'job_id':job_id}

@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = jobs.jobs.get(job_id)
    if not job: raise HTTPException(404,'Check not found. It may have expired after a server restart.')
    return {key:job.get(key) for key in ('status','steps','sources','history','created_at','report_id','error','query','state')}

@app.get('/api/jobs/{job_id}/events')
async def job_events(job_id: str):
    job = jobs.jobs.get(job_id)
    if not job: raise HTTPException(404,'Job not found')
    async def stream():
        while True:
            event = await job['queue'].get()
            if event is None: break
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], default=str)}\n\n"
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'out'
if frontend.exists():
    app.mount('/_next',StaticFiles(directory=frontend/'_next'),name='next-assets')
    @app.get('/{path:path}')
    def serve_frontend(path: str):
        candidate = frontend / path
        if path and candidate.is_file(): return FileResponse(candidate)
        static_page = frontend / f'{path.rstrip("/")}.html' if path else frontend / 'index.html'
        if static_page.is_file(): return FileResponse(static_page)
        if path.startswith('report/'):
            report_template = frontend / 'report' / 'saved-dlf.html'
            if report_template.is_file(): return FileResponse(report_template)
        if path.startswith('run/'):
            run_template = frontend / 'run' / 'demo.html'
            if run_template.is_file(): return FileResponse(run_template)
        html = frontend / (path.rstrip('/') + '/index.html' if path else 'index.html')
        if html.is_file(): return FileResponse(html)
        if not path or '.' not in path: return FileResponse(frontend/'index.html')
        raise HTTPException(404,'Not found')
