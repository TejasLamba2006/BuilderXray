"""Court / consumer-forum signals via TinyFish Search, plus a manual eCourts hand-off.

Party-name eCourts searches need a CAPTCHA, so official eCourts stays a manual link.
Search hits (Indian Kanoon judgments, NCDRC / RERA orders) are real public documents with real URLs.
"""
import asyncio
import re

from ..models import CourtRecord
from .news import tokens
from .tinyfish_search import search, traced

ECOURTS_URL = 'https://services.ecourts.gov.in/ecourtindia_v6/?p=casestatus%2Findex'
ECOURTS_REASON = (
    'eCourts requires a court jurisdiction and registration year, then a CAPTCHA for party-name searches. '
    'BuilderXray cannot complete that official search automatically. Open eCourts to check manually.'
)
DOMAINS = ('indiankanoon.org', 'ecourts.gov.in', 'rera.haryana.gov.in', 'ncdrc.nic.in')
_CASE_NO = re.compile(r'(?:Consumer Case No\.?|Complaint Case No\.?|Appeal No\.?|\bCC)\s*[:/]?\s*(\d{1,6}\s*(?:/|of)\s*\d{4})', re.I)


def _split(title: str) -> tuple[str, str]:
    head = re.split(r'\s+on\s+\d', title)[0]
    parts = re.split(r'\s+vs\.?\s+', head, maxsplit=1, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ('', head.strip())


def _status(text: str) -> str:
    low = text.casefold()
    if re.search(r'dismiss|allowed|disposed|directed|decreed|refund|struck off', low):
        return 'disposed'
    if re.search(r'pending|listed for|next date|notice issued|admitted', low):
        return 'pending'
    return 'unknown'


def _kind(text: str, url: str) -> str:
    low = text.casefold()
    if 'ncdrc' in url or re.search(r'consumer|ncdrc|deficiency in service', low):
        return 'consumer'
    if 'rera' in low or 'rera' in url:
        return 'rera'
    if re.search(r'civil suit|\bsuit\b|injunction|decree', low):
        return 'civil'
    return 'other'


def parse_results(builder: str, results: list[dict]) -> tuple[list[CourtRecord], list[dict]]:
    want = tokens(builder)
    court, ncdrc, seen = [], [], set()
    for r in results:
        url = str(r.get('url') or '')
        title = str(r.get('title') or '')
        snippet = str(r.get('snippet') or '')
        blob = f'{title} {snippet}'
        if not url or url in seen or (want and not want & tokens(blob)):
            continue
        seen.add(url)
        kind, status = _kind(blob, url), _status(blob)
        complainant, respondent = _split(title)
        m = _CASE_NO.search(blob)
        case_no = m.group(1).replace(' ', '') if m else None
        if kind == 'consumer':
            low = blob.casefold()
            forum = ('NCDRC' if 'national consumer' in low or 'ncdrc' in low
                     else 'State Consumer Commission' if 'state' in low and 'commission' in low
                     else 'District Consumer Commission' if 'district' in low else 'Consumer forum')
            ncdrc.append({'case_no': case_no or title[:60], 'forum': forum, 'complainant': complainant or 'Not stated',
                          'status': status, 'summary': snippet[:300], 'source_url': url})
        else:
            court.append(CourtRecord(
                case_no=case_no, court='Indian Kanoon judgment' if 'indiankanoon' in url else 'Court / authority order',
                party=complainant or respondent or None, status=status if status != 'unknown' else 'unknown', kind=kind,
                summary=f'{title[:120]}. {snippet[:250]}', source_url=url))
    return court[:8], ncdrc[:8]


async def search_court(builder: str, traces: list[dict] | None = None) -> tuple[list[CourtRecord], list[dict]]:
    """Return (court_records, ncdrc_records). Raises only if every domain search failed."""
    name = re.sub(r'\s+(Haryana|Delhi)$', '', builder.strip())
    query = f'{name} consumer complaint OR RERA OR civil suit'
    purpose = f'Find court or consumer-forum cases against {name}'
    local: list[list[dict]] = [[] for _ in DOMAINS]
    outcomes = await asyncio.gather(
        *(traced(local[i], 'Search API', f'{d}: {query}', search(query, d, purpose, limit=8)) for i, d in enumerate(DOMAINS)),
        return_exceptions=True)
    if traces is not None:
        for rows in local:
            traces.extend(rows)
    if all(isinstance(o, Exception) for o in outcomes):
        raise RuntimeError(f'Court searches failed: {outcomes[0]}')
    return parse_results(name, [r for o in outcomes if not isinstance(o, Exception) for r in o])
