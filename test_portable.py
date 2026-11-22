"""Offline portability, data isolation and Qt rendering checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app_paths import locations
from backend import read_json, write_private
from desktop import CalendarWindow


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_platform_paths_preserve_linux_and_separate_cache(self):
        home = Path(tempfile.gettempdir()) / 'calendar-user'
        config, cache, state = locations('linux', {}, home)
        self.assertEqual(config, home / '.config/laptop-calendar')
        self.assertEqual(cache, home / '.cache/laptop-calendar')
        self.assertEqual(state, home / '.local/state/laptop-calendar')
        config, cache, state = locations('win32', {}, home)
        self.assertEqual(config, home / 'AppData/Roaming/LaptopCalendar')
        self.assertEqual(cache, home / 'AppData/Local/LaptopCalendar/Cache')
        config, cache, state = locations('darwin', {}, home)
        self.assertEqual(config, home / 'Library/Application Support/LaptopCalendar')
        self.assertEqual(cache, home / 'Library/Caches/LaptopCalendar')

    def test_override_keeps_test_or_portable_data_in_one_root(self):
        root = Path(tempfile.gettempdir()) / 'calendar-isolated'
        self.assertEqual(locations('win32', {'LAPTOP_CALENDAR_HOME': str(root)}),
                         (root / 'config', root / 'cache', root / 'state'))

    def test_unicode_cache_and_atomic_failure_preserve_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.json'
            write_private(path, {'title': 'গবেষণা সভা'})
            with patch('pathlib.Path.replace', side_effect=OSError('read-only')):
                with self.assertRaises(OSError):
                    write_private(path, {'title': 'new'})
            self.assertEqual(read_json(path)['title'], 'গবেষণা সভা')
            self.assertEqual(list(Path(folder).glob('.calendar-*')), [])

    def test_demo_is_offline_and_agenda_treats_titles_as_text(self):
        with patch('desktop.synchronize', side_effect=AssertionError('Demo must stay offline')):
            window = CalendarWindow(demo=True)
            window.data['events'][0]['title'] = '<script>unsafe</script>'
            window.show()
            self.app.processEvents()
            window.render()
            self.assertIn('<script>unsafe</script>', window.agenda.toPlainText())
            self.assertIn('Review experiment notes', window.agenda.toPlainText())
            self.assertGreater(window.grab().width(), 300)
            window.refresh()
            self.assertFalse(window.busy)
            window.close()
            self.app.processEvents()
            self.assertFalse(window.isVisible())

    def test_unscheduled_tasks_are_separate(self):
        window = CalendarWindow(demo=True)
        window.show_tasks()
        self.assertIn('Plan next week', window.agenda.toPlainText())
        self.assertNotIn('Research reading group', window.agenda.toPlainText())
        window.close()


if __name__ == '__main__':
    unittest.main()
