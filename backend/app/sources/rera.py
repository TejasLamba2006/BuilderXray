"""Fast, source-restricted RERA lookups using TinyFish Search and Fetch."""
import re
from datetime import date
from urllib.parse import urlparse

from .agent import Progress
from .tinyfish_search import fetch_pages, page_text, search_official, traced
from pydantic import BaseModel, Field

class PortalComplaint(BaseModel):
    ref: str | None = None
    project: str | None = None
    filed_on: str | None = None
    status: str = 'unknown'
    source_url: str
    snippet: str

class PortalProject(BaseModel):
    name: str
    rera_no: str | None = None
    original_possession: str | None = None
    current_possession: str | None = None
    revisions: int | None = Field(default=0, ge=0)
    lapsed: bool = False
    oc_listed: bool | None = None
    source_url: str
    snippet: str
    complaints: list[PortalComplaint] = []

class PortalResult(BaseModel):
    projects: list[PortalProject]
    checked_url: str | None = None
    reason: str | None = None

_NULLABLE_STRING = {'type':'string','nullable':True}
SCHEMA = {'type':'object','properties':{'projects':{'type':'array','items':{'type':'object','properties':{
    'name':{'type':'string'},'rera_no':_NULLABLE_STRING,'original_possession':_NULLABLE_STRING,'current_possession':_NULLABLE_STRING,'revisions':{'type':'integer'},'lapsed':{'type':'boolean'},'oc_listed':{'type':'boolean','nullable':True},'source_url':{'type':'string'},'snippet':{'type':'string'},
    'complaints':{'type':'array','items':{'type':'object','properties':{'ref':_NULLABLE_STRING,'project':_NULLABLE_STRING,'filed_on':_NULLABLE_STRING,'status':{'type':'string'},'source_url':{'type':'string'},'snippet':{'type':'string'}},'required':['status','source_url','snippet']}}
},'required':['name','revisions','source_url','snippet','complaints']}},'checked_url':_NULLABLE_STRING,'reason':_NULLABLE_STRING},'required':['projects']}

PORTALS = {
    'Haryana': ('https://haryanarera.gov.in/', 'Haryana Real Estate Regulatory Authority', {'haryanarera.gov.in','www.haryanarera.gov.in','hareraggm.gov.in','www.hareraggm.gov.in'}),
    'Delhi': ('https://rera.delhi.gov.in/', 'Real Estate Regulatory Authority for the NCT of Delhi', {'rera.delhi.gov.in','www.rera.delhi.gov.in'}),
}

_REVISION_WORDS = re.compile(r'supersede|extend|extension|revised|revision', re.I)
_DMY = re.compile(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})')
_INITIAL = re.compile(r'Initial date of completion[^|]*\|[^|]*\|\s*\*{0,2}(\d{1,2}-\d{1,2}-\d{4})', re.I)
_LIKELY = re.compile(r'Likely date of completion[^|]*\|[^|]*\|\s*\*{0,2}(\d{1,2}-\d{1,2}-\d{4})', re.I)
_COMPLAINT_REF = re.compile(r'(?<![A-Za-z0-9/])([A-Z]{1,6}(?:/[A-Za-z0-9.-]+){2,})')

def _iso(value: str) -> str | None:
    """dd/mm/yyyy or dd-mm-yyyy -> ISO date, or None."""
    m = _DMY.search(value or '')
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None

def parse_row_facts(row: str) -> dict:
    """Facts readable from one registered-projects table row: validity date, revisions, lapsed, OC link."""
    cells = [c.strip() for c in row.split('|')]
    valid_upto = next((iso for c in reversed(cells) if (iso := _iso(c)) and len(c) <= 10), None)
    return {
        'current_possession': valid_upto,  # registration validity is the filed completion horizon
        'revisions': len(_REVISION_WORDS.findall(row)),
        'lapsed': 'lapsed' in row.casefold(),
        'oc_listed': 'oc/cc/pcc' in row.casefold() and 'view oc/cc/pcc' in row.casefold(),
    }

def parse_detail_page(text: str) -> dict:
    """Possession dates, date-diff revisions and complaint rows from a fetched project/order page."""
    initial = [_iso(m) for m in _INITIAL.findall(text)]
    likely = [_iso(m) for m in _LIKELY.findall(text)]
    pairs = [(i, l) for i, l in zip(initial, likely) if i and l]
    complaints = []
    for line in text.splitlines():
        low = line.casefold()
        if 'complaint' not in low:
            continue
        status = 'pending' if re.search(r'pending|admitted|hearing|not disposed', low) else 'resolved' if re.search(r'disposed|resolved|decided|closed', low) else None
        ref = _COMPLAINT_REF.search(line)
        if status and ref:
            complaints.append({'ref': ref.group(1), 'status': status, 'snippet': line.strip()[:300]})
    return {
        'original_possession': min((i for i, _ in pairs), default=None),
        'current_possession': max((l for _, l in pairs), default=None),
        'revisions': sum(1 for i, l in pairs if l > i),
        'complaints': complaints,
    }

