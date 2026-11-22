"""Platform-native storage, with the original Linux paths preserved."""
import os
import sys
from pathlib import Path


def locations(platform=None, environ=None, home=None):
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if env.get('LAPTOP_CALENDAR_HOME'):
        root = Path(env['LAPTOP_CALENDAR_HOME']).expanduser()
        return root / 'config', root / 'cache', root / 'state'
    if platform == 'win32':
        config = Path(env.get('APPDATA', home / 'AppData/Roaming')) / 'LaptopCalendar'
        local = Path(env.get('LOCALAPPDATA', home / 'AppData/Local')) / 'LaptopCalendar'
        return config, local / 'Cache', local / 'State'
    if platform == 'darwin':
        root = home / 'Library/Application Support/LaptopCalendar'
        return root, home / 'Library/Caches/LaptopCalendar', root / 'State'
    return (Path(env.get('XDG_CONFIG_HOME', home / '.config')) / 'laptop-calendar',
            Path(env.get('XDG_CACHE_HOME', home / '.cache')) / 'laptop-calendar',
            Path(env.get('XDG_STATE_HOME', home / '.local/state')) / 'laptop-calendar')


CONFIG_DIR, CACHE_DIR, STATE_DIR = locations()
