import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from pathlib import Path
import tempfile

import backend

ICAL = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Widget Test//EN\r
BEGIN:VEVENT\r
UID:recurrence-test\r
DTSTART;TZID=America/New_York:20261113T090000\r
DTEND;TZID=America/New_York:20261113T100000\r
RRULE:FREQ=DAILY;COUNT=3\r
EXDATE;TZID=America/New_York:20261114T090000\r
SUMMARY:Test meeting\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:all-day-test\r
DTSTART;VALUE=DATE:20261120\r
DTEND;VALUE=DATE:20261123\r
SUMMARY:Three-day event\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cancelled-test\r
DTSTART:20261113T150000Z\r
DTEND:20261113T160000Z\r
STATUS:CANCELLED\r
SUMMARY:Cancelled\r
END:VEVENT\r
END:VCALENDAR\r
'''

class CalendarTests(unittest.TestCase):
    def test_recurrence_exceptions_and_cancelled_events(self):
        rows = backend.parse_events(ICAL, 'Test', date(2026,11,1), date(2026,12,1), 'America/New_York')
        repeated = [r for r in rows if r['uid']=='recurrence-test']
        self.assertEqual([r['start'][:10] for r in repeated], ['2026-11-13','2026-11-15'])
        self.assertFalse(any(r['title']=='Cancelled' for r in rows))

    def test_all_day_end_is_exclusive(self):
        rows = backend.parse_events(ICAL, 'Test', date(2026,11,1), date(2026,12,1), 'America/New_York')
        event = next(r for r in rows if r['uid']=='all-day-test')
        self.assertTrue(backend.on_day(event,date(2026,11,22),'America/New_York'))
        self.assertFalse(backend.on_day(event,date(2026,11,23),'America/New_York'))

    def test_timezone_conversion_preserves_instant(self):
        rows = backend.parse_events(ICAL,'Test',date(2026,11,1),date(2026,12,1),'Asia/Dhaka')
        event = next(r for r in rows if r['uid']=='recurrence-test')
        self.assertIn('20:00:00+06:00',event['start'])

    def test_only_google_https_feeds_are_accepted(self):
        self.assertTrue(backend.valid_feed('https://calendar.google.com/calendar/ical/example/private-example/basic.ics'))
        for url in ['http://calendar.google.com/calendar/ical/example/basic.ics',
                    'https://calendar.google.com.evil.test/calendar/ical/example/basic.ics',
                    'file:///etc/passwd','https://user:pass@calendar.google.com/calendar/ical/example/basic.ics']:
            self.assertFalse(backend.valid_feed(url))

    def test_offline_sync_preserves_private_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private/cache.json'
            backend.write_private(path,{'events':[{'title':'cached'}]})
            before=path.read_bytes()
            with patch.object(backend,'CACHE',path),patch.object(backend,'fetch_feed',side_effect=OSError('offline')):
                with self.assertRaises(OSError):backend.synchronize({'timezone':'America/New_York','calendars':[{'url':'unused'}]})
            self.assertEqual(path.read_bytes(),before)
            import os
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_google_task_due_date_is_not_shifted(self):
        from google_api import normalize_task
        row=normalize_task({'id':'task','title':'Due today','due':'2026-11-13T00:00:00Z','status':'needsAction'}, {'title':'Tasks'})
        self.assertEqual(row['start'],'2026-11-13')
        self.assertTrue(backend.on_day(row,date(2026,11,13),'America/New_York'))
        self.assertFalse(backend.on_day(row,date(2026,11,12),'America/New_York'))

    def test_google_all_day_event_and_completed_task(self):
        from google_api import normalize_event,normalize_task
        row=normalize_event({'id':'event','summary':'Several days','start':{'date':'2026-11-20'},'end':{'date':'2026-11-23'}},
                            {'summary':'Calendar'},'Asia/Dhaka')
        self.assertTrue(backend.on_day(row,date(2026,11,22),'Asia/Dhaka'))
        self.assertFalse(backend.on_day(row,date(2026,11,23),'Asia/Dhaka'))
        task=normalize_task({'id':'task','title':'Done','status':'completed'}, {'title':'Tasks'})
        self.assertTrue(task['completed']);self.assertNotIn('start',task)

    def test_google_adapter_rejects_write_requests(self):
        from google_api import get_json,CALENDAR
        with self.assertRaises(ValueError):get_json('unused',CALENDAR,'calendars/primary/events',payload={'summary':'Do not create'})

    def test_availability_queries_are_bounded_and_merge_boundary_spans(self):
        from google_api import availability_events
        lo=datetime(2026,11,1,tzinfo=timezone.utc)
        hi=datetime(2026,12,15,tzinfo=timezone.utc)
        def answer(token, base, path, params=None, payload=None):
            begin=datetime.fromisoformat(payload['timeMin'])
            end=datetime.fromisoformat(payload['timeMax'])
            self.assertLessEqual((end-begin).days,28)
            return {'calendars':{'test':{'busy':[{'start':begin.isoformat(),'end':end.isoformat()}]}}}
        with patch('google_api.get_json',side_effect=answer) as query:
            rows=availability_events('unused',{'id':'test','summary':'Private'},lo,hi)
        self.assertEqual(query.call_count,2)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['start'],lo.isoformat())
        self.assertEqual(rows[0]['end'],hi.isoformat())

if __name__ == '__main__':unittest.main()