def enrich_projects(projects: list[PortalProject], pages: dict[str, str]) -> None:
    """Apply detail-page facts to projects whose RERA number or name appears on that page."""
    for url, text in pages.items():
        if not text or 'registered_projects' in url.casefold():
            continue
        low = text.casefold()
        facts = None
        for project in projects:
            if not ((project.rera_no and project.rera_no.casefold() in low) or project.name.casefold() in low):
                continue
            facts = facts or parse_detail_page(text)
            project.original_possession = facts['original_possession'] or project.original_possession
            project.current_possession = facts['current_possession'] or project.current_possession
            project.revisions = (project.revisions or 0) + facts['revisions']
            for c in facts['complaints']:
                project.complaints.append(PortalComplaint(ref=c['ref'], project=project.name, status=c['status'], source_url=url, snippet=c['snippet']))

def _registered_rows(text: str, query_tokens: set[str], query: str, is_rera_id: bool) -> list[PortalProject]:
    """Read records from the official registered-projects table, not its search snippet."""
    lines = [line.strip() for line in text.splitlines() if '|' in line]
    headers = None
    found = []
    for line in lines:
        cells = [re.sub(r'[*`]', '', cell).strip() for cell in line.strip('|').split('|')]
        lowered = [cell.casefold() for cell in cells]
        if 'project name' in lowered and 'builder' in lowered and 'project id' in lowered:
            headers = {name: lowered.index(name) for name in ('project name', 'builder', 'project id')}
            continue
        if not headers or all(not cell or set(cell) <= {'-', ':'} for cell in cells):
            continue
        if max(headers.values()) >= len(cells):
            continue
        project_name = cells[headers['project name']]
        builder = cells[headers['builder']]
        project_id = cells[headers['project id']]
        if not project_name or not project_id:
            continue
        project_tokens = set(re.findall(r'[a-z0-9]+', project_name.casefold()))
        builder_tokens = set(re.findall(r'[a-z0-9]+', builder.casefold()))
        if is_rera_id:
            matched = query.casefold() in project_id.casefold()
        else:
            matched = bool(query_tokens) and (query_tokens.issubset(builder_tokens) or query_tokens.issubset(project_tokens))
        if not matched:
            continue
        row = ' | '.join(cells)
        found.append(PortalProject(name=project_name[:200], rera_no=project_id, source_url='',
                                   snippet=row[:500], complaints=[], **parse_row_facts(row)))
    return found

async def check_state(query: str, state: str = 'Haryana', on_progress: Progress | None = None,
                      traces: list[dict] | None = None) -> PortalResult:
    if state not in PORTALS: raise ValueError(f'Unsupported RERA state: {state}')
    portal_url, portal_name, allowed_hosts = PORTALS[state]
    domain = urlparse(portal_url).hostname
    is_rera_id = bool(re.fullmatch(r'[A-Za-z0-9/-]{8,32}', query.strip()) and any(c.isdigit() for c in query))
    search_query = (f'"{query}"' if is_rera_id else f'"{query}" {state} RERA registered project promoter')
    purpose = f'Find official {state} RERA project registration records matching {query}. Prefer individual project detail pages.'
    try:
        if on_progress: await on_progress('Searching official RERA records')
        results = await traced(traces, 'Search API', f'{domain} · {query}', search_official(search_query, domain, purpose, limit=8), portal_url)
        candidates = []
        for item in results:
            url = str(item.get('url') or '')
            parsed = urlparse(url)
            if parsed.scheme == 'https' and parsed.hostname in allowed_hosts:
                candidates.append(item)
        if not candidates:
            return PortalResult(projects=[], checked_url=portal_url,
                                reason=f'TinyFish Search returned no {state} RERA pages for this query. No record was validated; this is not evidence that no record exists.')
        if on_progress: await on_progress(f'Opening {min(5, len(candidates))} official record pages')
        fetch_urls = [str(item['url']) for item in candidates[:5]]
        pages = await traced(traces, 'Fetch API', f'{len(fetch_urls)} RERA page(s)', fetch_pages(fetch_urls, purpose), fetch_urls[0],
                             lambda r: f'{len(r)} page(s), {sum(len(page_text(p)) for p in r)//1000} KB markdown')
        pages_by_url = {str(page.get('url') or page.get('requested_url') or ''): page for page in pages}
        projects = []
        detail_text = {u: page_text(p) for u, p in pages_by_url.items()}
        tokens = {t for t in re.findall(r'[a-z0-9]+', query.casefold()) if t not in {'limited','ltd','private','pvt','company','the','and','llp'}}
        for item in candidates[:5]:
            url = str(item['url'])
            # Only the official registered-projects table proves a registration. Form previews
            # and monitoring orders may be applications or unrelated case material.
            if 'registered_projects' not in urlparse(url).path.casefold():
                continue
            page = pages_by_url.get(url, {})
            text = page_text(page)
            if not text:
                text = '\n'.join(str(item.get(k) or '') for k in ('title','snippet'))
            if not text.strip():
                continue
            matches = _registered_rows(text, tokens, query, is_rera_id)
            projects.extend(project.model_copy(update={'source_url': url}) for project in matches)
        # De-duplicate pages and keep the result small enough to review.
        unique = {project.rera_no or f'{project.source_url}:{project.name}': project for project in projects}
        enrich_projects(list(unique.values()), detail_text)
        return PortalResult(projects=list(unique.values())[:20], checked_url=portal_url,
                            reason=None if unique else f'{portal_name} pages were searched, but no exact project record for {query} could be validated. A missing match is not a clean record.')
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RuntimeError(f'{state} RERA check failed: {detail}') from exc

async def check_haryana(query: str) -> PortalResult:
    """Backward-compatible alias for existing callers."""
    return await check_state(query, 'Haryana')
