"""Application entry point."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dogsapka-ai-clipper")
    ap.add_argument("--data-dir", help="data folder (default: %%LOCALAPPDATA%%/DOGSAPKA_AI_CLIPPER)")
    ap.add_argument("--no-monitor", action="store_true", help="do not poll Twitch (offline/simulation use)")
    ap.add_argument("--simulate", action="store_true", help="start a simulated livestream on launch")
    ap.add_argument("--screenshot", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from dogsapka_clipper.core.i18n import set_language
    from dogsapka_clipper.core.logging_setup import setup_logging
    from dogsapka_clipper.core.paths import AppPaths
    paths = AppPaths.create(args.data_dir)
    setup_logging(paths.log_file)
    log = logging.getLogger("dogsapka")

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
