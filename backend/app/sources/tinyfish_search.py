"""Small wrappers for TinyFish's fast Search and Fetch APIs."""
import asyncio
import os
import time
from typing import Any

import httpx


def _key() -> str:
    key = os.getenv('TINYFISH_API_KEY')
    if not key:
        raise RuntimeError('TINYFISH_API_KEY is not configured')
    return key


async def search(query: str, domain: str | None = None, purpose: str = '', limit: int = 8) -> list[dict[str, Any]]:
    """TinyFish Search API; `domain=None` searches the open web."""
    params = {'query': query, 'location': 'IN', 'language': 'en'}
    if domain:
        params['include_domains'] = domain
    if purpose:
        params['purpose'] = purpose
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8)) as client:
        response = await client.get('https://api.search.tinyfish.ai', params=params, headers={'X-API-Key': _key()})
        response.raise_for_status()
        payload = response.json()
    results = payload.get('results', []) if isinstance(payload, dict) else []
    return [item for item in results if isinstance(item, dict)][:limit]


async def search_official(query: str, domain: str, purpose: str, limit: int = 8) -> list[dict[str, Any]]:
    return await search(query, domain, purpose, limit)

async def traced(traces: list[dict] | None, api: str, target: str, coro, url: str = '', summarize=None):
    """Await `coro`, append a {api,target,status,latency_ms,summary,url} row to `traces`, re-raise failures."""
    started = time.perf_counter()
    row = {'api': api, 'target': target, 'status': 'ok', 'latency_ms': 0, 'summary': '', 'url': url}
    try:
        result = await coro
        row['summary'] = summarize(result) if summarize else (f'{len(result)} result(s)' if hasattr(result, '__len__') else 'done')
        return result
    except asyncio.TimeoutError:
        row['status'], row['summary'] = 'timeout', 'timed out'
        raise
    except Exception as exc:
        row['status'], row['summary'] = 'failed', (str(exc).strip() or type(exc).__name__)[:200]
        raise
    finally:
        row['latency_ms'] = int((time.perf_counter() - started) * 1000)
        row['target_url'], row['detail'] = row['url'], row['summary']  # aliases used by saved fixtures
        if traces is not None:
            traces.append(row)

async def fetch_pages(urls: list[str], purpose: str) -> list[dict[str, Any]]:
    if not urls:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(55, connect=8)) as client:
        response = await client.post(
            'https://api.fetch.tinyfish.ai',
            json={'urls': urls[:10], 'format': 'markdown', 'links': True, 'ttl': 0,
                  'per_url_timeout_ms': 45000, 'purpose': purpose},
            headers={'X-API-Key': _key(), 'Content-Type': 'application/json'},
        )
        response.raise_for_status()
        payload = response.json()
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get('results') or payload.get('data') or payload.get('pages')
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return [payload]
    return []


def page_text(page: dict[str, Any]) -> str:
    """Flatten common Fetch response content fields without assuming one API shape."""
    parts: list[str] = []
    for key in ('markdown', 'content', 'text', 'body', 'html', 'title', 'snippet'):
        value = page.get(key)
        if isinstance(value, str):
            parts.append(value)
    nested = page.get('data')
    if isinstance(nested, dict):
        for key in ('markdown', 'content', 'text', 'body', 'html', 'title', 'snippet'):
            value = nested.get(key)
            if isinstance(value, str):
                parts.append(value)
    return '\n'.join(parts)

