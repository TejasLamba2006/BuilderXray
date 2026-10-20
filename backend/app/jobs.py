"""Check-job orchestration: saved replays, live runs (all sources concurrent), report persistence."""
import asyncio
import ipaddress
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .models import (Claim, Complaint, CheckRequest, NewsItem, Project, RegistryRecord, Report, Source)
from .score import score_report
from .sources.brochure_diff import diff_report
from .sources.court import ECOURTS_REASON, ECOURTS_URL, search_court
from .sources.mca import MCA_MASTER_DATA_URL, check_registry
from .sources.news import search_news
from .sources.rera import PORTALS, check_state

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'data' / 'reports'
try:
    DATA.mkdir(parents=True, exist_ok=True)
except OSError:  # read-only filesystem (Vercel serverless)
    DATA = Path('/tmp/builderxray/reports')
    DATA.mkdir(parents=True, exist_ok=True)
FIXTURES = ROOT.parent / 'fixtures'

jobs: dict[str, dict] = {}
reports: dict[str, Report] = {}
live_today = 0
live_day = datetime.now(timezone.utc).date()
ip_runs: dict[str, list[datetime]] = {}

SAVED_MCA_REASON = 'Saved run; no MCA lookup was made. Open the official master-data service to check manually.'
SOURCE_TIMEOUT = {'rera': 60, 'court': 40, 'news': 40, 'registry': 45}  # seconds; slow source never blocks the report


# ---------------------------------------------------------------- small helpers

def rera_ids(state: str) -> tuple[str, str]:
    """(source id, label) of the state RERA source."""
    return ('rera_dl', 'Delhi RERA') if state == 'Delhi' else ('rera_hr', 'Haryana RERA')


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_rera_url(value: str, state: str = 'Haryana') -> bool:
    allowed = {'Delhi': {'rera.delhi.gov.in', 'www.rera.delhi.gov.in'},
               'Haryana': {'haryanarera.gov.in', 'www.haryanarera.gov.in', 'hareraggm.gov.in', 'www.hareraggm.gov.in'}}
    try:
        parsed = urlparse(value)
        return parsed.scheme == 'https' and parsed.hostname in allowed.get(state, set())
    except ValueError:
        return False


