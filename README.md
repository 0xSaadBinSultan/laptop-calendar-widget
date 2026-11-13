# Desktop Calendar

A private, read-only GTK3 calendar on the XFCE/X11 wallpaper, below application windows. It starts when you sign in and refreshes every five minutes. Includes Google Calendar events, Google Tasks, a dark translucent panel and mouse resizing.

## Use

- Click a day to see that day's events and dated Google Tasks. Scroll the agenda when necessary.
- Blue underlines mark dates containing events or tasks. Completed tasks have a check mark.
- Use the three-dot menu → **Unscheduled tasks** for pending tasks without a due date. Click a day to return to its agenda.
- Drag the date heading to move the widget; **Reset position** returns it to the top-right.
- Drag any edge or corner to resize it, like a desktop application. The pointer changes to a resize arrow at the border. Size and position are remembered across restarts; the agenda scrolls when the widget is short.
- **Today** returns to today's date. The arrows browse months; the selected day stays selected until you click another date.
- The refresh button syncs immediately; the arrow opens Google Calendar for the configured account.
- Make changes in Google Calendar or Google Tasks. This widget has no write permissions.

Set `timezone` in your private config to the IANA time zone you use in Google Calendar, such as `Europe/London`. A Google Tasks due date stays on its calendar date rather than being shifted by a time zone conversion. The footer's sync time uses the laptop's local time zone.

## Data and limitations

The Google backend reads the primary and selected visible calendars, plus all task lists. It fetches expanded events from 62 days ago through 370 days ahead. Tasks without dates appear separately. Repeating tasks are limited to the instances Google exposes through its Tasks API; the widget does not invent future occurrences. Calendar selections are refreshed on the next sync. Calendars shared with availability-only access display **Busy**, as defined by Google's [free/busy API](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query).

If the network or API fails, the last successful private cache remains visible with an offline indicator. The widget does not publish calendars or change existing backup credentials.

Private configuration, OAuth credentials and cache are stored in `~/.config/laptop-calendar`, `~/.local/state/laptop-calendar`, and `~/.cache/laptop-calendar`. Treat these as sensitive. They are intentionally outside this source package. Revoke access through your Google Account's third-party connections if you no longer want it.

## Commands

```bash
laptop-calendar             # Start the widget
laptop-calendar --status    # Cached sync summary; no event titles
laptop-calendar --check     # Perform a sync and print counts
laptop-calendar --connect   # Authorize your own Google account
systemctl --user restart laptop-calendar-widget.service
systemctl --user stop laptop-calendar-widget.service
```

To stop it appearing after login, set `Hidden=true` in `~/.config/autostart/laptop-calendar-widget.desktop`. Stopping the service alone affects only the current session.

## Install on Debian/Kali XFCE

Install the desktop dependencies if they are missing:

```bash
sudo apt update
sudo apt install --no-remove python3 python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

Run as the desktop user, without sudo:

```bash
python3 /path/to/calendar-widget/install.py
```

The installer creates a private Python environment, copies the source, and installs the user service and XFCE autostart entry. It expects Debian's Python venv, GTK3, PyGObject and Cairo packages to exist. It does not run an operating-system upgrade. The service assumes the existing X11 display `:0`; adjust DISPLAY and XAUTHORITY for another desktop session.

Existing private configuration is preserved. For a fresh installation:

1. Edit `~/.config/laptop-calendar/config.json` and replace the placeholder account and time zone.
2. In your own Google Cloud project, enable Google Calendar API and Google Tasks API. Configure its OAuth consent screen and add your account as a test user if the app is in Testing.
3. Create a **Desktop app** OAuth client and save its downloaded JSON as `~/.config/laptop-calendar/client.json`. Keep it private with `chmod 600 ~/.config/laptop-calendar/client.json`.
4. Run `laptop-calendar --connect`, review the permissions in your browser and approve only if you agree. Leave the terminal running until authorization finishes.
5. Run `laptop-calendar --check`, then `laptop-calendar`.

The connector requests only Calendar and Tasks read-only scopes. It switches to the Google backend only after verifying both permissions and the expected account. Refresh tokens for external apps in Testing can expire after seven days; reconnect or configure your own consent setup appropriately for continued personal use. See Google's [OAuth consent guide](https://developers.google.com/workspace/guides/configure-oauth-consent) and [token expiration documentation](https://developers.google.com/identity/protocols/oauth2#expiration).

Your Google account, client credentials, tokens, private feed URLs and calendar cache are **never part of the public source**. Each installation connects to its own account; there is no shared API key or hosted service. Do not commit your private config directory. The connector does not need a GitHub token.

The older private iCal backend remains available for existing installations, but cannot supply Google Tasks. No credentials are included in this directory.
