"""Deterministic 0-100 safety score ("cricket scorecard"). No LLM, no network.

score_100 starts at 100 and loses runs per check; each check has a fixed weight so the
per-check `scorecard` rows always sum to score_100. Higher = safer.
OUT (<45, high risk) / DRS (45..70, medium) / NOT OUT (>70, low risk).
"""
from .models import Report, Flag

WEIGHTS = {'Possession dates': 25, 'RERA complaints': 20, 'Court & consumer cases': 25,
           'News': 15, 'Brochure vs RERA': 10, 'Company registry': 5}

def band(score: int) -> tuple[str, str]:
    """-> (verdict, cricket_verdict)"""
    if score < 45: return 'high', 'OUT'
    if score <= 70: return 'medium', 'DRS'
    return 'low', 'NOT OUT'

def _is(row, *values) -> bool:
    return str(row.get('status') or row.get('verdict') or '').upper() in values

def score_report(report: Report) -> Report:
    flags: list[Flag] = []
    loss = {name: 0 for name in WEIGHTS}
    notes = {name: '' for name in WEIGHTS}

    def hit(check: str, points: int, label: str, severity: str, claim_ids=()):
        loss[check] += points
        flags.append(Flag(label=label, severity=severity, claim_ids=list(claim_ids)))

    for p in report.projects:
        rev = p.revisions or 0
        if rev >= 3: hit('Possession dates', 12, f'{p.name}: {rev} possession date revisions', 'high', p.claim_ids)
        elif rev: hit('Possession dates', 7, f'{p.name}: {rev} possession date revision(s)', 'medium', p.claim_ids)
        if p.lapsed: hit('Possession dates', 3, f'{p.name}: registration marked lapsed', 'medium', p.claim_ids)

    pending = [c for c in report.complaints if c.status == 'pending']
    if pending:
        hit('RERA complaints', 7 * len(pending), f'{len(pending)} pending RERA complaint(s)', 'high' if len(pending) >= 3 else 'medium', [c.claim_id for c in pending])

    cases = [(r.status, r.claim_id) for r in report.court_records] + [(str(r.get('status', 'unknown')).lower(), r.get('claim_id')) for r in report.ncdrc_records]
    if cases:
        n_pending = sum(1 for status, _ in cases if status == 'pending')
        hit('Court & consumer cases', 6 * n_pending + 2 * (len(cases) - n_pending),
            f'{len(cases)} court / consumer case(s), {n_pending} pending', 'high' if n_pending or len(cases) >= 4 else 'medium',
            [c for _, c in cases if c])

    adverse = [n for n in report.news if n.tag in ('fraud', 'litigation')]
    delays = [n for n in report.news if n.tag == 'delay']
    if adverse: hit('News', 6 * len(adverse), f'{len(adverse)} news item(s) tagged litigation or fraud', 'high', [n.claim_id for n in adverse if n.claim_id])
    if delays: hit('News', 3 * len(delays), f'{len(delays)} news item(s) tagged delay', 'medium', [n.claim_id for n in delays if n.claim_id])

    mismatches = [r for r in report.brochure_diff if _is(r, 'MISMATCH')]
    if mismatches: hit('Brochure vs RERA', 5 * len(mismatches), f'{len(mismatches)} brochure promise(s) contradict the RERA record', 'high' if len(mismatches) >= 2 else 'medium')

    for r in report.registry_records:
        if r.status and r.status.casefold() not in ('active', 'listed', 'active compliance'):
            hit('Company registry', 5, f'{r.company_name}: MCA status {r.status}', 'high', [r.claim_id] if r.claim_id else [])

    report.flags = flags
    # Assessed = official RERA data or any other real signal exists. Nothing at all -> unknown, never "clean".
    rera = next((s for s in report.sources if s.id in ('rera_hr', 'rera_dl')), None)
    rera_signal = bool(rera and rera.status in ('ok', 'partial') and (report.projects or report.claims))
    assessed = rera_signal or any((report.court_records, report.ncdrc_records, report.registry_records, report.news, report.projects and report.brochure_diff))
    if not assessed:
        report.verdict, report.cricket_verdict = 'unknown', 'DRS'
        report.risk_score = report.score_100 = 50
        report.score, report.scorecard = 0, []
        return report

    report.scorecard = []
    for name, weight in WEIGHTS.items():
        runs = max(0, weight - loss[name])
        report.scorecard.append({'check': name, 'runs': runs, 'out_of': weight, 'note': notes[name] or ('clean in sources checked' if loss[name] == 0 else 'see flags')})
    report.score_100 = report.risk_score = sum(row['runs'] for row in report.scorecard)
    report.verdict, report.cricket_verdict = band(report.score_100)
    report.score = round((100 - report.score_100) / 10)  # legacy "risk points"
    return report
