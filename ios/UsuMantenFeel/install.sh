#!/bin/bash
# install.sh
# 「満点で感じる」(UsuMantenFeel)をビルドして、つないだ iPhone に入れ、開く(TODO 58)
#   ビルドのときに batch_plots の教材のページ(波形・3D の図・揺れを比べる・世界の地震)をアプリに入れるので、
#   ページを作り直したあとや、7 日の期限が切れたときに実行する
#
#     ios/UsuMantenFeel/install.sh [端末の UDID]
#   UDID を省くと、つながっている(ケーブルか、ペアリングした Wi-Fi の)実機の iPhone のうち最初のものに入れる
#   署名は Xcode と同じ自動の署名(Signing & Capabilities で選んだ Team)。期限が切れたプロファイルは作り直す
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"

# 入れる端末
UDID="${1:-}"
if [ -z "$UDID" ]; then
  JSON="$(mktemp -t usumanten_devices)"
  trap 'rm -f "$JSON"' EXIT
  xcrun devicectl list devices --json-output "$JSON" >/dev/null
  UDID="$(/usr/bin/python3 - "$JSON" <<'PY'
import json, sys
devices = json.load(open(sys.argv[1]))["result"]["devices"]
for d in devices:
  hw, conn = d.get("hardwareProperties", {}), d.get("connectionProperties", {})
  if hw.get("platform") == "iOS" and hw.get("reality") == "physical" and conn.get("tunnelState") != "unavailable":
    print(hw["udid"], d.get("deviceProperties", {}).get("name", ""), sep="\t")
    break
PY
)"
  if [ -z "$UDID" ]; then
    echo "つながっている iPhone が見つかりません(ケーブルでつなぐか、Xcode で Wi-Fi のペアリングをしてください)" >&2
    exit 1
  fi
  NAME="${UDID#*	}"
  UDID="${UDID%%	*}"
  echo "入れる端末: $NAME ($UDID)"
fi

echo "== ビルド"
xcodebuild -project "$HERE/UsuMantenFeel.xcodeproj" -scheme UsuMantenFeel -configuration Debug \
  -destination "id=$UDID" -derivedDataPath "$BUILD" -allowProvisioningUpdates -quiet build \
  2> >(grep -v "IDERunDestination: Supported platforms" >&2)   # 害の無い注意(スキームの実行先の表示)は出さない

APP="$BUILD/Build/Products/Debug-iphoneos/UsuMantenFeel.app"
BUNDLE_ID="$(/usr/libexec/PlistBuddy -c "Print CFBundleIdentifier" "$APP/Info.plist")"
echo "アプリに入れたページ: $(ls "$APP/Pages" 2>/dev/null | tr '\n' ' ')"

echo "== iPhone に入れる"
xcrun devicectl device install app --device "$UDID" "$APP" >/dev/null

echo "== 開く"
xcrun devicectl device process launch --device "$UDID" "$BUNDLE_ID" >/dev/null || \
  echo "開けませんでした(iPhone の画面がロックされているときなど)。ホーム画面の「満点で感じる」から開いてください" >&2

echo "できました"