def is_local(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def authorized(req: CheckRequest, ip: str) -> bool:
    """Live access: needs a TinyFish key, then localhost, the LIVE_PASSCODE, or mode='auto' (judges on the deployed URL)."""
    if not os.getenv('TINYFISH_API_KEY'):
        return False
    passcode = os.getenv('LIVE_PASSCODE')
    return is_local(ip) or req.mode == 'auto' or bool(passcode and req.passcode == passcode)


def _normalize(report: Report) -> Report:
    """Fill alias keys so older/hand-written fixtures and live reports expose one consistent shape."""
    for row in report.brochure_diff:
        row.setdefault('brochure_claim', row.get('brochure_promise', ''))
        row.setdefault('rera_reality', row.get('rera_filing', ''))
        row.setdefault('status', row.get('verdict', 'UNVERIFIED'))
        row.setdefault('source_url', '')
    for row in report.tinyfish_traces:
        row.setdefault('target', row.get('target_url', ''))
        row.setdefault('url', row.get('target_url', ''))
        row.setdefault('summary', row.get('detail', ''))
    for row in report.ncdrc_records:
        row.setdefault('complainant', row.get('project', ''))
        row.setdefault('summary', '')
    return report


# ---------------------------------------------------------------- persistence

def _migrate_sources(report: Report) -> None:
    """Upgrade placeholder source rows from older saved reports."""
    rera_id, _ = rera_ids(report.state)
    migrated = []
    for source in report.sources:
        update = {}
        if source.id == rera_id:
            update['url'] = PORTALS[report.state][0]
            if report.mode == 'live' and source.status == 'not_checked' and 'check failed' in (source.reason or '').casefold():
                update['status'] = 'failed'
        if source.id == 'court' and (not source.url or source.reason == 'Coming next'):
            update['url'] = ECOURTS_URL
            if source.reason == 'Coming next':
                update['reason'] = ECOURTS_REASON
        if source.id == 'registry' and (not source.url or source.reason == 'Coming next'):
            update['url'] = MCA_MASTER_DATA_URL
            if source.reason == 'Coming next':
                update['reason'] = ('This older report predates the live MCA lookup. Open MCA Services → Master Data Services → View Company/LLP Master Data.'
                                    if report.mode == 'live' else SAVED_MCA_REASON)
        migrated.append(source.model_copy(update=update))
    report.sources = migrated


def load_reports() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for path in [*FIXTURES.glob('*.json'), *DATA.glob('*.json')]:
        try:
            report = Report.model_validate_json(path.read_text(encoding='utf-8'))
            _migrate_sources(report)
            reports[report.id] = score_report(_normalize(report))
        except Exception:
            pass  # one corrupt file must not stop the rest loading


def fixture_for(query: str, state: str = 'Haryana') -> Report | None:
    q = query.casefold()
    candidates = [r for r in reports.values() if r.state == state and r.mode == 'saved']
    if not candidates:
        return None
    return next((r for r in candidates if r.builder_name.casefold() in q or q in r.builder_name.casefold()), candidates[0])


def find_report(builder: str, state: str = 'Haryana') -> Report | None:
    """Newest known report (saved or live) whose builder name matches `builder`; None if no match."""
    q = builder.casefold().strip()
    matches = [r for r in reports.values() if r.state == state and q and (q in r.builder_name.casefold() or r.builder_name.casefold() in q)]
    return max(matches, key=lambda r: r.generated_at, default=None)


# ---------------------------------------------------------------- job runner

async def run_job_safe(job_id: str, req: CheckRequest, ip: str) -> None:
    try:
        await run_job(job_id, req, ip)
    except Exception as exc:
        job = jobs.get(job_id)
        if not job:
            return
        message = str(exc).replace(os.getenv('TINYFISH_API_KEY') or '__no_key__', '[redacted]')[:500]
        event = {'event': 'error', 'data': {'message': message or 'The check could not be completed.'}}
        job['history'].append(event)
        job['status'] = 'error'
        job['error'] = event['data']['message']
        await job['queue'].put(event)
        await job['queue'].put(None)


class _Emitter:
    """Records every event on the job (for polling) and pushes it to the SSE queue."""

    def __init__(self, job_id: str):
        self.job = jobs[job_id]

    async def __call__(self, kind: str, data: dict) -> None:
        job = self.job
        event = {'event': kind, 'data': data}
        job['history'].append(event)
        if kind in ('step', 'source', 'progress') and job['status'] == 'starting':
            job['status'] = 'running'
        if kind == 'step':
            job['steps'].append(data['text'])
        elif kind == 'source':
            job['sources'][data['id']] = {'status': data['status'], 'reason': data.get('reason'), 'url': data.get('url')}
        elif kind == 'done':
            job['status'], job['report_id'] = 'done', data['report_id']
        elif kind == 'error':
            job['status'], job['error'] = 'error', data.get('message')
        await job['queue'].put(event)


async def run_job(job_id: str, req: CheckRequest, ip: str) -> None:
    global live_today, live_day
    emit = _Emitter(job_id)
    queue = jobs[job_id]['queue']
    await emit('step', {'text': 'Resolving builder name'})
    await asyncio.sleep(.15)

    now = datetime.now(timezone.utc)
    if now.date() != live_day:
        live_today, live_day = 0, now.date()
    configured = bool(os.getenv('TINYFISH_API_KEY'))
    allowed = authorized(req, ip)
    if req.mode == 'live' and not allowed:
        await emit('error', {'message': 'TinyFish is not connected' if not configured else 'Live access code is not valid'})
        await queue.put(None)
        return

    recent = [at for at in ip_runs.get(ip, []) if at > now - timedelta(hours=1)]
    ip_runs[ip] = recent
    over_quota = live_today >= int(os.getenv('MAX_LIVE_RUNS_PER_DAY', '30')) or (len(recent) >= 3 and not is_local(ip))
    saved = req.mode == 'saved' or os.getenv('DEMO_MODE', 'auto') == 'replay' or not allowed or over_quota

    if saved:
        report = await _saved_report(job_id, req, emit)
    else:
        live_today += 1
        ip_runs[ip].append(now)
        report = await _live_report(job_id, req, emit)

    report = score_report(_normalize(report))
    reports[report.id] = report
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f'{report.id}.json').write_text(report.model_dump_json(indent=2), encoding='utf-8')
    await emit('step', {'text': f'Report ready: {report.cricket_verdict} · {report.risk_score}/100'})
    await emit('done', {'report_id': report.id})
    await queue.put(None)


