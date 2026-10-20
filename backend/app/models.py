from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field

class Source(BaseModel):
    id: str
    label: str
    status: Literal['ok','partial','not_checked','running','failed']
    reason: str | None = None
    checked_at: datetime | None = None
    url: str | None = None
class Claim(BaseModel):
    id: str
    text: str
    source_id: str
    source_url: str
    snippet: str | None = None
class Project(BaseModel):
    name: str
    rera_no: str | None = None
    original_possession: date | None = None
    current_possession: date | None = None
    revisions: int | None = None
    lapsed: bool = False
    oc_listed: bool | None = None
    claim_ids: list[str] = []
class Complaint(BaseModel):
    ref: str | None = None
    project: str | None = None
    filed_on: date | None = None
    status: Literal['pending','resolved','unknown']
    claim_id: str
class NewsItem(BaseModel):
    title: str
    outlet: str = ''
    published: date | None = None
    tag: Literal['delay','fraud','litigation','neutral']
    reason: str = ''
    claim_id: str = ''
    url: str = ''
    source: str = ''
    published_at: str | None = None
    sentiment: Literal['delay','fraud','litigation','neutral'] = 'neutral'  # same value as tag
class CourtRecord(BaseModel):
    case_no: str | None = None
    court: str = ''
    party: str | None = None
    status: Literal['pending','disposed','unknown'] = 'unknown'
    kind: Literal['civil','rera','consumer','other'] = 'other'
    summary: str = ''
    filed_on: date | None = None
    source_url: str
    claim_id: str | None = None
class RegistryRecord(BaseModel):
    company_name: str
    cin: str | None = None
    status: str | None = None
    incorporated_on: str | None = None
    registered_office: str | None = None
    category: str | None = None
    directors: list[str] = []
    source: str = ''
    source_url: str
    snippet: str = ''
    claim_id: str | None = None
class Flag(BaseModel):
    label: str
    severity: str
    claim_ids: list[str] = []
class Report(BaseModel):
    id: str
    builder_query: str
    builder_name: str
    state: Literal['Haryana','Delhi'] = 'Haryana'
    mode: Literal['live','saved']
    generated_at: datetime
    verdict: Literal['low','medium','high','unknown']
    score: int = 0
    flags: list[Flag] = []
    projects: list[Project] = []
    complaints: list[Complaint] = []
    news: list[NewsItem] = []
    sources: list[Source] = []
    claims: list[Claim] = []
    summary: str
    summary_claim_ids: list[str] = []
    # Cricket scorecard. score_100 is a SAFETY score: higher = safer (38 = OUT).
    # risk_score mirrors score_100 for clients that use that name.
    # cricket_verdict: "OUT" (<45, high risk), "DRS" (45..70, medium), "NOT OUT" (>70, low).
    risk_score: int = 50
    score_100: int = 50
    cricket_verdict: str = 'DRS'
    scorecard: list[dict] = []        # [{check, runs, out_of, note}] runs sum to score_100
    brochure_diff: list[dict] = []    # [{field, brochure_claim, rera_reality, status, source_url}]
    tinyfish_traces: list[dict] = []  # [{api, target, status, latency_ms, summary, url}]
    ncdrc_records: list[dict] = []    # [{case_no, forum, complainant, status, summary, source_url}]
    court_records: list[CourtRecord] = []
    registry_records: list[RegistryRecord] = []

class CheckRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    state: Literal['Haryana','Delhi'] = 'Haryana'
    mode: Literal['auto','live','saved'] = 'auto'
    passcode: str | None = None
    brochure_text: str | None = Field(default=None, max_length=20000)
