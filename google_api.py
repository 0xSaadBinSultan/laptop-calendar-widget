"""Read selected Google calendars and Google Tasks, without write permissions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from backend import CONFIG, CACHE, read_json, write_private, event_bounds

TOKEN = Path.home()/'.config/laptop-calendar/oauth.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly',
          'https://www.googleapis.com/auth/tasks.readonly']
CALENDAR = 'https://www.googleapis.com/calendar/v3/'
TASKS = 'https://tasks.googleapis.com/tasks/v1/'

def get_json(token, base, path, params=None, payload=None):
    url = base+path+('?' + urllib.parse.urlencode(params) if params else '')
    if payload is not None and (base != CALENDAR or path != 'freeBusy'):
        raise ValueError('Only the read-only free/busy query accepts a request body')
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={'Authorization': 'Bearer '+token,
                                          'Content-Type': 'application/json', 'User-Agent': 'LaptopCalendar/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def pages(token, base, path, params):
    query = dict(params)
    rows = []
    while True:
        data = get_json(token, base, path, query)
        rows.extend(data.get('items', []))
        if not data.get('nextPageToken'): return rows
        query['pageToken'] = data['nextPageToken']

def calendar_list(token):
    return pages(token, CALENDAR, 'users/me/calendarList',
                 {'maxResults': 250, 'showDeleted': 'false', 'showHidden': 'false',
                  'fields': 'nextPageToken,items(id,summary,selected,primary,accessRole,backgroundColor)'})

def task_lists(token):
    return pages(token, TASKS, 'users/@me/lists',
                 {'maxResults': 100, 'fields': 'nextPageToken,items(id,title)'})

def normalize_event(item, source, timezone):
    if item.get('status') == 'cancelled' or not item.get('start'): return None
    zone = ZoneInfo(timezone)
    all_day = 'date' in item['start']
    def value(data):
        if 'date' in data: return data['date']
        stamp = datetime.fromisoformat(data['dateTime'].replace('Z', '+00:00'))
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=ZoneInfo(data.get('timeZone', timezone)))
        return stamp.astimezone(zone).isoformat()
    start = value(item['start'])
    finish = value(item.get('end', item['start']))
    return {'type': 'event', 'title': item.get('summary', 'Busy'), 'start': start, 'end': finish,
            'all_day': all_day, 'calendar': source['summary'], 'uid': item['id'],
            'color': source.get('backgroundColor', '#99baff')}

def normalize_task(item, source):
    if item.get('deleted'): return None
    row = {'type': 'task', 'title': item.get('title') or 'Untitled task',
           'calendar': 'Tasks · '+source['title'], 'uid': item['id'],
           'completed': item.get('status') == 'completed', 'all_day': True}
    # Google Tasks stores a due date in an RFC3339 field, but explicitly
    # discards its time of day. Do not shift that date across time zones.
    if item.get('due'):
        due = date.fromisoformat(item['due'][:10])
        row.update(start=due.isoformat(), end=(due+timedelta(days=1)).isoformat())
    return row

def availability_events(token, source, lo, hi):
    # Availability-only calendars reject a long free/busy query even though
    # ordinary event queries accept it. Fetch short windows and merge spans
    # crossing their boundaries so one busy period appears only once.
    spans = []
    cursor = lo
    while cursor < hi:
        stop = min(cursor+timedelta(days=28), hi)
        data = get_json(token, CALENDAR, 'freeBusy', payload={
            'timeMin': cursor.isoformat(), 'timeMax': stop.isoformat(),
            'items': [{'id': source['id']}]})
        group = data.get('calendars', {}).get(source['id'], {})
        if group.get('errors'): raise RuntimeError('Calendar availability could not be read')
        spans.extend((datetime.fromisoformat(b['start'].replace('Z', '+00:00')),
                      datetime.fromisoformat(b['end'].replace('Z', '+00:00')))
                     for b in group.get('busy', []))
        cursor = stop
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else: merged.append((start, end))
    return [{'type': 'event', 'title': 'Busy', 'start': start.isoformat(), 'end': end.isoformat(),
             'all_day': False, 'calendar': source['summary'],
             'uid': source['id']+'|'+start.isoformat()} for start, end in merged]

def synchronize(config=None):
    cfg = config or read_json(CONFIG)
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        creds.refresh(Request()); write_private(TOKEN, json.loads(creds.to_json()))
    token = creds.token
    timezone = cfg.get('timezone', 'America/New_York')
    now = datetime.now(ZoneInfo(timezone))
    lo = datetime.combine(now.date()-timedelta(days=62), time.min, now.tzinfo)
    hi = datetime.combine(now.date()+timedelta(days=370), time.min, now.tzinfo)
    calendars = [c for c in calendar_list(token) if (c.get('selected') or c.get('primary'))
                 and c.get('accessRole') in {'owner', 'writer', 'reader', 'freeBusyReader'}]
    lists = task_lists(token)

    def calendar_events(source):
        if source.get('accessRole') == 'freeBusyReader':
            return availability_events(token, source, lo, hi)
        items = pages(token, CALENDAR, 'calendars/'+urllib.parse.quote(source['id'], safe='')+'/events',
                      {'singleEvents': 'true', 'timeMin': lo.isoformat(), 'timeMax': hi.isoformat(),
                       'maxResults': 2500, 'showDeleted': 'false',
                       'fields': 'nextPageToken,items(id,summary,start,end,status)'})
        return [row for item in items if (row := normalize_event(item, source, timezone))]

    def tasks(source):
        items = pages(token, TASKS, 'lists/'+urllib.parse.quote(source['id'], safe='')+'/tasks',
                      {'maxResults': 100, 'showCompleted': 'true', 'showHidden': 'true', 'showDeleted': 'false',
                       'fields': 'nextPageToken,items(id,title,due,status,deleted)'})
        return [row for item in items if (row := normalize_task(item, source))]

    with ThreadPoolExecutor(max_workers=4) as pool:
        event_groups = list(pool.map(calendar_events, calendars))
        task_groups = list(pool.map(tasks, lists))
    events = [e for group in event_groups for e in group]
    all_tasks = [t for group in task_groups for t in group]
    dated = [t for t in all_tasks if 'start' in t and lo.date() <= date.fromisoformat(t['start']) < hi.date()]
    undated = [t for t in all_tasks if 'start' not in t and not t['completed']]
    events.extend(dated)
    events.sort(key=lambda e: (event_bounds(e, timezone)[0], e.get('type') == 'task'))
    data = {'updated': datetime.now().astimezone().isoformat(), 'timezone': timezone,
            'calendars': len(calendars), 'task_lists': len(lists), 'events': events,
            'undated_tasks': undated, 'source': 'google_api'}
    write_private(CACHE, data)
    return data
