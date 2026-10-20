"""TinyFish async agent helper for long-running public portal checks."""
import asyncio
import os
from collections.abc import Awaitable, Callable

import httpx

Progress = Callable[[str], Awaitable[None]]


def _error_detail(value) -> str:
    if isinstance(value, dict):
        return str(value.get('message') or value.get('detail') or value)
    return str(value or '').strip()


async def run_agent(url: str, goal: str, output_schema: dict, *, max_duration: int = 120,
                    on_progress: Progress | None = None) -> dict:
    key = os.getenv('TINYFISH_API_KEY')
    if not key:
        raise RuntimeError('TINYFISH_API_KEY is not configured')

    headers = {'X-API-Key': key, 'Content-Type': 'application/json'}
    payload = {
        'url': url,
        'goal': goal,
        'output_schema': output_schema,
        'proxy_config': {'enabled': True, 'type': 'tinyfish', 'country_code': 'IN'},
        'agent_config': {'max_duration_seconds': max_duration},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        try:
            response = await client.post('https://agent.tinyfish.ai/v1/automation/run-async', json=payload, headers=headers)
            response.raise_for_status()
            started = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise RuntimeError(f'TinyFish could not start the portal check: {detail}') from exc

        run_id = started.get('run_id')
        if not run_id:
            raise RuntimeError(_error_detail(started.get('error')) or 'TinyFish did not return a run ID.')

        deadline = asyncio.get_running_loop().time() + max_duration + 45
        last_status = None
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(4)
            try:
                response = await client.get(f'https://agent.tinyfish.ai/v1/runs/{run_id}', headers=headers)
                response.raise_for_status()
                result = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                detail = str(exc).strip() or type(exc).__name__
                raise RuntimeError(f'TinyFish run status could not be read: {detail}') from exc

            status = result.get('status', 'unknown')
            if status != last_status and on_progress:
                await on_progress(f'TinyFish portal run: {status.lower()}')
                last_status = status
            if status == 'COMPLETED':
                data = result.get('result')
                if isinstance(data, dict) and isinstance(data.get('result'), dict):
                    data = data['result']
                if not isinstance(data, dict):
                    raise RuntimeError('TinyFish completed without a structured result.')
                return data
            if status in {'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                detail = _error_detail(result.get('error'))
                raise RuntimeError(detail or f'TinyFish portal run ended with status {status}.')

        raise RuntimeError(f'TinyFish portal run exceeded {max_duration} seconds.')
