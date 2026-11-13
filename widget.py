#!/usr/bin/env python3
"""A translucent calendar pinned to the XFCE desktop, below application windows."""
import calendar
from datetime import datetime, timedelta
import fcntl
import os
from pathlib import Path
import threading
import webbrowser
from zoneinfo import ZoneInfo

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
from backend import CONFIG, CACHE, POSITION, read_json, write_private, synchronize, on_day, event_bounds

CSS = b'''
window { background-color: transparent; }
.panel { background-color: rgba(19,22,30,0.92); border: 1px solid rgba(255,255,255,0.16);
         border-radius: 22px; color: #f3f5fb; padding: 18px; }
label { color: #edf0f7; font-family: "DejaVu Sans"; }
.eyebrow { color: #9ba8bf; font-size: 10px; letter-spacing: 1.8px; }
.date-title { font-size: 24px; font-weight: 600; }
.month-title { font-size: 14px; font-weight: 600; }
.muted { color: #9ba8bf; font-size: 11px; }
.weekday { color: #9ba8bf; font-size: 10px; }
button { color: #d8e1f4; background: transparent; border: 0; box-shadow: none;
         text-shadow: none; border-radius: 10px; padding: 5px; min-height: 20px; }
button:hover { background-color: rgba(255,255,255,0.1); }
button.day { font-size: 12px; padding: 0; min-width: 34px; min-height: 30px;
             border: 1px solid transparent; border-radius: 11px; }
button.outside { color: #626c80; }
button.event-day { border-bottom: 2px solid #8eb2ff; }
button.today { background-color: #99baff; color: #101827; font-weight: bold; }
button.selected { border: 1px solid rgba(210,224,255,0.7); }
.event { background-color: rgba(255,255,255,0.055); border-radius: 12px; padding: 10px; }
.event-title { font-size: 12px; font-weight: 600; }
.completed label { color: #9ba8bf; }
.section-title { font-size: 10px; color: #aebbd1; letter-spacing: 1px; }
separator { background-color: rgba(255,255,255,0.10); min-height: 1px; }
menu.calendar-menu { background-color: #191f2b; background-image: none;
                     border: 1px solid #46516a; border-radius: 10px; padding: 5px; }
menu.calendar-menu menuitem { background-color: transparent; background-image: none;
                              color: #edf0f7; padding: 9px 14px; border-radius: 6px; }
menu.calendar-menu menuitem label { color: #edf0f7; }
menu.calendar-menu menuitem:hover { background-color: #344462; color: #ffffff; }
menu.calendar-menu menuitem:hover label { color: #ffffff; }
'''

def label(text='', css=None, xalign=0):
    item = Gtk.Label(label=text, xalign=xalign)
    if css:
        item.get_style_context().add_class(css)
    return item

def resized_geometry(initial, edge, dx, dy, minimum):
    x, y, width, height = initial
    min_width, min_height = minimum
    right, bottom = x+width, y+height
    if 'w' in edge:
        x = min(x+dx, right-min_width); width = right-x
    elif 'e' in edge:
        width = max(min_width, width+dx)
    if 'n' in edge:
        y = min(y+dy, bottom-min_height); height = bottom-y
    elif 's' in edge:
        height = max(min_height, height+dy)
    return x, y, width, height

