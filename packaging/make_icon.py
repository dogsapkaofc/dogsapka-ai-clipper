"""Render the application icon (uses Qt, no extra dependencies).

    python packaging/make_icon.py                 -> packaging/dogsapka.ico (Windows)
    python packaging/make_icon.py --png out.png   -> 1024x1024 PNG (macOS .icns source, store/app icons)
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication([])
from dogsapka_clipper.ui.icons import app_icon  # noqa: E402

if "--png" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--png") + 1])
    pm = app_icon().pixmap(1024, 1024)
    if pm.width() < 1024:      # an icon built from small pixmaps: scale up smoothly
        from PySide6.QtCore import Qt
        pm = pm.scaled(1024, 1024, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm.save(str(out), "PNG")
    print(f"wrote {out} ({pm.width()}x{pm.height()})")
    sys.exit(0)

out = Path(__file__).with_name("dogsapka.ico")
pm = app_icon().pixmap(256, 256)
if not pm.save(str(out), "ICO"):
    pm.save(str(out.with_suffix(".png")), "PNG")
    print("ICO writer unavailable; wrote PNG instead")
else:
    print(f"wrote {out}")
