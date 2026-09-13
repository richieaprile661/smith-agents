"""Windows placement follows the monitor under the widget and clears the taskbar."""
import sys
import unittest
from types import SimpleNamespace


@unittest.skipUnless(sys.platform == 'win32', 'uses the Windows monitor API')
class WindowsScreenTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        import ctypes.wintypes as wintypes
        from smith_agents import platform_win32
        self.ctypes, self.wintypes, self.win = ctypes, wintypes, platform_win32
        # A zero-sized screen makes the old single-screen guess fail loudly.
        self.root = SimpleNamespace(winfo_screenwidth=lambda: 0, winfo_screenheight=lambda: 0)

    def monitors(self):
        rects = []
        proto = self.ctypes.WINFUNCTYPE(self.wintypes.BOOL, self.wintypes.HMONITOR, self.wintypes.HDC,
                                        self.ctypes.POINTER(self.wintypes.RECT), self.wintypes.LPARAM)

        def visit(_monitor, _dc, rect, _param):
            r = rect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return True

        self.ctypes.windll.user32.EnumDisplayMonitors(None, None, proto(visit), 0)
        return rects

    def test_primary_monitor_reports_the_work_area_without_the_taskbar(self):
        work = self.wintypes.RECT()
        self.ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, self.ctypes.byref(work), 0)  # SPI_GETWORKAREA
        self.assertEqual(self.win.screen_bounds(self.root, (work.left + 1, work.top + 1)),
                         (work.left, work.top, work.right - work.left, work.bottom - work.top))

    def test_each_monitor_is_chosen_by_a_point_on_it(self):
        for left, top, right, bottom in self.monitors():
            with self.subTest(monitor=(left, top, right, bottom)):
                x, y, width, height = self.win.screen_bounds(self.root, ((left + right) // 2, (top + bottom) // 2))
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                self.assertTrue(left <= x and top <= y and x + width <= right and y + height <= bottom)

    def test_a_point_off_every_screen_uses_the_nearest_monitor(self):
        _x, _y, width, height = self.win.screen_bounds(self.root, (10 ** 6, 10 ** 6))
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)


if __name__ == '__main__':
    unittest.main()
