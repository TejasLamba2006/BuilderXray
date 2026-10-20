"""Builder news via TinyFish Search. Keyword classification only; works without any LLM key."""
import re
from datetime import date, datetime

from ..models import NewsItem
from .tinyfish_search import search, traced

# Order matters: first matching tag wins, so the most serious comes first.
_TAGS = (
    ('fraud', r'fraud|cheat|scam|forgery|money laundering|\bED\b|\bCBI\b|arrest|ponzi|diver(?:t|sion)'),
    ('litigation', r'\bcourt\b|\bNCLT\b|insolven|bankrupt|NCDRC|tribunal|\bsuit\b|\bFIR\b|petition|\bRERA\b.{0,40}(?:penalty|order|fine)'),
    ('delay', r'delay|possession|late handover|stalled|behind schedule|missed deadline|refund|protest'),
)
_NOISE = {'limited', 'ltd', 'private', 'pvt', 'company', 'the', 'and', 'llp', 'builders', 'developers', 'realty', 'india'}


def tokens(name: str) -> set[str]:
    return {t for t in re.findall(r'[a-z0-9]+', name.casefold()) if t not in _NOISE}


def classify(text: str) -> str:
    for tag, pattern in _TAGS:
        if re.search(pattern, text, re.I):
            return tag
    return 'neutral'


def _published(raw) -> date | None:
    for fmt in ('%d %b %Y', '%d %B %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None  # relative ("7 months ago") or localised dates stay text-only in published_at


def items_from_results(builder: str, results: list[dict]) -> list[NewsItem]:
    want = tokens(builder)
    seen, items = set(), []
    for r in results:
        url = str(r.get('url') or '')
        title = str(r.get('title') or '').strip()
        snippet = str(r.get('snippet') or '')
        blob = f'{title}. {snippet}'
        if not url or url in seen or not title or (want and not want & tokens(blob)):
            continue
        seen.add(url)
        tag = classify(blob)
        outlet = re.sub(r'^www\.', '', str(r.get('site_name') or ''))
        items.append(NewsItem(
            title=title[:200], outlet=outlet, source=outlet, url=url, tag=tag, sentiment=tag,
            published_at=str(r['date']) if r.get('date') else None, published=_published(r.get('date')),
            reason=snippet[:300],
        ))
    return items


async def search_news(query: str, traces: list[dict] | None = None) -> list[NewsItem]:
    name = re.sub(r'\s+(Haryana|Delhi)$', '', query.strip())
    results: list[dict] = []
    # two queries: adverse-signal heavy and general; merged and de-duplicated downstream
    for q in (f'"{name}" builder possession delay OR fraud OR court', f'"{name}" real estate news'):
        results += await traced(traces, 'Search API', f'news: {q}',
                                search(q, None, 'Find recent news about a real-estate developer', limit=8))
    return items_from_results(name, results)[:12]
