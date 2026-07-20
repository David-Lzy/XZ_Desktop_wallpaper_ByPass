from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import XZWallpaperBypass as bypass


class StartupCoordinationTests(unittest.TestCase):
    def test_fresh_patch_does_not_send_unsafe_repaint_message(self) -> None:
        inspected = {
            "pid": 42,
            "state": "patched",
            "modules": [],
            "per_monitor_paths": {"state": "disabled_4.1.3.8"},
            "desktop_layer": {"state": "visible_xiaozhi"},
        }
        with (
            patch.object(bypass, "resolve_modules", return_value=[]),
            patch.object(bypass, "open_process", return_value=99),
            patch.object(bypass, "close_handle"),
            patch.object(bypass, "trigger_repaint") as repaint,
            patch.object(bypass, "inspect_pid", return_value=inspected),
        ):
            result = bypass.change_pid(42, "patched")

        repaint.assert_not_called()
        self.assertNotIn("repaint", result)

    def test_external_wdd_state_marker_calls_verified_xz_repaint(self) -> None:
        inspected = {
            "pid": 42,
            "state": "patched",
            "modules": [],
            "per_monitor_paths": {"state": "disabled_4.1.3.8"},
            "desktop_layer": {"state": "visible_xiaozhi"},
        }
        layer = {"state": "visible_xiaozhi", "hwnd": "0x1234"}

        with (
            patch.object(
                bypass,
                "EXTERNAL_LAYER_REFRESH_DEBOUNCE_SECONDS",
                0.0,
            ),
            patch.object(
                bypass,
                "external_wallpaper_state_path",
                return_value=Path("state.json"),
            ),
            patch.object(bypass, "file_marker", return_value=(100, 200)),
            patch.object(bypass, "current_virtual_desktop_id", return_value=None),
            patch.object(bypass, "find_processes", return_value=[42]),
            patch.object(bypass, "inspect_pid", return_value=inspected),
            patch.object(
                bypass,
                "repaint_live_pid",
                return_value="xz_setwallpaper_thread_7_exit_0x00000000",
            ) as repaint,
            patch.object(
                bypass,
                "set_desktop_layer",
                return_value=layer,
            ) as refresh_layer,
            patch.object(bypass, "print_result"),
            patch.object(bypass.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            result = bypass.watch(
                interval=0.25,
                requested_pid=None,
                as_json=False,
                desktop_mode="xz",
                wallpaper_refresh="external",
                startup_delay=0.0,
            )

        self.assertEqual(result, 0)
        repaint.assert_called_once_with(42)
        refresh_layer.assert_called_once_with(42, "xz")

    def test_external_mode_repaints_after_virtual_desktop_switch(self) -> None:
        inspected = {
            "pid": 42,
            "state": "patched",
            "modules": [],
            "per_monitor_paths": {"state": "disabled_4.1.3.8"},
            "desktop_layer": {"state": "visible_xiaozhi"},
        }
        layer = {"state": "visible_xiaozhi", "hwnd": "0x1234"}

        with (
            patch.object(
                bypass,
                "EXTERNAL_LAYER_REFRESH_DEBOUNCE_SECONDS",
                0.0,
            ),
            patch.object(bypass, "external_wallpaper_state_path", return_value=None),
            patch.object(
                bypass,
                "current_virtual_desktop_id",
                side_effect=["desktop-a", "desktop-b"],
            ),
            patch.object(bypass, "find_processes", return_value=[42]),
            patch.object(bypass, "inspect_pid", return_value=inspected),
            patch.object(
                bypass,
                "repaint_live_pid",
                return_value="xz_setwallpaper_thread_8_exit_0x00000000",
            ) as repaint,
            patch.object(bypass, "set_desktop_layer", return_value=layer),
            patch.object(bypass, "print_result"),
            patch.object(bypass.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            result = bypass.watch(
                interval=0.25,
                requested_pid=None,
                as_json=False,
                desktop_mode="xz",
                wallpaper_refresh="external",
                startup_delay=0.0,
            )

        self.assertEqual(result, 0)
        repaint.assert_called_once_with(42)

    def test_watcher_waits_after_desktop_window_becomes_ready(self) -> None:
        original = {
            "pid": 42,
            "state": "original",
            "modules": [],
            "per_monitor_paths": {"state": "disabled_4.1.3.8"},
            "desktop_layer": {"state": "visible_xiaozhi"},
        }
        with (
            patch.object(bypass, "external_wallpaper_state_path", return_value=None),
            patch.object(bypass, "current_virtual_desktop_id", return_value=None),
            patch.object(bypass, "find_processes", return_value=[42]),
            patch.object(bypass, "inspect_pid", return_value=original),
            patch.object(bypass, "change_pid") as change,
            patch.object(bypass.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            result = bypass.watch(
                interval=0.25,
                requested_pid=None,
                as_json=False,
                desktop_mode="xz",
                wallpaper_refresh="external",
                startup_delay=10.0,
            )

        self.assertEqual(result, 0)
        change.assert_not_called()


if __name__ == "__main__":
    unittest.main()
