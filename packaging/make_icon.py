"""Render the application icon to packaging/dogsapka.ico (uses Qt, no extra dependencies)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication([])
from dogsapka_clipper.ui.icons import app_icon  # noqa: E402

out = Path(__file__).with_name("dogsapka.ico")
pm = app_icon().pixmap(256, 256)
if not pm.save(str(out), "ICO"):
    pm.save(str(out.with_suffix(".png")), "PNG")
    print("ICO writer unavailable; wrote PNG instead")
else:
    print(f"wrote {out}")