# ---------------------------------------------------------------- saved replay

async def _saved_report(job_id: str, req: CheckRequest, emit: _Emitter) -> Report:
    rera_id, rera_label = rera_ids(req.state)
    now = datetime.now(timezone.utc)
    await emit('step', {'text': 'Using a saved example; no external sources will be contacted'})
    fixture = fixture_for(req.query, req.state)

    if not fixture:
        sources = [
            Source(id=rera_id, label=rera_label, status='not_checked', reason='Saved run; no live portal check was made', checked_at=now),
            Source(id='news', label='News search', status='not_checked', reason='Saved run; no live news search was made', checked_at=now),
            Source(id='court', label='Court records', status='not_checked', reason=ECOURTS_REASON, url=ECOURTS_URL),
            Source(id='registry', label='Company registry', status='not_checked', reason=SAVED_MCA_REASON, url=MCA_MASTER_DATA_URL),
        ]
        for s in sources:
            await emit('source', {'id': s.id, 'status': s.status, 'reason': s.reason, 'url': s.url})
        report = Report(id=job_id, builder_query=req.query, builder_name=req.query, state=req.state, mode='saved', generated_at=now,
                        verdict='unknown', sources=sources,
                        summary=f'No saved {req.state} example is available for this builder. No live check was made.')
        report.brochure_diff = diff_report(report, req.brochure_text)
        return report

    report = fixture.model_copy(deep=True, update={'id': job_id, 'builder_query': req.query, 'state': req.state, 'mode': 'saved', 'generated_at': now})
    report.summary = f'Saved {req.state} example from {fixture.generated_at.date().isoformat()}. {fixture.summary}'
    if req.brochure_text:
        report.brochure_diff = diff_report(report, req.brochure_text)
    for s in report.sources:
        await emit('source', {'id': s.id, 'status': s.status, 'reason': s.reason, 'url': s.url})
    return report


# ---------------------------------------------------------------- live run

async def _guarded(coro, timeout: int, emit: _Emitter, label: str):
    """Run one source; a failure or timeout becomes a returned Exception, never a crash."""
    try:
        result = await asyncio.wait_for(coro, timeout)
        await emit('step', {'text': f'{label} returned; validating its result'})
        return result
    except asyncio.TimeoutError:
        await emit('step', {'text': f'{label} timed out after {timeout}s; recording it as not checked'})
        return TimeoutError(f'{label} timed out after {timeout}s')
    except Exception as exc:
        await emit('step', {'text': f'{label} could not be read; recording it as not checked'})
        return exc


