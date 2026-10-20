"""Fast offline checks: python -m unittest backend/tests/test_score.py  (or pytest backend/tests)."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import jobs
from app.models import Claim, Complaint, CourtRecord, NewsItem, Project, Report, Source
from app.score import band, score_report
from app.sources import court, mca, news, rera
from app.sources.brochure_diff import diff_report


def make(**kw) -> Report:
    base = dict(id='t', builder_query='X', builder_name='X', state='Haryana', mode='live',
                generated_at=datetime.now(timezone.utc), verdict='unknown', summary='')
    base.update(kw)
    return Report(**base)


def rera_src(status='ok'):
    return Source(id='rera_hr', label='Haryana RERA', status=status)


class ScoreTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(band(44), ('high', 'OUT'))
        self.assertEqual(band(45), ('medium', 'DRS'))
        self.assertEqual(band(70), ('medium', 'DRS'))
        self.assertEqual(band(71), ('low', 'NOT OUT'))

    def test_clean_project_is_not_out(self):
        r = score_report(make(sources=[rera_src()], projects=[Project(name='P', revisions=0)]))
        self.assertEqual((r.verdict, r.cricket_verdict, r.risk_score), ('low', 'NOT OUT', 100))
        self.assertEqual(sum(row['runs'] for row in r.scorecard), r.score_100)

    def test_partial_rera_still_scored(self):  # regression: used to return "unknown" unless status == ok
        r = score_report(make(sources=[rera_src('partial')], projects=[Project(name='P', revisions=4)]))
        self.assertNotEqual(r.verdict, 'unknown')
        self.assertTrue(r.flags)

    def test_risky_builder_is_out(self):
        r = score_report(make(
            sources=[rera_src()], projects=[Project(name='P', revisions=4, claim_ids=['c1']), Project(name='Q', revisions=3)],
            complaints=[Complaint(status='pending', claim_id=f'c{i}') for i in range(3)],
            court_records=[CourtRecord(status='pending', source_url=f'https://indiankanoon.org/doc/{i}/') for i in range(3)],
            news=[NewsItem(title='NCLT plea', tag='litigation')]))
        self.assertEqual((r.verdict, r.cricket_verdict), ('high', 'OUT'))
        self.assertLess(r.risk_score, 45)

    def test_no_signal_is_unknown_not_clean(self):
        r = score_report(make(sources=[rera_src('failed')]))
        self.assertEqual(r.verdict, 'unknown')

    def test_news_only_signal_scores(self):
        r = score_report(make(news=[NewsItem(title='Buyers protest delay', tag='delay')]))
        self.assertNotEqual(r.verdict, 'unknown')

    def test_new_report_defaults(self):
        r = make()
        self.assertEqual((r.risk_score, r.cricket_verdict, r.brochure_diff, r.tinyfish_traces, r.ncdrc_records), (50, 'DRS', [], [], []))


class ParserTests(unittest.TestCase):
    def test_news_classification(self):
        self.assertEqual(news.classify('CBI arrests promoter in fraud case'), 'fraud')
        self.assertEqual(news.classify('NCLT admits insolvency plea'), 'litigation')
        self.assertEqual(news.classify('Buyers protest possession delay'), 'delay')
        self.assertEqual(news.classify('Record pre-sales in Gurugram'), 'neutral')

    def test_news_filters_unrelated(self):
        items = news.items_from_results('DLF Limited', [
            {'url': 'https://a.com/1', 'title': 'DLF faces delay complaints', 'snippet': 'x', 'site_name': 'www.a.com', 'date': '9 Jan 2023'},
            {'url': 'https://a.com/2', 'title': 'Unrelated cricket news', 'snippet': 'nothing'}])
        self.assertEqual([i.tag for i in items], ['delay'])
        self.assertEqual(str(items[0].published), '2023-01-09')

    def test_court_split_consumer_vs_court(self):
        records, ncdrc = court.parse_results('DLF', [
            {'url': 'https://indiankanoon.org/doc/1/', 'title': 'Rohit Jain vs Dlf Home Developers Ltd. on 7 September, 2024',
             'snippet': 'Consumer Case No. 123 of 2020 NATIONAL CONSUMER DISPUTES REDRESSAL COMMISSION complaint dismissed'},
            {'url': 'https://indiankanoon.org/doc/2/', 'title': 'Dlf Ltd vs Buyer', 'snippet': 'civil suit for injunction pending'}])
        self.assertEqual(len(ncdrc), 1)
        self.assertEqual(ncdrc[0]['forum'], 'NCDRC')
        self.assertEqual(ncdrc[0]['case_no'], '123of2020')
        self.assertEqual((len(records), records[0].kind, records[0].status), (1, 'civil', 'pending'))

    def test_rera_row_and_detail_parsing(self):
        row = '56 | GGM/1 DATED 21.11.2022 (SUPERSEDES GGM/0) Lapsed Project | RERA-GRG-1 | A | B | S | G | HRERA |  | 08/09/2027 | View OC/CC/PCC'
        facts = rera.parse_row_facts(row)
        self.assertEqual((facts['current_possession'], facts['revisions'], facts['lapsed']), ('2027-09-08', 1, True))
        page = ('| 10. Initial date of completion of the project. |  | **24-08-2024** |\n'
                '| 11. Likely date of completion of the project |  | **30-06-2025** |\nComplaint CC/12/2022 is pending')
        d = rera.parse_detail_page(page)
        self.assertEqual((d['original_possession'], d['current_possession'], d['revisions']), ('2024-08-24', '2025-06-30', 1))
        self.assertEqual(d['complaints'][0]['status'], 'pending')

    def test_mca_search_hit(self):
        rec = mca.parse_search_hit({
            'title': 'DLF INDIA LIMITED', 'url': 'https://www.zaubacorp.com/DLF-INDIA-LIMITED-U45201DL2006PLC147370',
            'snippet': 'Directors of DLF INDIA LIMITED are MOHIT GUJRAL, SUROJIT BASAK, Number (CIN) is U45201DL2006PLC147370.'})
        self.assertEqual((rec.cin, rec.directors), ('U45201DL2006PLC147370', ['MOHIT GUJRAL', 'SUROJIT BASAK']))
        self.assertIsNone(mca.parse_search_hit({'title': 'x', 'url': 'https://evil.com/U45201DL2006PLC147370', 'snippet': ''}))


class DiffTests(unittest.TestCase):
    def test_possession_mismatch_from_brochure_text(self):
        r = make(sources=[rera_src()], projects=[Project(name='P', original_possession=datetime(2023, 6, 1).date(),
                                                         current_possession=datetime(2025, 12, 31).date(), revisions=2)])
        rows = {x['field']: x for x in diff_report(r, 'Possession by June 2023. Litigation-free project.')}
        self.assertEqual(rows['Possession Date']['status'], 'MISMATCH')
        self.assertEqual(set(rows), {'Possession Date', 'Approvals / Phase', 'Litigation Disclosure', 'Escrow / OC Status'})
        for x in rows.values():
            self.assertTrue({'field', 'brochure_claim', 'rera_reality', 'status', 'source_url'} <= set(x))

    def test_litigation_contradiction(self):
        r = make(sources=[rera_src()], projects=[Project(name='P')], complaints=[Complaint(status='pending', claim_id='c1')],
                 claims=[Claim(id='c1', text='t', source_id='rera_hr', source_url='https://haryanarera.gov.in/')])
        rows = {x['field']: x for x in diff_report(r, 'Zero litigation. No litigation at all.')}
        self.assertEqual(rows['Litigation Disclosure']['status'], 'MISMATCH')

    def test_no_rera_data_is_unverified(self):
        rows = diff_report(make(), None)
        self.assertEqual({x['status'] for x in rows if x['field'] in ('Possession Date', 'Approvals / Phase')}, {'UNVERIFIED'})


class FixtureTests(unittest.TestCase):
    def test_fixtures_load_and_score(self):
        jobs.reports.clear()
        jobs.load_reports()
        saved = {r.id: r for r in jobs.reports.values() if r.mode == 'saved'}
        self.assertGreaterEqual(len(saved), 3)
        for r in saved.values():
            self.assertIn(r.cricket_verdict, ('OUT', 'DRS', 'NOT OUT'))
            self.assertEqual(r.cricket_verdict, band(r.risk_score)[1])
            self.assertEqual(sum(row['runs'] for row in r.scorecard), r.risk_score)
            for row in r.brochure_diff:
                self.assertIn('rera_reality', row)
        if 'saved-parsvnath' in saved:
            self.assertEqual(saved['saved-parsvnath'].cricket_verdict, 'OUT')

    def test_authorized(self):
        import os
        from app.models import CheckRequest
        os.environ['TINYFISH_API_KEY'] = 'k' * 20
        os.environ['LIVE_PASSCODE'] = 'pw'
        self.assertTrue(jobs.authorized(CheckRequest(query='DLF', mode='auto'), '8.8.8.8'))
        self.assertTrue(jobs.authorized(CheckRequest(query='DLF', mode='live', passcode='pw'), '8.8.8.8'))
        self.assertFalse(jobs.authorized(CheckRequest(query='DLF', mode='live', passcode='bad'), '8.8.8.8'))
        self.assertTrue(jobs.authorized(CheckRequest(query='DLF', mode='live'), '127.0.0.1'))


if __name__ == '__main__':
    unittest.main()
