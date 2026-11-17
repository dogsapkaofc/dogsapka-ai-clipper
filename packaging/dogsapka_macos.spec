# PyInstaller spec for macOS - build with:  bash packaging/build_macos.sh
# Produces "dist/DOGSAPKA AI CLIPPER.app". FFmpeg/FFprobe are copied into Contents/MacOS/tools by the build
# script. Streamlink is bundled as a Python package and run by the app itself ("<app> --streamlink …").
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
import sys
sys.path.insert(0, str(root))
from dogsapka_clipper import __version__ as VERSION  # noqa: E402
datas = collect_data_files("faster_whisper") + collect_data_files("cv2", includes=["data/*.xml"])
datas += collect_data_files("streamlink") + collect_data_files("streamlink_cli")
datas += [(str(root / "dogsapka_clipper" / "assets" / "face_detection_yunet_2023mar.onnx"), "dogsapka_clipper/assets")]
hidden = (collect_submodules("dogsapka_clipper") + collect_submodules("streamlink") + collect_submodules("streamlink_cli")
          + ["keyring.backends.macOS", "ctranslate2"])
icns = root / "packaging" / "dogsapka.icns"

a = Analysis([str(root / "dogsapka_clipper" / "__main__.py")], pathex=[str(root)], datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "IPython", "pytest", "pandas", "scipy"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DOGSAPKA AI CLIPPER", console=False,
          argv_emulation=False, target_arch=None, codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DOGSAPKA AI CLIPPER")
app = BUNDLE(
    coll,
    name="DOGSAPKA AI CLIPPER.app",
    icon=str(icns) if icns.exists() else None,
    bundle_identifier="com.dogsapka.aiclipper",
    info_plist={
        "CFBundleName": "DOGSAPKA AI CLIPPER",
        "CFBundleDisplayName": "DOGSAPKA AI CLIPPER",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSRequiresAquaSystemAppearance": False,
        "LSApplicationCategoryType": "public.app-category.video",
    },
)
