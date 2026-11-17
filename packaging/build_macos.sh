#!/usr/bin/env bash
# Builds "DOGSAPKA AI CLIPPER.app" and a drag-to-Applications disk image (.dmg) on macOS.
# Requirements: macOS 12+, Python 3.12+ (python3), Xcode command line tools, internet access.
# Usage:  bash packaging/build_macos.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_NAME="DOGSAPKA AI CLIPPER"
ARCH="$(uname -m)"                                   # arm64 (Apple silicon) or x86_64 (Intel)
FF_ARCH=$([ "$ARCH" = "arm64" ] && echo arm64 || echo amd64)
PY="${PYTHON:-python3}"

echo "==> Creating virtual environment"
[ -d .venv-mac ] || "$PY" -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
VERSION="$(python -c 'import dogsapka_clipper; print(dogsapka_clipper.__version__)')"
echo "Version $VERSION"

echo "==> Bundled FFmpeg (static build for macOS $ARCH, includes VideoToolbox + libass)"
mkdir -p build/ffmpeg-mac
for tool in ffmpeg ffprobe; do
  if [ ! -x "build/ffmpeg-mac/$tool" ]; then
    curl -fsSL -o "build/ffmpeg-mac/$tool.zip" \
      "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip"
    unzip -o -q "build/ffmpeg-mac/$tool.zip" -d build/ffmpeg-mac
    chmod +x "build/ffmpeg-mac/$tool"
  fi
done
xattr -cr build/ffmpeg-mac || true
build/ffmpeg-mac/ffmpeg -hide_banner -version | head -1
build/ffmpeg-mac/ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_videotoolbox && echo "VideoToolbox: yes"
build/ffmpeg-mac/ffmpeg -hide_banner -filters 2>/dev/null | grep -qw subtitles && echo "libass: yes"

echo "==> Running tests"
PATH="$ROOT/build/ffmpeg-mac:$PATH" python -m pytest -q

echo "==> Generating icon"
QT_QPA_PLATFORM=offscreen python packaging/make_icon.py --png packaging/dogsapka_1024.png
ICONSET=build/dogsapka.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/dogsapka_1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) packaging/dogsapka_1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/dogsapka.icns

echo "==> PyInstaller"
pyinstaller packaging/dogsapka_macos.spec --noconfirm --distpath dist-mac --workpath build/pyinstaller-mac
APP="dist-mac/$APP_NAME.app"
mkdir -p "$APP/Contents/MacOS/tools"
cp build/ffmpeg-mac/ffmpeg build/ffmpeg-mac/ffprobe "$APP/Contents/MacOS/tools/"

echo "==> Ad-hoc code signature (no Apple developer ID)"
codesign --force --deep --sign - "$APP"

echo "==> Smoke test of the bundled app"
"$APP/Contents/MacOS/$APP_NAME" --streamlink --version
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/$APP_NAME" --data-dir "$ROOT/build/smoke-data" --no-monitor \
  --screenshot "$ROOT/build/smoke-shots" &
SMOKE_PID=$!
for _ in $(seq 1 90); do kill -0 $SMOKE_PID 2>/dev/null || break; sleep 1; done
if kill -0 $SMOKE_PID 2>/dev/null; then kill $SMOKE_PID; echo "App did not finish the screenshot run"; exit 1; fi
wait $SMOKE_PID
ls "$ROOT/build/smoke-shots"
grep -iE "error|traceback" "$ROOT/build/smoke-data/logs/"*.log && { echo "Errors in smoke log"; exit 1; } || true

echo "==> Disk image"
mkdir -p dist-mac/installer
STAGE=build/dmg-stage
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist-mac/installer/DOGSAPKA_AI_CLIPPER_${VERSION}_macOS_${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Done: $DMG"
