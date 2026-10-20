"""Best-effort lookup of public company/LLP master data on the MCA portal."""
import asyncio
import re
from urllib.parse import urlparse
from pydantic import BaseModel

from ..models import RegistryRecord
from .agent import Progress, run_agent
from .tinyfish_search import fetch_pages, page_text, search, traced

# MCA's deep link redirects through a client-side flow that TinyFish cannot
# reliably load. Start on the official homepage and navigate via MCA Services.
MCA_MASTER_DATA_URL = 'https://www.mca.gov.in/content/mca/global/en/home.html'

class CompanyRecord(BaseModel):
    company_name: str
    cin_or_llpin: str | None = None
    company_status: str | None = None
    date_of_incorporation: str | None = None
    source_url: str
    snippet: str

class MCAResult(BaseModel):
    companies: list[CompanyRecord]
    reason: str | None = None

_NULLABLE = {'type': 'string', 'nullable': True}
SCHEMA = {
    'type': 'object',
    'properties': {
        'companies': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'company_name': {'type': 'string'}, 'cin_or_llpin': _NULLABLE,
            'company_status': _NULLABLE, 'date_of_incorporation': _NULLABLE,
            'source_url': {'type': 'string'}, 'snippet': {'type': 'string'},
        }, 'required': ['company_name', 'source_url', 'snippet']}},
        'reason': _NULLABLE,
    }, 'required': ['companies'],
}

def _tokens(value: str) -> set[str]:
    words = set(re.findall(r'[a-z0-9]+', value.casefold()))
    words.difference_update({'limited', 'ltd', 'private', 'pvt', 'company', 'co', 'llp', 'llc', 'the', 'and'})
    return words

async def check_company(query: str, on_progress: Progress | None = None) -> MCAResult:
    tokens = _tokens(query)
    if len(tokens) < 1 or (re.fullmatch(r'[A-Z0-9/-]{10,24}', query.strip()) and any(char.isdigit() for char in query)):
        raise RuntimeError('Enter a company name rather than a RERA number to check MCA master data.')
    goal = (
        'On the official Ministry of Corporate Affairs (MCA) India homepage, open the '
        'MCA Services menu, then Master Data Services, then View Company/LLP Master Data. '
        'Use the public name search to search this exact name: '
        f'"{query}". Return only records visibly shown by the official MCA site. '
        'For each record extract legal company/LLP name, CIN or LLPIN if shown, company status '
        'if shown, incorporation date if shown, the exact MCA page URL, and a short supporting snippet. '
        'Do not use third-party company databases, infer a match, or treat no results as proof that '
        'the entity is not registered. If login, CAPTCHA, or a portal error blocks the lookup, return '
        'an empty companies list and explain why.'
    )
    try:
        data = await run_agent(MCA_MASTER_DATA_URL, goal, SCHEMA, max_duration=20, on_progress=on_progress)
        result = MCAResult.model_validate(data)
        matches = []
        for company in result.companies:
            parsed = urlparse(company.source_url)
            if parsed.scheme != 'https' or parsed.hostname not in {'mca.gov.in', 'www.mca.gov.in'}:
                continue
            found = _tokens(company.company_name)
            if tokens and tokens.issubset(found) and company.snippet.strip():
                matches.append(company)
        if not matches:
            return MCAResult(companies=[], reason=result.reason or 'The MCA portal returned no clearly matched official record. This is not evidence that the company is unregistered.')
        return MCAResult(companies=matches[:5], reason=None)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise RuntimeError(f'MCA master-data check failed: {detail}') from exc


# ---- Live pipeline entry point: official MCA agent + TinyFish Search/Fetch fallback ----------------

THIRD_PARTY = ('zaubacorp.com', 'tofler.in', 'mca.gov.in')
_CIN = re.compile(r'\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b')
_DIRECTORS_SENTENCE = re.compile(r'(?:Directors? of [^.]*? (?:are|is)|has (?:\w+ )?directors? ?[-:])\s*([^.]+?)\.', re.I)
_DIRECTOR_ROW = re.compile(r'\|\s*(?:Whole[- ]time )?(?:Managing )?Directors?\s*\|\s*([^|]+?)\s*\|', re.I)
_STATE = {'HR': 'Haryana', 'DL': 'Delhi', 'MH': 'Maharashtra', 'UP': 'Uttar Pradesh', 'KA': 'Karnataka'}


def _split_names(text: str) -> list[str]:
    names = re.split(r',\s*|\s+and\s+', text)
    return [n.strip(' .') for n in names if 2 < len(n.strip(' .')) < 60 and not re.search(r'\d|\(|cin', n, re.I)][:12]


