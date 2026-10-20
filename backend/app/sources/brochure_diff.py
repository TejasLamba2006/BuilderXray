"""Doglapan Detector: deterministic brochure promise vs RERA filing diff. No LLM, no network."""
import re
from datetime import date

from ..models import Report

_MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
_DATE = r'(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*)?(20\d{2})'
_POSSESSION = re.compile(r'(?:possession|hand\s?over|delivery|completion|ready by)[^.\n]{0,60}?' + _DATE, re.I)
_NO_LITIGATION = re.compile(r'no litigation|litigation[- ]free|zero litigation|dispute[- ]free|clear title|clean title', re.I)
_APPROVALS = re.compile(r'all approvals|fully approved|approvals? (?:in place|received|obtained)|approved by|rera[- ]approved|rera[- ]registered|phase[- ]?\w+ (?:launched|approved)', re.I)
_OC_ESCROW = re.compile(r'occupation certificate|\bOC\b|ready[- ]to[- ]move|escrow|70\s?%', re.I)

# Used when no brochure text is supplied: the promises almost every launch brochure makes.
TYPICAL = {
    'Possession Date': 'On-time possession as promised at launch (typical brochure promise)',
    'Approvals / Phase': 'All approvals in place (typical brochure promise)',
    'Litigation Disclosure': 'Clear title, no litigation (typical brochure promise)',
    'Escrow / OC Status': 'Funds safe in RERA escrow, OC on time (typical brochure promise)',
}


def parse_brochure(text: str) -> dict[str, str]:
    """Pull the sentence containing each promise type out of brochure text."""
    claims = {}
    sentences = re.split(r'(?<=[.!?\n])\s+', text or '')
    for field, pattern in (('Possession Date', _POSSESSION), ('Litigation Disclosure', _NO_LITIGATION),
                           ('Approvals / Phase', _APPROVALS), ('Escrow / OC Status', _OC_ESCROW)):
        hit = next((s.strip() for s in sentences if pattern.search(s)), None)
        if hit:
            claims[field] = hit[:240]
    return claims


def _brochure_date(sentence: str) -> date | None:
    m = _POSSESSION.search(sentence)
    if not m:
        return None
    return date(int(m.group(2)), _MONTHS.get((m.group(1) or 'dec').lower()[:3], 12), 28)


def _fmt(d) -> str:
    return d.strftime('%b %Y') if isinstance(d, date) else 'not stated'


def _row(field, claim, reality, status, url):
    # brochure_promise / rera_filing / verdict mirror the saved-fixture key names
    return {'field': field, 'brochure_claim': claim, 'rera_reality': reality, 'status': status, 'source_url': url,
            'brochure_promise': claim, 'rera_filing': reality, 'verdict': status}


def diff_report(report: Report, brochure_text: str | None = None) -> list[dict]:
    """Compare brochure promises (supplied or typical) against everything the report found."""
    claims = parse_brochure(brochure_text) if brochure_text else {}
    given = bool(claims)
    claim_for = lambda field: claims.get(field) or (TYPICAL[field] if not given else 'Not mentioned in the brochure text')
    claim_url = {c.id: c.source_url for c in report.claims}
    portal = next((s.url for s in report.sources if s.id in ('rera_hr', 'rera_dl') and s.url), '')
    url_for = lambda p: next((claim_url[i] for i in p.claim_ids if i in claim_url), portal)
    rows = []

    # 1. Possession date
    projects = report.projects
    if projects:
        worst = max(projects, key=lambda p: (p.revisions or 0, p.current_possession or date.min))
        revs = worst.revisions or 0
        reality = f'{worst.name}: RERA possession {_fmt(worst.current_possession)}'
        if worst.original_possession:
            reality += f' (originally {_fmt(worst.original_possession)})'
        reality += f', {revs} revision(s)'
        promised = _brochure_date(claims.get('Possession Date', ''))
        if promised and worst.current_possession:
            status = 'MISMATCH' if worst.current_possession > promised.replace(day=28) and (worst.current_possession - promised).days > 90 else 'MATCH'
        else:
            status = 'MISMATCH' if revs else ('MATCH' if worst.original_possession else 'UNVERIFIED')
        rows.append(_row('Possession Date', claim_for('Possession Date'), reality, status, url_for(worst)))
    else:
        rows.append(_row('Possession Date', claim_for('Possession Date'), 'No RERA project record was retrieved to compare against', 'UNVERIFIED', portal))

    # 2. Approvals / phase: lapsed or phase-revised registrations contradict "all approvals in place"
    if projects:
        lapsed = [p for p in projects if p.lapsed]
        phase_revised = [p for p in projects if (p.revisions or 0) and not p.lapsed]
        if lapsed:
            rows.append(_row('Approvals / Phase', claim_for('Approvals / Phase'),
                             f'{len(lapsed)} of {len(projects)} RERA registration(s) marked lapsed, e.g. {lapsed[0].name}', 'MISMATCH', url_for(lapsed[0])))
        else:
            rows.append(_row('Approvals / Phase', claim_for('Approvals / Phase'),
                             f'{len(projects)} project(s) registered with RERA, none marked lapsed'
                             + (f'; {len(phase_revised)} with revised timelines' if phase_revised else ''),
                             'MATCH', url_for(projects[0])))
    else:
        rows.append(_row('Approvals / Phase', claim_for('Approvals / Phase'), 'No RERA registration found for this builder name', 'UNVERIFIED', portal))

    # 3. Litigation disclosure
    pending = [c for c in report.complaints if c.status == 'pending']
    cases = [r for r in report.court_records] + report.ncdrc_records
    n_cases = len(cases)
    adverse = [n for n in report.news if n.tag in ('litigation', 'fraud')]
    parts = []
    if pending: parts.append(f'{len(pending)} pending RERA complaint(s)')
    if n_cases: parts.append(f'{n_cases} court / consumer case(s) found')
    if adverse: parts.append(f'{len(adverse)} adverse news item(s)')
    lit_url = next(
        (r.source_url if hasattr(r, 'source_url') else r.get('source_url') for r in cases), None) or (adverse[0].url if adverse and adverse[0].url else portal)
    if parts:
        rows.append(_row('Litigation Disclosure', claim_for('Litigation Disclosure'), ', '.join(parts), 'MISMATCH', lit_url))
    else:
        searched = any(s.id in ('court', 'news') and s.status in ('ok', 'partial') for s in report.sources)
        rows.append(_row('Litigation Disclosure', claim_for('Litigation Disclosure'),
                         'No pending complaints or cases found in the sources searched' if searched else 'Court and news sources were not searched',
                         'MATCH' if searched else 'UNVERIFIED', portal))

    # 4. Escrow / OC status
    known = [p for p in projects if p.oc_listed is not None]
    if known:
        without = [p for p in known if not p.oc_listed]
        rows.append(_row('Escrow / OC Status', claim_for('Escrow / OC Status'),
                         f'{len(without)} of {len(known)} project row(s) show no OC/CC link on the RERA table' if without else 'OC/CC link present on every project row',
                         'MISMATCH' if without and re.search(r'occupation|\bOC\b|ready', claims.get('Escrow / OC Status', ''), re.I) else ('UNVERIFIED' if without else 'MATCH'),
                         url_for(known[0])))
    else:
        rows.append(_row('Escrow / OC Status', claim_for('Escrow / OC Status'),
                         'RERA quarterly progress and escrow statements were not read; check the project page', 'UNVERIFIED', portal))
    return rows
