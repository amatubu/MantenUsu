#!/bin/bash
# build_cftool_mac.sh
# CF カードのメンテナンスツール(UsuMantenCFTool.py)の Mac 版のビルド(PyInstaller)。build_mac.sh の CF ツール版
#
# 使い方:
#   ./build_cftool_mac.sh           ... .app 形式。Finder からダブルクリックで起動する
#   ./build_cftool_mac.sh --folder  ... フォルダ形式。ターミナルから起動する(ログも見える。うまく動かないときの確認用)
#   Python は、有効にした環境の python3 → プロジェクトの隣の venv → プロジェクト内の venv / .venv
#   の順に探し、必要なライブラリ(PyInstaller・numpy・tkinter)が入っている最初のものを使う
#   使う Python を指定するとき: PYTHON=/path/to/python3 ./build_cftool_mac.sh
#
# できるもの:
#   scripts/dist/UsuMantenCFTool-mac/UsuMantenCFTool.app  ... アプリ
#   UsuMantenCFTool-mac.zip                               ... 上のフォルダを zip にしたもの
#
# 本体(UsuMantenViewer)との違い:
#   - 使うライブラリは numpy と tkinter だけ(obspy・matplotlib・Googleのライブラリは入れない)
#   - アプリの中の etc には、拠点のチャンネル(*.ch・*.tbl)とロガーの校正値(logger_cal.txt)を入れる
#   - Googleドライブの鍵(service_account)と外部ツール(win2sac_32 など)は使わないので入れない
#   - ユーザーの設定・データフォルダは本体と同じもの(app_paths.py)を使う。カードのイメージは
#     データフォルダの raw/image、台帳は cf_cards、WIN は daily_zips・source_zips に書く
#   - 校正値を新しく求めたら(raw_to_win.py --fit --save-cal)、ビルドし直すか、
#     ユーザーの設定の etc(~/Library/Application Support/UsuMantenViewer/etc)に logger_cal.txt を置く
set -euo pipefail

NAME=UsuMantenCFTool
MODE=app
if [ "${1:-}" = "--folder" ]; then
  MODE=folder
fi

# プロジェクトのフォルダ(このスクリプトのある場所)に移動
cd "$(dirname "$0")"
ROOT="$(pwd)"

# --- 使う Python を決める(必要なライブラリが入っているもの) ---
has_libs() {
  "$1" -c "import PyInstaller, numpy, tkinter" 2>/dev/null
}
if [ -n "${PYTHON:-}" ]; then
  CANDIDATES=("$PYTHON")
else
  CANDIDATES=(python3 "$ROOT/../venv/bin/python3" "$ROOT/venv/bin/python3" "$ROOT/.venv/bin/python3")
fi
FOUND=""
for py in "${CANDIDATES[@]}"; do
  if has_libs "$py"; then
    FOUND="$py"
    break
  fi
done
if [ -z "$FOUND" ]; then
  echo "エラー: PyInstaller・numpy・tkinter が入った Python が見つかりません。"
  echo "       探した場所: ${CANDIDATES[*]}"
  echo "       それらを入れた環境を有効にするか、PYTHON=/path/to/python3 で指定してください。"
  exit 1
fi
PYTHON="$FOUND"
echo "使用する Python: $("$PYTHON" -c 'import sys, tkinter; print(sys.executable, sys.version.split()[0], "/ Tk", tkinter.TkVersion)')"

# ツールのバージョン(UsuMantenCFTool.py の TOOL_VERSION。.app の情報に入れる)
VERSION="$(sed -n 's/^TOOL_VERSION = "\([^"]*\)".*/\1/p' scripts/$NAME.py)"
echo "ツールのバージョン: ${VERSION:-(読めませんでした)}"

# --- ビルド ---
cd scripts
# 使わない大きなライブラリは入れない(同じ環境に入っていても、ほかのスクリプトから引き込まれないように)
OPTS=(--onedir --noconfirm --name "$NAME"
      --workpath "build/$NAME" --distpath "dist"
      --exclude-module matplotlib --exclude-module obspy --exclude-module scipy --exclude-module pandas
      --exclude-module googleapiclient --exclude-module google --exclude-module PIL --exclude-module IPython)
if [ "$MODE" = app ]; then
  OPTS+=(--windowed --osx-bundle-identifier "jp.usumanten.cftool")   # .app を作る(ターミナルを開かずに起動する)
fi
# アプリに付く物(etc の初期値)をアプリの中に入れる(app_paths.py の BUNDLE_DIR)
#   本体のビルド(build_mac.sh)の build_res とは別のフォルダを使う(同時にビルドしても混ざらないように)
STAGE="build_res_cftool"
rm -rf "$STAGE"
mkdir -p "$STAGE/etc"
cp ../etc/*.ch ../etc/*.tbl ../etc/logger_cal.txt "$STAGE/etc/"
OPTS+=(--add-data "$STAGE/etc:etc")
"$PYTHON" -m PyInstaller "${OPTS[@]}" "$NAME.py"

# .app の情報(Finder の「情報を見る」に出るバージョンと名前)
if [ "$MODE" = app ] && [ -n "$VERSION" ]; then
  PLIST="dist/$NAME.app/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST"
  # Info.plist を変えたので、署名し直す(しないと「壊れている」と言われて開けないことがある)
  codesign --force --deep --sign - "dist/$NAME.app" >/dev/null 2>&1 || echo "注意: 署名し直せませんでした"
fi

# --- 配布用フォルダを作る ---
RELEASE="dist/$NAME-mac"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
if [ "$MODE" = app ]; then
  ditto "dist/$NAME.app" "$RELEASE/$NAME.app"   # .app の中身(リンクや属性)を崩さずにコピー
else
  ditto "dist/$NAME" "$RELEASE"                 # 実行ファイルと _internal フォルダ
fi

# --- zip にまとめる(ditto は .app のリンクや属性を保ったまま圧縮できる) ---
rm -f "$ROOT/$NAME-mac.zip"
ditto -c -k --keepParent "$RELEASE" "$ROOT/$NAME-mac.zip"

echo ""
echo "完了しました。"
echo "  フォルダ: scripts/$RELEASE"
echo "  zip     : $NAME-mac.zip"
if [ "$MODE" = app ]; then
  echo "  起動    : open scripts/$RELEASE/$NAME.app"
else
  echo "  起動    : scripts/$RELEASE/$NAME"
fi