def parse_search_hit(hit: dict) -> RegistryRecord | None:
    """Turn a zaubacorp/tofler search result into a RegistryRecord using CIN + director text in the snippet."""
    title, snippet, url = str(hit.get('title') or ''), str(hit.get('snippet') or ''), str(hit.get('url') or '')
    host = (urlparse(url).hostname or '').removeprefix('www.')
    if host not in THIRD_PARTY:
        return None
    cin = _CIN.search(url) or _CIN.search(snippet) or _CIN.search(title)
    m = _DIRECTORS_SENTENCE.search(snippet)
    directors = _split_names(m.group(1)) if m else []
    name = re.sub(r'\s*(Financials|\||-\s*(Zauba|Tofler)).*$', '', title, flags=re.I).strip()
    if not name or not (cin or directors):
        return None
    cin_s = cin.group(1) if cin else None
    return RegistryRecord(company_name=name.upper(), cin=cin_s, directors=directors, source=host, source_url=url,
                          snippet=snippet[:400], registered_office=_STATE.get(cin_s[6:8]) if cin_s else None)


def parse_directors_from_page(text: str) -> list[str]:
    seen = []
    for n in _DIRECTOR_ROW.findall(text):
        n = re.sub(r'\s*Shareholder\s*$', '', n).strip()
        if n and n not in seen:
            seen.append(n)
    return seen[:12]


async def _third_party(query: str, traces: list[dict]) -> list[RegistryRecord]:
    purpose = 'Find company CIN and directors for an Indian real-estate developer'
    local: list[list[dict]] = [[] for _ in THIRD_PARTY]
    outcomes = await asyncio.gather(*(
        traced(local[i], 'Search API', f'{d}: {query} CIN directors', search(f'{query} CIN directors', d, purpose, limit=6))
        for i, d in enumerate(THIRD_PARTY)), return_exceptions=True)
    for rows in local:
        traces.extend(rows)
    want = _tokens(query)
    records = [r for o in outcomes if not isinstance(o, Exception) for h in o
               if (r := parse_search_hit(h)) and want and want <= _tokens(r.company_name)]
    # closest name first (fewest extra words), tofler before zauba on ties
    records.sort(key=lambda r: (len(_tokens(r.company_name) - want), r.source != 'tofler.in'))
    seen, unique = set(), []
    for r in records:
        key = r.cin or r.company_name
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique = unique[:3]
    if unique and len(unique[0].directors) < 2:
        # one page fetch for the best match so the director list is not left (nearly) empty
        try:
            pages = await asyncio.wait_for(traced(
                traces, 'Fetch API', unique[0].source, fetch_pages([unique[0].source_url], purpose), unique[0].source_url,
                lambda r: f'{sum(len(page_text(p)) for p in r) // 1000} KB markdown'), 25)
            unique[0].directors = parse_directors_from_page('\n'.join(page_text(p) for p in pages)) or unique[0].directors
        except Exception:
            pass
    return unique


async def _official(query: str, traces: list[dict], on_progress: Progress | None) -> list[RegistryRecord]:
    # Bounded to 25 s so a CAPTCHA wall cannot stall the whole report.
    result = await asyncio.wait_for(traced(
        traces, 'Web Agent', 'mca.gov.in master data', check_company(query, on_progress), MCA_MASTER_DATA_URL,
        lambda r: f'{len(r.companies)} official record(s)' if r.companies else (r.reason or 'no record')[:160]), 25)
    return [RegistryRecord(company_name=c.company_name, cin=c.cin_or_llpin, status=c.company_status,
                           incorporated_on=c.date_of_incorporation, source='mca.gov.in', source_url=c.source_url,
                           snippet=c.snippet[:400]) for c in result.companies]


async def check_registry(query: str, traces: list[dict] | None = None, on_progress: Progress | None = None):
    """Return (records, status, reason, url). Official MCA agent and public-page Search/Fetch run concurrently."""
    traces = traces if traces is not None else []
    agent_rows: list[dict] = []
    page_rows: list[dict] = []
    official, third = await asyncio.gather(_official(query, agent_rows, on_progress), _third_party(query, page_rows),
                                           return_exceptions=True)
    traces.extend(agent_rows + page_rows)
    third = [] if isinstance(third, Exception) else third
    if not isinstance(official, Exception) and official:
        by_cin = {r.cin: r.directors for r in third if r.cin}
        for r in official:  # enrich official records with directors found on public pages
            r.directors = r.directors or by_cin.get(r.cin, [])
        return official, 'ok', None, MCA_MASTER_DATA_URL
    if third:
        return third, 'partial', ('MCA portal blocked or returned no clear record (CAPTCHA protected); CIN and directors come '
                                  'from public company-data pages found via TinyFish Search. Verify on MCA master data.'), MCA_MASTER_DATA_URL
    return [], 'not_checked', ('MCA name search is protected by a CAPTCHA and no public company page matched. '
                               'Open the official portal to check manually.'), MCA_MASTER_DATA_URL
