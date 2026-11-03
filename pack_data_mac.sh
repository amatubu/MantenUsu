#!/bin/bash
# pack_data_mac.sh
# 別のマシンに移すデータ(手で直した到着時刻・設定・RMS・震源リストなど)を1つの zip にまとめる(Mac)
# 中身と使い方は scripts/pack_data.py を参照。アプリ本体のビルド(build_mac.sh)とは別に使う
#
# 使い方:
#   ./pack_data_mac.sh               ... UsuMantenViewer-data_YYYYMMDD.zip を作る
#   ./pack_data_mac.sh --private     ... 再配布できないもの(時刻表・v-net の ZIP)も入れる(自分用。人に渡さない)
#   ./pack_data_mac.sh --with-zips   ... daily_zips(とても大きい)も入れる
#   Python は標準のライブラリだけを使う。有効にした環境の python3 → プロジェクトの隣の venv → プロジェクト内の venv / .venv
#   の順に探し、動く最初のものを使う(build_mac.sh と同じ順)。指定するとき: PYTHON=/path/to/python3 ./pack_data_mac.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ -n "${PYTHON:-}" ]; then
  CANDIDATES=("$PYTHON")
else
  CANDIDATES=(python3 ../venv/bin/python3 venv/bin/python3 .venv/bin/python3)
fi
for py in "${CANDIDATES[@]}"; do
  if "$py" -c "" 2>/dev/null; then
    exec "$py" scripts/pack_data.py "$@"
  fi
done
echo "エラー: 動く Python が見つかりません(探した場所: ${CANDIDATES[*]})"
exit 1
