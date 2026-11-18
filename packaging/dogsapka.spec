# PyInstaller spec - build with:  pyinstaller packaging/dogsapka.spec --noconfirm
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
datas = collect_data_files("faster_whisper") + collect_data_files("cv2", includes=["data/*.xml"])
datas += collect_data_files("tzdata")
datas += [(str(root / "dogsapka_clipper" / "assets" / "face_detection_yunet_2023mar.onnx"), "dogsapka_clipper/assets")]
hidden = collect_submodules("dogsapka_clipper") + ["keyring.backends.Windows", "ctranslate2"]
hidden += collect_submodules("tzdata")
icon = str(root / "packaging" / "dogsapka.ico") if (root / "packaging" / "dogsapka.ico").exists() else None

a = Analysis([str(root / "dogsapka_clipper" / "__main__.py")], pathex=[str(root)], datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "IPython", "pytest", "pandas", "scipy"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DOGSAPKA AI CLIPPER", console=False, icon=icon,
          version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DOGSAPKA AI CLIPPER")
