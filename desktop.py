#!/usr/bin/env python3
"""Portable Qt calendar. Linux's existing GTK desktop remains available."""
import argparse
import html
import sys
import threading
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import QDate, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QCalendarWidget, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)
from backend import CONFIG, CACHE, read_json, write_private, synchronize, on_day
from app_paths import CONFIG_DIR, CACHE_DIR, STATE_DIR


def demo_data():
    today = datetime.now().date()
    return {'timezone': 'UTC', 'updated': 'Synthetic preview', 'events': [
        {'title': 'Research reading group', 'calendar': 'Demo calendar', 'all_day': True,
         'start': today.isoformat(), 'end': (today + timedelta(days=1)).isoformat()},
        {'title': 'Review experiment notes', 'calendar': 'Demo tasks', 'type': 'task',
         'all_day': True, 'start': today.isoformat(),
         'end': (today + timedelta(days=1)).isoformat()}],
        'undated_tasks': [{'title': 'Plan next week', 'calendar': 'Demo tasks'}]}


class Results(QObject):
    completed = Signal(str, object, str)


class CalendarWindow(QWidget):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.cfg = {} if demo else read_json(CONFIG)
        self.data = demo_data() if demo else read_json(CACHE)
        self.busy = False
        self.unscheduled = False
        self.results = Results(self)
        self.results.completed.connect(self.finished)
        self.setWindowTitle('Desktop Calendar' + (' — Demo' if demo else ''))
        self.resize(460, 690)
        self.setMinimumSize(340, 440)
        self.settings = QSettings('0xSaadBinSultan', 'LaptopCalendar')
        if not demo and self.settings.value('geometry'):
            self.restoreGeometry(self.settings.value('geometry'))
            if not any(self.frameGeometry().intersects(s.availableGeometry())
                       for s in QApplication.screens()):
                self.move(QApplication.primaryScreen().availableGeometry().topLeft())
        self.setWindowOpacity(max(.35, min(1., float(self.cfg.get('opacity', .93)))))
        layout = QVBoxLayout(self)
        heading = QLabel('YOUR CALENDAR')
        heading.setStyleSheet('font-size:20px;font-weight:600;padding:8px')
        layout.addWidget(heading)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        today = QPushButton('Today')
        today.clicked.connect(self.today)
        settings = QPushButton('Settings')
        settings.clicked.connect(self.configure)
        self.connect_button = QPushButton('Connect Google')
        self.connect_button.clicked.connect(self.connect_google)
        for button in (today, self.refresh_button, settings, self.connect_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.selectionChanged.connect(self.show_day)
        layout.addWidget(self.calendar)
        tasks = QPushButton('Unscheduled tasks')
        tasks.clicked.connect(self.show_tasks)
        layout.addWidget(tasks)
        self.agenda = QTextBrowser()
        self.agenda.setOpenExternalLinks(False)
        layout.addWidget(self.agenda, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(1, int(self.cfg.get('refresh_minutes', 5))) * 60000)
        self.render()
        if not demo and (self.cfg.get('source') or self.cfg.get('calendars')):
            QTimer.singleShot(0, self.refresh)
        elif not demo:
            self.status.setText('Open Settings to choose your OAuth client, then Connect Google. '
                                'You can browse an existing cache without connecting.')

    def zone(self):
        name = self.data.get('timezone', self.cfg.get('timezone', 'UTC'))
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            return 'UTC'

    def today(self):
        day = datetime.now(ZoneInfo(self.zone())).date()
        self.calendar.setSelectedDate(QDate(day.year, day.month, day.day))
        self.show_day()

    def show_day(self):
        self.unscheduled = False
        self.render_agenda()

    def show_tasks(self):
        self.unscheduled = True
        self.render_agenda()

    def render_agenda(self):
        day = self.calendar.selectedDate().toPython()
        if self.unscheduled:
            title, rows = 'Unscheduled tasks', self.data.get('undated_tasks', [])
        else:
            title = day.strftime('%A, %d %B %Y')
            rows = []
            for row in self.data.get('events', []):
                try:
                    if on_day(row, day, self.zone()):
                        rows.append(row)
                except (KeyError, ValueError, TypeError):
                    continue
        out = ['<h3>' + html.escape(title) + '</h3>']
        for row in rows:
            prefix = '✓ ' if row.get('completed') else '☐ ' if row.get('type') == 'task' else ''
            when = 'All day'
            if not row.get('all_day', True):
                try:
                    when = datetime.fromisoformat(row['start']).astimezone(ZoneInfo(self.zone())).strftime('%H:%M')
                except (ValueError, KeyError):
                    when = ''
            out.append('<p><b>' + html.escape(prefix + row.get('title', 'Untitled')) +
                       '</b><br><span style="color:#aebbd1">' +
                       html.escape(when + ' · ' + row.get('calendar', 'Calendar')) + '</span></p>')
        if not rows:
            out.append('<p>No items.</p>')
        self.agenda.setHtml(''.join(out))

    def render(self):
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        marker = QTextCharFormat()
        marker.setForeground(QColor('#8fbeff'))
        for row in self.data.get('events', []):
            try:
                start = datetime.fromisoformat(row['start']).date()
                end = datetime.fromisoformat(row['end']).date()
                # Bound expansion for malformed or very long cached events.
                for n in range(min(433, max(1, (end - start).days + 1))):
                    day = start + timedelta(days=n)
                    if on_day(row, day, self.zone()):
                        self.calendar.setDateTextFormat(QDate(day.year, day.month, day.day), marker)
            except (KeyError, ValueError, TypeError):
                continue
        self.render_agenda()
        self.status.setText('Last sync: ' + self.data.get('updated', 'Never') +
                            ' · ' + self.zone() + (' · Demo data' if self.demo else ''))

    def run_job(self, kind, operation):
        if self.busy or self.demo:
            return
        self.busy = True
        self.refresh_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.status.setText('Approve read-only access in your browser…' if kind == 'connect' else 'Refreshing…')
        bridge = self.results
        def work():
            data, error = None, ''
            try:
                data = operation()
            except Exception as exc:
                # Transport error messages can contain private URLs/tokens.
                error = type(exc).__name__
            try:
                bridge.completed.emit(kind, data, error)
            except RuntimeError:
                pass  # Window was closed while a daemon worker was finishing.
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        self.run_job('sync', lambda: synchronize(read_json(CONFIG)))

    def connect_google(self):
        if not (CONFIG_DIR / 'client.json').exists() and not self.cfg.get('oauth_client'):
            self.configure()
            if not self.cfg.get('oauth_client'):
                return
        def authorize():
            from connect_google import main
            main(open_browser=True)
        self.run_job('connect', authorize)

    def finished(self, kind, data, error):
        self.busy = False
        self.refresh_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        if error:
            self.status.setText('Connection unavailable (' + error + '). Cached data preserved. '
                                'Check Settings and your Google connection.')
        elif kind == 'connect':
            self.cfg = read_json(CONFIG)
            self.refresh()
        else:
            self.data = data
            self.render()

    def configure(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Calendar settings')
        form = QFormLayout(dialog)
        account = QLineEdit(self.cfg.get('account', ''))
        account.setPlaceholderText('Optional: verify a specific Google account')
        timezone = QLineEdit(self.cfg.get('timezone', 'UTC'))
        client = QLineEdit(self.cfg.get('oauth_client', str(CONFIG_DIR / 'client.json')))
        browse = QPushButton('Choose OAuth client JSON')
        def choose():
            path, _ = QFileDialog.getOpenFileName(dialog, 'Desktop OAuth client', '', 'JSON (*.json)')
            if path:
                client.setText(path)
        browse.clicked.connect(choose)
        opacity = QDoubleSpinBox()
        opacity.setRange(.35, 1.)
        opacity.setSingleStep(.05)
        opacity.setValue(float(self.cfg.get('opacity', .93)))
        form.addRow('Google account', account)
        form.addRow('IANA timezone', timezone)
        form.addRow('OAuth client', client)
        form.addRow('', browse)
        form.addRow('Opacity', opacity)
        form.addRow(QLabel('Read-only Google Calendar and Tasks. No shared API key.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                ZoneInfo(timezone.text().strip())
            except (ZoneInfoNotFoundError, ValueError):
                QMessageBox.warning(self, 'Invalid timezone', 'Use an IANA timezone such as Asia/Dhaka or UTC.')
                return
            cfg = {**self.cfg, 'account': account.text().strip(),
                   'timezone': timezone.text().strip(), 'oauth_client': client.text().strip(),
                   'opacity': opacity.value()}
            try:
                write_private(CONFIG, cfg)
            except OSError:
                QMessageBox.warning(self, 'Settings unavailable', 'Could not write your configuration directory.')
                return
            self.cfg = cfg
            self.setWindowOpacity(opacity.value())

    def closeEvent(self, event):
        if not self.demo:
            self.settings.setValue('geometry', self.saveGeometry())
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='Read-only desktop calendar for Windows, macOS and Linux')
    parser.add_argument('--demo', action='store_true', help='Use synthetic data; no credentials or network')
    parser.add_argument('--paths', action='store_true', help='Show configuration, cache and state directories')
    parser.add_argument('--export-preview', type=Path, help='Save a synthetic preview and exit; requires --demo')
    args = parser.parse_args()
    if args.export_preview and not args.demo:
        parser.error('--export-preview requires --demo')
    if args.paths:
        for name, path in [('Config', CONFIG_DIR), ('Cache', CACHE_DIR), ('State', STATE_DIR)]:
            print(f'{name}: {path}')
        return
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    palette = app.palette()
    for role, color in [(QPalette.ColorRole.Window, '#151b27'),
                        (QPalette.ColorRole.Base, '#20293a'),
                        (QPalette.ColorRole.AlternateBase, '#29354b'),
                        (QPalette.ColorRole.Button, '#29354b'),
                        (QPalette.ColorRole.Text, '#edf0f7'),
                        (QPalette.ColorRole.WindowText, '#edf0f7'),
                        (QPalette.ColorRole.ButtonText, '#edf0f7'),
                        (QPalette.ColorRole.Highlight, '#426296'),
                        (QPalette.ColorRole.HighlightedText, '#ffffff')]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet('''QWidget { background:#151b27; color:#edf0f7; font-size:12px; }
        QPushButton { background:#29354b; border:0; border-radius:6px; padding:7px; }
        QPushButton:hover { background:#3b5071; } QPushButton:disabled { color:#70809a; }
        QTextBrowser,QLineEdit { background:#20293a; border:1px solid #46516a; padding:6px; }
        QCalendarWidget QAbstractItemView { background:#20293a; selection-background-color:#426296; }''')
    window = CalendarWindow(args.demo)
    window.show()
    if args.export_preview:
        def export():
            args.export_preview.parent.mkdir(parents=True, exist_ok=True)
            success = window.grab().save(str(args.export_preview))
            app.exit(0 if success else 1)
        QTimer.singleShot(150, export)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
