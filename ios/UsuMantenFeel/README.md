# 満点で感じる(UsuMantenFeel)

満点の地震の揺れを、iPhone の振動(Core Haptics)で感じるための、テスト用のアプリです(TODO 58)。
自分の iPhone に入れて試すだけで、配りません。

- 教材の HTML を中で開きます(WKWebView)。波形のページ(`scripts/quake_wave_view.py`)の「▶ 振動」を押すと、
  ページが揺れの強さの並び(20 ms ごと、0〜1)を `webkit.messageHandlers.usuHaptics` で送り、アプリが振動させます。
- ビルドのときに、`batch_plots/quakes/locate` の `usu_quakes_waves.html`・`usu_quakes_3d.html` と、`batch_plots/quiz` の `compare.html`(揺れを比べる)・`world.html`(世界の地震が届く)をアプリに入れます(「アプリに入れたページ」)。比べるページは「♪の段を振動で感じる」、世界の地震は設定の「振動」で振動します。
  ページを作り直したら、Xcode でもう一度ビルドして入れ直します。
- ほかの HTML は、右上の「読み込む」(ファイル App から)か、Mac の Finder で iPhone を選び「ファイル」のこのアプリに入れます(「読み込んだページ」)。
- 振動できない端末(シミュレーターなど)では、ページは画面の揺れで代わりにします。

## iPhone に入れる(無料の Apple ID)

1. Xcode で `ios/UsuMantenFeel/UsuMantenFeel.xcodeproj` を開く。
2. Xcode の「設定」→「Apple Accounts」で、自分の Apple ID を足す(初めてのときだけ)。
3. プロジェクトの「UsuMantenFeel」のターゲット →「Signing & Capabilities」で、Team に自分の名前(Personal Team)を選ぶ。
   Bundle Identifier(`jp.usumanten.UsuMantenFeel`)が使えないと出たら、末尾に文字を足して変える。
4. iPhone をケーブルでつなぎ、上の実行先で iPhone を選んで、実行(⌘R)。
   - 初めてのときは、iPhone の「設定」→「プライバシーとセキュリティ」→「デベロッパモード」をオンにして再起動する。
   - 「信頼されていないデベロッパ」と出たら、「設定」→「一般」→「VPN とデバイス管理」で自分の Apple ID を信頼する。
5. 無料の Apple ID では 7 日で開かなくなるので、そのときはもう一度 4 をする。

## コマンドで入れる(2 回目から)

Xcode で一度 Team を選んで入れたあとは、MantenUsu のフォルダで次を実行すると、ビルド → iPhone に入れる → 開く、までをします
(教材のページを作り直したあとや、7 日の期限が切れたとき。期限が切れたプロファイルは自動で作り直す)。

```bash
./install_iphone.sh
```

- つながっている(ケーブルか、Xcode でペアリングした Wi-Fi の)実機の iPhone のうち、最初のものに入れる。端末を選ぶときは UDID を付ける(`xcrun devicectl list devices` で分かる)。
- 中身は `ios/UsuMantenFeel/install.sh`(xcodebuild でビルドし、`xcrun devicectl` で入れて開く)。ビルドしたものは `ios/UsuMantenFeel/build/` に置く(Git には入れない)。
- iPhone の画面がロックされていると、入れたあと開けないことがある。そのときはホーム画面から開く。

## 確かめること

- 波形のページで地震を選び、「▶ 振動」。P波で弱く、S波で強く振動し、だんだん弱くなるか。
- 強さの段階(0.3・0.5・1 くらい)の違いが分かるか。×½ にすると P と S の間が分かりやすくなるか。
- 「設定」→「サウンドと触覚」→「システムの触覚」がオフだと振動しないことがある。
- Mac の Safari の「開発」メニューから、アプリの中のページを調べられる(`isInspectable`)。
