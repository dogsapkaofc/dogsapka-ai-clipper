"""Application entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    if raw[:1] == ["--streamlink"]:
        # The packaged app doubles as the Streamlink CLI (macOS bundle / frozen builds without a separate
        # streamlink executable): "<app> --streamlink <streamlink args>".
        return _run_streamlink(raw[1:])
    ap = argparse.ArgumentParser(prog="dogsapka-ai-clipper")
    ap.add_argument("--data-dir", help="data folder (default: %%LOCALAPPDATA%%/DOGSAPKA_AI_CLIPPER)")
    ap.add_argument("--no-monitor", action="store_true", help="do not poll Twitch (offline/simulation use)")
    ap.add_argument("--simulate", action="store_true", help="start a simulated livestream on launch")
    ap.add_argument("--screenshot", help=argparse.SUPPRESS)
    ap.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from dogsapka_clipper.core.i18n import set_language
    from dogsapka_clipper.core.logging_setup import setup_logging
    from dogsapka_clipper.core.paths import AppPaths
    if args.wait_pid:      # restarted by the previous instance: let it release the database first
        _wait_for_exit(args.wait_pid)
    move_error = None
    if not args.data_dir:
        from dogsapka_clipper.core.paths import apply_pending_move
        move_error = apply_pending_move()      # data folder change requested in Settings
    paths = AppPaths.create(args.data_dir)
    setup_logging(paths.log_file)
    log = logging.getLogger("dogsapka")
    if move_error:
        log.error(move_error)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DOGSAPKA AI CLIPPER")
    app.setOrganizationName("DOGSAPKA")
    from dogsapka_clipper.ui import theme
    from dogsapka_clipper.ui.icons import app_icon
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(app_icon())
    from dogsapka_clipper.ui.widgets.common import WheelGuard
    wheel_guard = WheelGuard()               # mouse wheel scrolls pages, never changes unfocused settings
    app.installEventFilter(wheel_guard)
    if move_error:
        QMessageBox.warning(None, "DOGSAPKA AI CLIPPER", move_error)

    def excepthook(t, v, tb):
        log.error("Unhandled error:\n%s", "".join(traceback.format_exception(t, v, tb)))
        try:
            QMessageBox.critical(None, "DOGSAPKA AI CLIPPER", f"An unexpected error occurred:\n{v}\n\n"
                                                          f"Details were written to {paths.log_file}")
        except Exception:
            pass
    sys.excepthook = excepthook

    from dogsapka_clipper.services.backend import Backend
    try:
        backend = Backend(paths)
    except Exception as e:
        log.exception("Startup failed")
        QMessageBox.critical(None, "DOGSAPKA AI CLIPPER", f"Could not open the database:\n{e}\n\nLog: {paths.log_file}")
        return 1
    set_language(backend.settings.interface.language)
    backend.start(monitor=not args.no_monitor)

    if not backend.settings.interface.first_run_complete and not args.screenshot:
        from dogsapka_clipper.ui.setup_wizard import SetupWizard
        SetupWizard(backend).exec()
        set_language(backend.settings.interface.language)

    from dogsapka_clipper.ui.main_window import MainWindow
    win = MainWindow(backend)
    # the window's X hides to the system tray; the app exits from the tray menu ("Close")
    app.setQuitOnLastWindowClosed(win.tray is None)
    win.show()
    if args.simulate:
        from dogsapka_clipper.ui.bridge import run_async
        run_async(backend.start_simulation, lambda sid: win.open_live(sid))
    if args.screenshot:
        _screenshot_mode(app, win, args.screenshot)
    elif backend.settings.interface.auto_update_check:
        from PySide6.QtCore import QTimer
        from dogsapka_clipper.ui.update_dialog import check_for_updates
        QTimer.singleShot(6000, lambda: check_for_updates(win))     # after startup has settled
    rc = app.exec()
    backend.shutdown()
    return rc


def _screenshot_mode(app, win, out_dir: str) -> None:
    """Internal: capture every page (used for documentation / UI review)."""
    from pathlib import Path
    from PySide6.QtCore import QTimer
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pages = [p[0] for p in win.PAGES]
    extra = os.environ.get("DOGSAPKA_SHOT_CLIP")

    def shoot(i=0):
        if i >= len(pages):
            app.quit()
            return
        key = pages[i]
        if key == "editor" and extra:
            win.open_editor(int(extra))
        else:
            win.navigate(key)

        def grab():
            win.grab().save(str(out / f"{i}_{key}.png"))
            shoot(i + 1)
        QTimer.singleShot(4000 if key == "editor" else 1200, grab)
    QTimer.singleShot(1500, shoot)


def _wait_for_exit(pid: int, timeout: float = 90.0) -> None:
    import time
    try:
        import psutil
    except ImportError:
        time.sleep(5)
        return
    end = time.time() + timeout
    while time.time() < end and psutil.pid_exists(pid):
        time.sleep(0.25)


def _run_streamlink(args: list[str]) -> int:
    from streamlink_cli.main import main as streamlink_main
    sys.argv = ["streamlink", *args]
    try:
        streamlink_main()
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else 1
    return 0