class CalendarWidget(Gtk.Window):
    def __init__(self):
        super().__init__(title='Desktop Calendar')
        self.cfg = read_json(CONFIG)
        self.data = read_json(CACHE)
        self.timezone = self.cfg.get('timezone', 'America/New_York')
        self.selected = self.today()
        self.year, self.month = self.selected.year, self.selected.month
        self.busy = False
        self.offline = False
        self.drag = None
        self.resize_drag = None
        self.undated_view = False
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_keep_below(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_opacity(float(self.cfg.get('opacity', .93)))
        provider = Gtk.CssProvider(); provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.connect('draw', self.clear_background)
        self.connect('destroy', Gtk.main_quit)
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.panel.get_style_context().add_class('panel')
        overlay = Gtk.Overlay()
        overlay.add(self.panel)
        self.add(overlay)
        self.set_size_request(300, -1)
        saved = read_json(POSITION)
        self.set_default_size(max(300, int(saved.get('width', self.cfg.get('width', 340)))),
                              max(1, int(saved['height'])) if 'height' in saved else -1)
        self.add_resize_handles(overlay)

        header = Gtk.Box(spacing=8)
        drag_area = Gtk.EventBox()
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        title.pack_start(label('GOOGLE CALENDAR', 'eyebrow'), False, False, 0)
        self.date_label = label('', 'date-title')
        self.date_label.set_ellipsize(Pango.EllipsizeMode.END)
        title.pack_start(self.date_label, False, False, 0)
        drag_area.add(title)
        drag_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        drag_area.connect('button-press-event', self.drag_start)
        drag_area.connect('motion-notify-event', self.drag_move)
        drag_area.connect('button-release-event', self.drag_end)
        header.pack_start(drag_area, True, True, 0)
        header.pack_end(self.button('⋮', lambda _: self.show_menu(), 'Calendar menu'), False, False, 0)
        self.panel.pack_start(header, False, False, 0)

        nav = Gtk.Box(spacing=4)
        self.month_label = label('', 'month-title')
        nav.pack_start(self.month_label, True, True, 0)
        nav.pack_end(self.button('›', lambda _: self.shift_month(1), 'Next month'), False, False, 0)
        nav.pack_end(self.button('‹', lambda _: self.shift_month(-1), 'Previous month'), False, False, 0)
        nav.pack_end(self.button('Today', lambda _: self.go_today(), 'Return to today'), False, False, 0)
        self.panel.pack_start(nav, False, False, 0)
        self.grid = Gtk.Grid(column_homogeneous=True, row_spacing=3, column_spacing=2)
        self.panel.pack_start(self.grid, False, False, 0)
        self.panel.pack_start(Gtk.Separator(), False, False, 0)
        self.agenda_heading = label('UPCOMING', 'section-title')
        self.panel.pack_start(self.agenda_heading, False, False, 0)
        self.agenda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(60)
        scroll.set_max_content_height(190)
        scroll.set_propagate_natural_height(True)
        scroll.add(self.agenda)
        self.panel.pack_start(scroll, True, True, 0)
        footer = Gtk.Box(spacing=8)
        self.status = label('', 'muted')
        footer.pack_start(self.status, True, True, 0)
        footer.pack_end(self.button('↗', lambda _: self.open_calendar(), 'Open Google Calendar'), False, False, 0)
        footer.pack_end(self.button('↻', lambda _: self.refresh(), 'Refresh calendar'), False, False, 0)
        self.panel.pack_start(footer, False, False, 0)
        self.render()
        self.show_all()
        GLib.idle_add(self.place)
        GLib.timeout_add_seconds(max(60, int(self.cfg.get('refresh_minutes', 5))*60), self.refresh)
        GLib.timeout_add_seconds(60, self.minute_tick)
        self.refresh()

    def today(self):
        return datetime.now(ZoneInfo(self.timezone)).date()

    def button(self, text, callback, tooltip):
        b = Gtk.Button(label=text); b.set_focus_on_click(False)
        b.set_tooltip_text(tooltip); b.connect('clicked', callback)
        return b

    def clear_background(self, _window, cr):
        import cairo
        cr.set_operator(cairo.OPERATOR_SOURCE); cr.set_source_rgba(0, 0, 0, 0); cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def place(self, position=None):
        monitor = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        rect = monitor.get_geometry()
        pos = read_json(POSITION) if position is None else position
        width, height = self.get_size()
        x = max(rect.x, min(int(pos.get('x', rect.x+rect.width-width-24)), rect.x+rect.width-width))
        y = max(rect.y+36, min(int(pos.get('y', rect.y+70)), rect.y+rect.height-height-24))
        self.move(x, y)
        return False

    def add_resize_handles(self, overlay):
        # Input-only borders preserve the frameless desktop appearance.
        # Manual resizing also works for DESKTOP windows that the window
        # manager does not offer ordinary decoration-based resize controls.
        self.resize_handles = {}
        for edge in ('n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se'):
            handle = Gtk.EventBox()
            handle.set_visible_window(False)
            handle.set_above_child(True)
            handle.set_halign(Gtk.Align.START if 'w' in edge else Gtk.Align.END if 'e' in edge else Gtk.Align.FILL)
            handle.set_valign(Gtk.Align.START if 'n' in edge else Gtk.Align.END if 's' in edge else Gtk.Align.FILL)
            corner = len(edge) == 2
            handle.set_size_request(16 if corner else 8 if edge in ('w', 'e') else -1,
                                    16 if corner else 8 if edge in ('n', 's') else -1)
            handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                              Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                              Gdk.EventMask.LEAVE_NOTIFY_MASK)
            handle.connect('enter-notify-event', self.resize_cursor, edge)
            handle.connect('leave-notify-event', self.resize_cursor, None)
            handle.connect('button-press-event', self.resize_start, edge)
            handle.connect('motion-notify-event', self.resize_move)
            handle.connect('button-release-event', self.resize_end)
            overlay.add_overlay(handle)
            self.resize_handles[edge] = handle

    def resize_cursor(self, _handle, _event, edge):
        if not self.resize_drag:
            cursor = Gdk.Cursor.new_from_name(self.get_display(), edge+'-resize') if edge else None
            self.get_window().set_cursor(cursor)
        return False

    def resize_start(self, handle, event, edge):
        if event.button != 1: return False
        x, y = self.get_position(); width, height = self.get_size()
        self.resize_drag = (edge, event.x_root, event.y_root, (x, y, width, height))
        handle.grab_add()
        return True

    def resize_move(self, _handle, event):
        if not self.resize_drag: return False
        edge, root_x, root_y, initial = self.resize_drag
        minimum, _ = self.get_preferred_size()
        x, y, width, height = resized_geometry(initial, edge, int(event.x_root-root_x),
                                               int(event.y_root-root_y), (minimum.width, minimum.height))
        self.move(x, y); self.resize(width, height)
        return True

    def resize_end(self, handle, event):
        if event.button != 1 or not self.resize_drag: return False
        self.resize_move(handle, event)
        handle.grab_remove()
        self.resize_drag = None
        self.get_window().set_cursor(None)
        # Wait for GTK/X11 to apply the final resize before persisting it.
        GLib.timeout_add(150, self.save_geometry)
        return True

    def save_geometry(self):
        x, y = self.get_position(); width, height = self.get_size()
        saved = read_json(POSITION)
        saved.update(x=x, y=y, width=width, height=height)
        write_private(POSITION, saved)
        return False

    def set_position(self):
        self.save_geometry()
        saved = read_json(POSITION)
        saved['preferred_position'] = {'x': saved['x'], 'y': saved['y']}
        write_private(POSITION, saved)
        self.status.set_text('Position saved')

    def reset_position(self):
        self.place(read_json(POSITION).get('preferred_position', {}))
        GLib.timeout_add(150, self.save_geometry)

    def drag_start(self, _widget, event):
        if event.button == 1:
            self.drag = (event.x_root, event.y_root, self.get_position())
        return True

    def drag_move(self, _widget, event):
        if self.drag:
            x, y, (old_x, old_y) = self.drag
            self.move(int(old_x+event.x_root-x), int(old_y+event.y_root-y))
        return True

    def drag_end(self, *_args):
        if self.drag:
            self.save_geometry(); self.drag = None
        return True

    def select_day(self, day):
        self.undated_view = False
        self.selected = day; self.year, self.month = day.year, day.month; self.render()

    def shift_month(self, delta):
        value = self.year*12+self.month-1+delta
        self.year, month = divmod(value, 12); self.month = month+1; self.render()

    def go_today(self):
        self.select_day(self.today())

    def render(self):
        today = self.today()
        self.date_label.set_text(today.strftime('%a, %d %B').replace(' 0', ' '))
        self.month_label.set_text(f'{calendar.month_name[self.month]} {self.year}')
        for child in self.grid.get_children(): self.grid.remove(child)
        for i, name in enumerate(['S', 'M', 'T', 'W', 'T', 'F', 'S']):
            self.grid.attach(label(name, 'weekday', .5), i, 0, 1, 1)
        events = self.data.get('events', [])
        for row, week in enumerate(calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month), 1):
            for col, day in enumerate(week):
                b = self.button(str(day.day), lambda _, d=day: self.select_day(d), day.strftime('%A, %d %B'))
                style = b.get_style_context(); style.add_class('day')
                if day.month != self.month: style.add_class('outside')
                if day == today: style.add_class('today')
                if day == self.selected: style.add_class('selected')
                if any(on_day(e, day, self.timezone) for e in events): style.add_class('event-day')
                self.grid.attach(b, col, row, 1, 1)
        for child in self.agenda.get_children(): self.agenda.remove(child)
        if self.undated_view:
            visible = self.data.get('undated_tasks', [])
            self.agenda_heading.set_text('UNSCHEDULED TASKS')
        else:
            visible = [e for e in events if on_day(e, self.selected, self.timezone)]
            self.agenda_heading.set_text(('TODAY · ' if self.selected == today else '')+
                                         self.selected.strftime('%A, %d %B').upper())
        for event in visible:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            box.get_style_context().add_class('event')
            prefix = ('✓ ' if event.get('completed') else '☐ ') if event.get('type') == 'task' else ''
            title = label(prefix+event['title'], 'event-title'); title.set_max_width_chars(34)
            title.set_ellipsize(Pango.EllipsizeMode.END); title.set_tooltip_text(event['title'])
            if 'start' in event:
                start, _ = event_bounds(event, self.timezone)
                stamp = start.strftime('%a %d %b') + (' · All day' if event['all_day'] else start.strftime(' · %-I:%M %p'))
            else: stamp = 'No due date'
            if event.get('type') == 'task': stamp += ' · Completed' if event.get('completed') else ' · Task'
            if event.get('completed'): box.get_style_context().add_class('completed')
            box.pack_start(title, False, False, 0); box.pack_start(label(stamp, 'muted'), False, False, 0)
            self.agenda.pack_start(box, False, False, 0)
        if not visible:
            message = 'No events or tasks for this day' if self.data.get('updated') else 'Connecting your calendar…'
            self.agenda.pack_start(label(message, 'muted'), False, False, 12)
        if self.offline:
            self.status.set_text('Offline · Cached events' if self.data else 'Calendar connection unavailable')
        elif self.data.get('updated'):
            when = datetime.fromisoformat(self.data['updated']).astimezone().strftime('%-I:%M %p')
            self.status.set_text('Synced '+when)
        else:
            self.status.set_text('Private · Read only')
        self.status.set_tooltip_text('Calendar time zone: '+self.timezone+'\nRefreshes every five minutes')
        self.panel.show_all()

    def refresh(self):
        if self.busy: return True
        self.cfg = read_json(CONFIG, self.cfg)
        self.busy = True
        def work():
            try:
                data = synchronize(self.cfg); GLib.idle_add(self.sync_finished, data, False)
            except Exception:
                GLib.idle_add(self.sync_finished, None, True)
        threading.Thread(target=work, daemon=True).start()
        return True

    def sync_finished(self, data, failed):
        self.busy = False
        self.offline = failed
        if data:
            self.data = data; self.render()
        if failed:
            self.status.set_text('Offline · Cached events' if self.data else 'Calendar connection unavailable')
        return False

    def minute_tick(self):
        self.render(); return True

    def open_calendar(self):
        import urllib.parse
        account = urllib.parse.quote(self.cfg.get('account', ''))
        webbrowser.open('https://calendar.google.com/calendar/?authuser='+account)

    def show_menu(self):
        menu = Gtk.Menu()
        menu.get_style_context().add_class('calendar-menu')
        for text, callback in [('Refresh', self.refresh), ('Open Google Calendar', self.open_calendar),
                               ('Unscheduled tasks', self.show_undated),
                               ('Set position', self.set_position),
                               ('Reset position', self.reset_position),
                               ('Quit widget', Gtk.main_quit)]:
            item = Gtk.MenuItem(label=text); item.connect('activate', lambda _, fn=callback: fn()); menu.append(item)
        menu.show_all(); menu.popup_at_pointer(None)

    def show_undated(self):
        self.undated_view = True; self.render()

if __name__ == '__main__':
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp'))/'laptop-calendar.lock'
    lock = runtime.open('a+')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit(0)
    window = CalendarWidget()
    Gtk.main()