async def _live_report(job_id: str, req: CheckRequest, emit: _Emitter) -> Report:
    rera_id, rera_label = rera_ids(req.state)
    portal_url = PORTALS[req.state][0]
    now = datetime.now(timezone.utc)
    traces: list[dict] = []

    await emit('source', {'id': rera_id, 'status': 'running', 'url': portal_url})
    await emit('source', {'id': 'news', 'status': 'running'})
    await emit('source', {'id': 'court', 'status': 'running', 'url': ECOURTS_URL})
    await emit('source', {'id': 'registry', 'status': 'running', 'url': MCA_MASTER_DATA_URL})
    await emit('step', {'text': f'Querying {rera_label}, court records, news and the company registry in parallel'})

    async def progress(label: str):
        async def report_progress(text: str):
            await emit('progress', {'text': f'{label} · {text}'})
        return report_progress

    tasks = {
        'RERA portal': _guarded(check_state(req.query, req.state, on_progress=await progress(rera_label), traces=traces),
                                SOURCE_TIMEOUT['rera'], emit, 'RERA portal'),
        'court search': _guarded(search_court(req.query, traces), SOURCE_TIMEOUT['court'], emit, 'Court search'),
        'news search': _guarded(search_news(f'{req.query} {req.state}', traces), SOURCE_TIMEOUT['news'], emit, 'News search'),
        'company registry': _guarded(check_registry(req.query, traces, on_progress=await progress('MCA')),
                                     SOURCE_TIMEOUT['registry'], emit, 'Company registry'),
    }
    started = asyncio.get_running_loop().time()
    gathered = asyncio.gather(*tasks.values(), return_exceptions=True)
    while True:  # heartbeat so the UI shows life during slow portals
        done, _ = await asyncio.wait([gathered], timeout=4)
        if done:
            break
        await emit('progress', {'text': f'Waiting for sources · {int(asyncio.get_running_loop().time() - started)}s'})
    rera_res, court_res, news_res, registry_res = gathered.result()

    claims: list[Claim] = []

    def add_claim(text: str, source_id: str, url: str, snippet: str | None) -> str:
        claim_id = f'c{len(claims) + 1}'
        claims.append(Claim(id=claim_id, text=text, source_id=source_id, source_url=url, snippet=(snippet or '')[:500] or None))
        return claim_id

    # ---- RERA
    projects: list[Project] = []
    complaints: list[Complaint] = []
    rera_reason: str | None = None
    if isinstance(rera_res, Exception):
        rera_reason = str(rera_res)
    else:
        for item in rera_res.projects:
            if not _is_rera_url(item.source_url, req.state):
                rera_reason = f'Portal result included an invalid or non-{req.state} source URL.'
                claims.clear(); projects.clear(); complaints.clear()
                break
            cid = add_claim(f'{rera_label} lists {item.name} for {req.query}.', rera_id, item.source_url, item.snippet)
            projects.append(Project(name=item.name, rera_no=item.rera_no, original_possession=_as_date(item.original_possession),
                                    current_possession=_as_date(item.current_possession), revisions=item.revisions or 0,
                                    lapsed=item.lapsed, oc_listed=item.oc_listed, claim_ids=[cid]))
            for c in item.complaints:
                if not _is_rera_url(c.source_url, req.state):
                    continue
                ccid = add_claim(f'{rera_label} complaint record for {c.project or item.name} is marked {c.status}.', rera_id, c.source_url, c.snippet)
                status = c.status.casefold()
                complaints.append(Complaint(ref=c.ref, project=c.project, filed_on=_as_date(c.filed_on),
                                            status=status if status in ('pending', 'resolved') else 'unknown', claim_id=ccid))
        if not projects and not rera_reason:
            rera_reason = rera_res.reason or 'No projects were returned for this builder; absence of results is not treated as a clean record.'
    rera_status = 'ok' if projects else 'failed'
    if projects:
        dated = sum(1 for p in projects if p.current_possession)
        rera_reason = (f'{len(projects)} official registered project(s); {dated} with a filed completion date, '
                       f'{sum(p.revisions or 0 for p in projects)} extension/supersede marker(s), {len(complaints)} complaint record(s).')

    # ---- court + consumer forums
    court_records, ncdrc_records = ([], []) if isinstance(court_res, Exception) else court_res
    for rec in court_records:
        rec.claim_id = add_claim(f'Court/authority record ({rec.kind}, {rec.status}): {rec.summary[:160]}', 'court', rec.source_url, rec.summary)
    for row in ncdrc_records:
        row['claim_id'] = add_claim(f'{row["forum"]} case {row["case_no"]} ({row["status"]}) names this builder.', 'court',
                                    row['source_url'], row['summary'])
    if isinstance(court_res, Exception):
        court_status, court_reason = 'not_checked', f'{ECOURTS_REASON} (Search failed: {court_res})'
    else:
        court_status = 'partial'  # eCourts party search stays manual, so never "complete"
        court_reason = (f'{len(court_records)} court/authority record(s) and {len(ncdrc_records)} consumer-forum case(s) found via TinyFish Search. '
                        'Official eCourts party-name search needs a CAPTCHA; open it for the complete list.')
        if not court_records and not ncdrc_records:
            court_reason = 'No indexed judgments or consumer cases matched. This is not proof none exist. ' + ECOURTS_REASON

    # ---- news
    news: list[NewsItem] = [] if isinstance(news_res, Exception) else news_res
    for n in news:
        n.claim_id = add_claim(f'{n.outlet}: "{n.title}" ({n.tag}).', 'news', n.url, n.reason)
    news_status = 'not_checked' if isinstance(news_res, Exception) else ('ok' if news else 'partial')
    news_reason = (str(news_res) if isinstance(news_res, Exception)
                   else f'{len(news)} relevant item(s), keyword-classified (delay / litigation / fraud / neutral).' if news
                   else 'TinyFish Search returned no relevant news for this builder.')

    # ---- registry
    if isinstance(registry_res, Exception):
        registry, registry_status, registry_reason, registry_url = [], 'not_checked', str(registry_res), MCA_MASTER_DATA_URL
    else:
        registry, registry_status, registry_reason, registry_url = registry_res
    for rec in registry:
        bits = [rec.company_name, f'CIN {rec.cin}' if rec.cin else '', f'status {rec.status}' if rec.status else '',
                f'directors {", ".join(rec.directors[:5])}' if rec.directors else '']
        rec.claim_id = add_claim(f'{rec.source} lists ' + ', '.join(b for b in bits if b) + '.', 'registry', rec.source_url, rec.snippet)
    if registry_status == 'ok':
        registry_reason = f'MCA master data matched {len(registry)} record(s).'

    sources = [
        Source(id=rera_id, label=rera_label, status=rera_status, reason=rera_reason, checked_at=now, url=portal_url),
        Source(id='news', label='News search', status=news_status, reason=news_reason, checked_at=now),
        Source(id='court', label='Court records', status=court_status, reason=court_reason, checked_at=now, url=ECOURTS_URL),
        Source(id='registry', label='Company registry', status=registry_status, reason=registry_reason, checked_at=now, url=registry_url),
    ]
    for s in sources:
        await emit('source', {'id': s.id, 'status': s.status, 'reason': s.reason, 'url': s.url})
    await emit('step', {'text': 'Comparing brochure promises with the RERA filing'})

    report = Report(
        id=job_id, builder_query=req.query, builder_name=registry[0].company_name.title() if registry and rera_status != 'ok' else req.query,
        state=req.state, mode='live', generated_at=now, verdict='unknown', sources=sources, claims=claims, projects=projects,
        complaints=complaints, news=news, court_records=court_records, ncdrc_records=ncdrc_records, registry_records=registry,
        tinyfish_traces=traces, summary='',
        summary_claim_ids=[c.id for c in claims if c.source_id in (rera_id, 'registry')][:12])
    report.brochure_diff = diff_report(report, req.brochure_text)
    await emit('step', {'text': 'Assembling the cited report'})
    report = score_report(report)
    report.summary = _summary(report, rera_label)
    return report


def _summary(report: Report, rera_label: str) -> str:
    lines = [f'{rera_label}: {len(report.projects)} registered project(s), {sum(p.revisions or 0 for p in report.projects)} possession-date revision marker(s), '
             f'{sum(1 for c in report.complaints if c.status == "pending")} pending complaint(s).',
             f'{len(report.court_records)} court record(s), {len(report.ncdrc_records)} consumer-forum case(s), {len(report.news)} news item(s).']
    if report.registry_records:
        r = report.registry_records[0]
        lines.append(f'Registry: {r.company_name}' + (f' ({r.cin})' if r.cin else '') + (f', {len(r.directors)} director(s) listed.' if r.directors else '.'))
    if report.verdict == 'unknown':
        lines.append('Not enough official data to score. This is not a clean record.')
    else:
        lines.append(f'Scorecard {report.risk_score}/100: {report.cricket_verdict} ({report.verdict} risk).')
    lines.append('eCourts and MCA party searches are CAPTCHA-protected; use the official links for the complete record.')
    return ' '.join(lines)
