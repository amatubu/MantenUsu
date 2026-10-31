#!/bin/bash
# build_mac.sh
# Mac 版のビルド(PyInstaller)。build_win.bat の Mac 版
#
# 使い方(obspy などを入れた Python 環境を有効にしてから実行する。例: conda activate obspy_env):
#   ./build_mac.sh        ... フォルダ形式。ターミナルから起動する(ログも見える)
#   ./build_mac.sh --app  ... .app 形式。Finder からダブルクリックで起動する
#   Python は、有効にした環境の python3 → プロジェクトの隣の venv → プロジェクト内の venv / .venv
#   の順に探し、必要なライブラリが入っている最初のものを使う
#   使う Python を指定するとき: PYTHON=/path/to/python3 ./build_mac.sh
#
# できるもの:
#   scripts/dist/UsuMantenViewer-mac/  ... アプリと、その横に etc・tools・service_account・output
#                                          (アプリはこの横のフォルダを使う。app_paths.py を参照)
#   UsuMantenViewer-mac.zip            ... 上のフォルダを zip にしたもの(配布用)
#   ※ Windows 版と同じく service_account(Googleドライブの鍵)も含むので、zip の扱いに注意
set -euo pipefail

NAME=UsuMantenViewer
MODE=folder
if [ "${1:-}" = "--app" ]; then
  MODE=app
fi

# プロジェクトのフォルダ(このスクリプトのある場所)に移動
cd "$(dirname "$0")"
ROOT="$(pwd)"

# --- 使う Python を決める(必要なライブラリが入っているもの) ---
has_libs() {
  "$1" -c "import PyInstaller, obspy, matplotlib, googleapiclient, google.oauth2" 2>/dev/null
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
  echo "エラー: PyInstaller・obspy・matplotlib・Googleのライブラリが入った Python が見つかりません。"
  echo "       探した場所: ${CANDIDATES[*]}"
  echo "       それらを入れた環境を有効にするか、PYTHON=/path/to/python3 で指定してください。"
  exit 1
fi
PYTHON="$FOUND"
echo "使用する Python: $("$PYTHON" -c 'import sys, tkinter; print(sys.executable, sys.version.split()[0], "/ Tk", tkinter.TkVersion)')"

# --- ビルド ---
cd scripts
# --debug noarchive: モジュールを1つの書庫にまとめず、ファイルのまま置く(build_win.bat と同じ)
#   obspy はカラーマップのデータを「自分のソースファイルの場所」から探すが、書庫の中では
#   その場所が相対パスになって見つからず、起動時に KeyError: 'viridis' で止まるため
OPTS=(--onedir --noconfirm --name "$NAME" --debug noarchive
      --collect-all obspy --collect-all matplotlib
      --collect-all google.oauth2 --collect-all googleapiclient)
if [ "$MODE" = app ]; then
  OPTS+=(--windowed)   # .app を作る(ターミナルを開かずに起動する)
fi
"$PYTHON" -m PyInstaller "${OPTS[@]}" "$NAME.py"

# --- 配布用フォルダを作る(アプリの横に etc などを置く) ---
RELEASE="dist/$NAME-mac"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
if [ "$MODE" = app ]; then
  ditto "dist/$NAME.app" "$RELEASE/$NAME.app"   # .app の中身(リンクや属性)を崩さずにコピー
else
  ditto "dist/$NAME" "$RELEASE"                 # 実行ファイルと _internal フォルダ
fi
ditto ../etc "$RELEASE/etc"
ditto ../service_account "$RELEASE/service_account"
ditto ../tools "$RELEASE/tools"
mkdir -p "$RELEASE/output"
if [ -f ../output/history.json ]; then
  cp ../output/history.json "$RELEASE/output/"
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
