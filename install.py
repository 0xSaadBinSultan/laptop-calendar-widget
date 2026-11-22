#!/usr/bin/env python3
"""Install the desktop widget for the current user without administrator access."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import json

if sys.platform != 'linux':
    raise SystemExit('This installer is for Linux/XFCE. On Windows/macOS use python desktop.py; see README.md.')
if os.geteuid() == 0: raise SystemExit('Run as your desktop user, not root.')
source = Path(__file__).resolve().parent
home = Path.home()
target = home/'.local/share/laptop-calendar'
target.mkdir(parents=True, exist_ok=True)
config_dir = home/'.config/laptop-calendar'
config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
config = config_dir/'config.json'
if not config.exists():
    with os.fdopen(os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump(json.loads((source/'config.example.json').read_text()), out, indent=2)
for name in ['backend.py', 'app_paths.py', 'widget.py', 'google_api.py', 'connect_google.py', 'requirements.txt']:
    shutil.copy2(source/name, target/name)
python = target/'.venv/bin/python'
if not python.exists():
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(target/'.venv')], check=True)
subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(target/'requirements.txt')], check=True)
units = home/'.config/systemd/user'; units.mkdir(parents=True, exist_ok=True)
(units/'laptop-calendar-widget.service').write_text(f'''[Unit]
Description=Private desktop calendar widget
PartOf=graphical-session.target

[Service]
Type=simple
Environment=DISPLAY=:0
Environment=XAUTHORITY={home}/.Xauthority
Environment=NO_AT_BRIDGE=1
ExecStart={python} {target}/widget.py
Restart=on-failure
RestartSec=10
Nice=10
UMask=0077
''')
autostart = home/'.config/autostart'; autostart.mkdir(parents=True, exist_ok=True)
(autostart/'laptop-calendar-widget.desktop').write_text('''[Desktop Entry]
Type=Application
Name=Desktop Calendar
Comment=Private Google Calendar on your desktop
Exec=systemctl --user start laptop-calendar-widget.service
OnlyShowIn=XFCE;
Terminal=false
''')
bindir = home/'.local/bin'; bindir.mkdir(parents=True, exist_ok=True)
launcher = bindir/'laptop-calendar'
launcher.write_text(f'''#!/bin/sh
case "$1" in
 --status|--check) exec "{python}" "{target}/backend.py" "$1" ;;
 --connect) exec "{python}" "{target}/connect_google.py" --open-browser ;;
 *) exec systemctl --user start laptop-calendar-widget.service ;;
esac
'''); launcher.chmod(0o755)
apps = home/'.local/share/applications'; apps.mkdir(parents=True, exist_ok=True)
(apps/'laptop-calendar.desktop').write_text(f'''[Desktop Entry]
Type=Application
Name=Desktop Calendar
Comment=Show your Google Calendar desktop widget
Exec={launcher}
Icon=office-calendar
Categories=Office;Calendar;
Terminal=false
''')
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
print('Desktop Calendar installed. Private configuration and credentials are kept outside the source package.')
