"""Private, read-only Google Calendar feed and recurrence handling."""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import tempfile
import urllib.parse
import urllib.request

from icalendar import Calendar
import recurring_ical_events

CONFIG = Path.home()/'.config/laptop-calendar/config.json'
CACHE = Path.home()/'.cache/laptop-calendar/events.json'
POSITION = Path.home()/'.config/laptop-calendar/position.json'
HOSTS = {'calendar.google.com', 'calendar.googleusercontent.com'}

def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default if default is not None else {}

def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.calendar-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)

def valid_feed(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == 'https' and p.hostname in HOSTS and not p.username
            and not p.password and p.port in (None, 443)
            and '/calendar/ical/' in p.path and p.path.endswith('.ics'))

class GoogleRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme != 'https' or p.hostname not in HOSTS:
            raise ValueError('Unexpected calendar redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def fetch_feed(url):
    if not valid_feed(url):
        raise ValueError('Use a Google Calendar iCal address')
    request = urllib.request.Request(url, headers={'User-Agent': 'LaptopCalendar/1.0'})
    with urllib.request.build_opener(GoogleRedirects()).open(request, timeout=25) as response:
        raw = response.read(20*1024*1024 + 1)
    if len(raw) > 20*1024*1024 or b'BEGIN:VCALENDAR' not in raw[:256]:
        raise ValueError('Calendar response is unavailable or too large')
    return raw

def parse_events(raw, name, start, end, timezone):
    cal = Calendar.from_ical(raw)
    zone = ZoneInfo(timezone)
    rows = []
    for item in recurring_ical_events.of(cal).between(start, end):
        if str(item.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        begin = item.decoded('DTSTART')
        all_day = isinstance(begin, date) and not isinstance(begin, datetime)
        finish = item.decoded('DTEND', None)
        if finish is None:
            duration = item.decoded('DURATION', None)
            finish = begin + (duration or timedelta(days=1) if all_day else duration or timedelta(hours=1))
        def normalized(value):
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=zone)
                return value.astimezone(zone).isoformat()
            return value.isoformat()
        rows.append({'title': str(item.get('SUMMARY', 'Untitled event'))[:300],
                     'start': normalized(begin), 'end': normalized(finish),
                     'all_day': all_day, 'calendar': name,
                     'uid': str(item.get('UID', ''))})
    return rows

def event_bounds(event, timezone):
    zone = ZoneInfo(timezone)
    if event['all_day']:
        start = datetime.combine(date.fromisoformat(event['start']), time.min, zone)
        end = datetime.combine(date.fromisoformat(event['end']), time.min, zone)
    else:
        start = datetime.fromisoformat(event['start']).astimezone(zone)
        end = datetime.fromisoformat(event['end']).astimezone(zone)
    return start, end

def on_day(event, day, timezone):
    start, end = event_bounds(event, timezone)
    lo = datetime.combine(day, time.min, ZoneInfo(timezone))
    if start == end:
        return lo <= start < lo + timedelta(days=1)
    return start < lo + timedelta(days=1) and (end > lo or start == lo == end)

def synchronize(config=None):
    cfg = config or read_json(CONFIG)
    if cfg.get('source') == 'google_api':
        from google_api import synchronize as google_synchronize
        return google_synchronize(cfg)
    sources = cfg.get('calendars', [])
    if not sources:
        raise ValueError('Calendar connection has not been configured')
    timezone = cfg.get('timezone', 'America/New_York')
    today = datetime.now(ZoneInfo(timezone)).date()
    rows = []
    for source in sources:
        rows.extend(parse_events(fetch_feed(source['url']), source.get('name', 'Calendar'),
                                 today-timedelta(days=62), today+timedelta(days=370), timezone))
    rows.sort(key=lambda e: event_bounds(e, timezone)[0])
    data = {'updated': datetime.now().astimezone().isoformat(), 'timezone': timezone,
            'calendars': len(sources), 'events': rows}
    write_private(CACHE, data)
    return data

if __name__ == '__main__':
    import sys
    try:
        data = synchronize() if '--check' in sys.argv else read_json(CACHE)
        rows = data.get('events', [])
        print(json.dumps({'last_sync': data.get('updated'), 'calendars': data.get('calendars', 0),
                          'events': sum(e.get('type') != 'task' for e in rows),
                          'dated_tasks': sum(e.get('type') == 'task' for e in rows),
                          'undated_tasks': len(data.get('undated_tasks', []))}))
    except Exception:
        print('Calendar sync is unavailable; any previous cached events were preserved.', file=sys.stderr)
        raise SystemExit(1)
