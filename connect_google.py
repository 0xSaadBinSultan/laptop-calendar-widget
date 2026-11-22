#!/usr/bin/env python3
"""Authorize only Calendar/Tasks reading using the owner's existing Desktop client."""
import json
from pathlib import Path
import sys
import time

from google_auth_oauthlib.flow import InstalledAppFlow
from backend import CONFIG, read_json, write_private
from google_api import TOKEN, SCOPES, calendar_list, task_lists
from app_paths import STATE_DIR

PENDING = STATE_DIR / 'authorization.json'

def main(open_browser=None):
    cfg = read_json(CONFIG)
    path = Path(cfg.get('oauth_client', str(CONFIG.parent / 'client.json'))).expanduser()
    client = json.loads(path.read_text(encoding='utf-8'))['installed']
    desktop = {'installed': {**client, 'auth_uri': 'https://accounts.google.com/o/oauth2/v2/auth',
                            'token_uri': 'https://oauth2.googleapis.com/token',
                            'redirect_uris': ['http://localhost']}}
    flow = InstalledAppFlow.from_client_config(desktop, scopes=SCOPES)
    authorize = flow.authorization_url
    def record(**kwargs):
        url, state = authorize(**kwargs)
        write_private(PENDING, {'url': url, 'started': time.time()})
        return url, state
    flow.authorization_url = record
    creds = flow.run_local_server(host='127.0.0.1', port=0,
                                  open_browser=('--open-browser' in sys.argv if open_browser is None else open_browser),
                                  authorization_prompt_message='', timeout_seconds=1200,
                                  success_message='Google authorization received. You can return to your desktop.',
                                  access_type='offline', prompt='consent',
                                  include_granted_scopes='false', login_hint=cfg.get('account'))
    if not creds.has_scopes(SCOPES): raise RuntimeError('Both read-only permissions are required')
    calendars = calendar_list(creds.token)
    primary = next(c for c in calendars if c.get('primary'))
    expected = cfg.get('account', '')
    if expected and expected != 'you@example.com' and primary['id'].casefold() != expected.casefold():
        raise RuntimeError('The selected Google account is different; existing connection preserved')
    task_lists(creds.token)
    write_private(TOKEN, json.loads(creds.to_json()))
    cfg['account'] = primary['id']
    cfg['source'] = 'google_api'; write_private(CONFIG, cfg)
    write_private(PENDING, {'completed': True, 'finished': time.time()})
    print('Calendar and Tasks read-only access verified.')

if __name__ == '__main__':
    try: main()
    except Exception as error:
        # Exceptions from OAuth transports can contain URLs or credentials.
        print('Google connection could not be completed ('+type(error).__name__+'). Existing configuration preserved.')
        raise SystemExit(1)
