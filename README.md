# 有珠満点地震計 波形ビューワ（UsuMantenViewer）

有珠山周辺に設置した満点地震計の観測データ（WIN形式）を表示・解析するアプリです。日ごとのZIPファイルから必要な期間のデータを取り出し、SAC形式に変換して、波形の表示や震度の推定、P波・S波の到着時刻の検出を行います。

- バージョン: 2.5.0
- 動作環境: Windows 10 / 11、Mac（Apple Silicon）
- 使用ライブラリ: ObsPy、Matplotlib、Tkinter、Google API（Googleドライブからのダウンロード）

---

## 主な機能

### 指定期間の波形表示
- 開始・終了日時（`YYYYMMDDHHmm`）を指定して、全拠点・全成分（U/N/E）の波形を表示します。縦軸は速度（cm/s）です。
- 4拠点のときは拠点ごとの2x2表示になり、ボタンで縦に並べる表示と切り替えられます。
- 表示した期間は履歴に残り、あとから呼び出せます。

### 震度推定
- 選んだ拠点の3成分から、気象庁の方法で計測震度と震度階級を計算します。
- 計算の途中経過（加速度波形、フィルター後の波形、3成分合成加速度など）もグラフで表示します。

### P波・S波検出
- STA/LTA 法で P波・S波の到着時刻を検出し、S-P時間と大森公式から震源までの距離を推定します。
- スライダーとプリセットでパラメータを調整でき、設定は次回に引き継がれます。

### 震源推定
- 4拠点の波形があるとき、上下動から P波の到着時刻を検出し、震源の位置（緯度・経度・深さ）と発生時刻を推定します。
- 拠点の間隔が数kmしかないため、遠い地震は P波だけでは距離が決まりません。その場合は到来方向と見かけの速度を表示します。
- 到着時刻は波形のクリックで手直しでき（Shift+クリックでその拠点だけ自動検出に戻す）、右クリックでその拠点を計算から外せます。波形は 2-20Hz のフィルターをかけて表示します（切り替え可）。
- 拠点の配置図の背景に、地理院タイル（標準地図・色別標高図）を表示します（インターネット接続が必要。取得したタイルはユーザーのキャッシュフォルダ `UsuMantenViewer/map_tiles` に保存し、2回目からはそれを使います）。拠点の位置は `etc/manten_ch.tbl` から読み込みます。

### 1時間スタック波形
- 1時間分の波形を1分ごとに60段に並べて表示します。
- 前後の1時間への移動、拠点・成分の切り替え、ホイールでのズーム、ドラッグでの横移動ができます。
- 波形を右クリックすると、その分から 1〜30分間の全拠点の波形表示（指定期間の波形表示）を開けます。

### データの取得とキャッシュ
- 手元の `daily_zips` に無い日付のZIPファイルは、Googleドライブから自動でダウンロードします（中止もできます）。
- 取り出したWINファイルや変換したSACファイルは `output` にキャッシュし、同じ期間は再利用します。
- 「出力データキャッシュ整理」で、結合前のWINファイル・結合後のWINファイル・SACファイルを種類ごとに削除できます。

### バッチ処理
- 画面を出さずに、指定したチャンネル・期間の1時間スタック波形の画像を一括で保存します（`batch_plots`）。

---

## フォルダ構成

```text
📦 MantenUsu/
 ┣ 📁 scripts/              # スクリプト(下の「スクリプト一覧」を参照)
 ┣ 📁 etc/                  # win2sac 用の設定・チャンネル定義ファイル
 ┣ 📁 tools/                # win2sac_32・wchch_32 などの外部ツール(Git の管理外)
 ┣ 📁 service_account/      # Googleドライブの鍵と設定(Git の管理外)
 ┣ 📁 daily_zips/           # 日ごとのZIPファイル(Git の管理外)
 ┣ 📁 output/               # キャッシュ・履歴(Git の管理外)
 ┣ 📁 batch_plots/          # バッチ処理で保存した画像(Git の管理外)
 ┣ 📜 requirements.txt      # 実行に必要なライブラリ
 ┣ 📜 requirements-build.txt # ビルドに必要なライブラリ(PyInstaller を含む)
 ┣ 📜 start_env.bat         # Windows: conda 環境を有効にしたコマンドプロンプトを開く
 ┣ 📜 build_win.bat         # Windows: ビルド
 ┣ 📜 build_mac.sh          # Mac: ビルド
 ┣ 📜 windows.md            # Windows の構築・ビルド手順
 ┗ 📜 mac.md                # Mac の構築・ビルド手順
```

---

## セットアップ

環境の構築手順は OS ごとにまとめています。

- Windows: [windows.md](windows.md)（Miniconda の conda 環境）
- Mac: [mac.md](mac.md)（Homebrew の Python と venv）

Googleドライブからダウンロードする場合は、`service_account` フォルダに鍵と設定ファイルが必要です（[windows.md の「4. Googleドライブの設定」](windows.md#4-googleドライブの設定service_account-フォルダ)）。

---

## 起動

プロジェクトのフォルダで次のコマンドを実行します（Windows は `python scripts\UsuMantenViewer.py`）。

```bash
python3 scripts/UsuMantenViewer.py
```

| オプション | 内容 |
|---|---|
| `-v` / `--verbose` | 画面のログをコンソールにも表示する |
| `--batch` | 画面を出さずに、1時間スタック波形の画像を一括で保存する |
| `-c` / `--channel` | バッチ処理の対象チャンネル（例: `Hokudai.U`） |
| `-s` / `--start-date`、`-e` / `--end-date` | バッチ処理の対象期間（`YYYYMMDD`） |
| `--overwrite` | バッチ処理で、既存の画像を上書きする |

---

## ビルド（配布用アプリの作成）

PyInstaller で、Python が入っていないパソコンでも動くアプリにまとめます。

- Windows: `build_win.bat` → `UsuMantenViewer.zip`
- Mac: `./build_mac.sh`（`.app` 形式は `./build_mac.sh --app`） → `UsuMantenViewer-mac.zip`

ビルドしたアプリは、自分の横にある `etc`・`tools`・`output` などのフォルダを使います。詳しくは [windows.md](windows.md) / [mac.md](mac.md) を参照してください。

---

## スクリプト一覧

### アプリ本体（`scripts/`）

| ファイル | 内容 |
|---|---|
| `UsuMantenViewer.py` | メイン画面、各処理の呼び出し、バッチ処理 |
| `waveform_window.py` | 指定期間の波形表示（縦に並べる表示・2x2表示） |
| `stack_plot.py` | 1時間スタック波形の表示・画像保存 |
| `jma_intensity.py` | 震度推定 |
| `ps_picker.py` | P波・S波検出 |
| `hypocenter.py` / `hypocenter_window.py` | 震源推定の計算と、その画面 |
| `gsi_tiles.py` | 地理院タイルの取得・キャッシュ（震源推定の地図の背景） |
| `win_pipeline.py` | ZIPからのWINファイルの取り出し、結合、SACファイルへの変換 |
| `drive_downloader.py` | Googleドライブからのダウンロード |
| `cache_manager.py` / `cache_dialog.py` | キャッシュの集計・削除と、その画面 |
| `app_paths.py` | データフォルダの場所（開発中とビルドしたアプリで共通）、ビルドしたアプリ向けの設定 |
| `plot_common.py` / `log_common.py` | 描画・ログ出力の共通処理 |

### 単体で使う補助スクリプト（`scripts/`）

| ファイル | 内容 |
|---|---|
| `extract_win.py` | 指定期間のWINファイルをZIPから取り出す |
| `convert_to_sac.py` | フォルダ内のWINファイルを結合してSACファイルに変換する |
| `repackage_to_daily_zip.py` | 元のZIPファイル群を日ごとのZIPファイルにまとめ直す |
| `check_zips.py` | 元のZIPファイルの中身の構成（日付フォルダ・ファイル名の形式）を確認する |
| `show_statistics.py` | `daily_zips` のデータの有無を集計する（pandas・openpyxl が必要） |
| `simulate_size.py` | 日ごと・月ごとのデータ量を集計する |
| `screen_events.py` | 地震らしい期間の一覧から、近い地震の候補を絞り込む（一覧のCSVと確認用の画像を出力） |

---

## 今後の予定（TODO）

`scripts/UsuMantenViewer.py` の先頭に一覧があります。主な未対応の項目:

- WINファイル・SACファイルのキャッシュ処理の最適化
- 変更しないファイル（`tools`・`service_account`・`etc` の設定ファイル）はアプリと一緒に置き、ユーザーの操作で作られる・変わるファイル（`output`・`daily_zips`・`batch_plots`・`etc/ps_picker_params.json`・`etc/hypocenter_params.json`）はユーザープロファイル配下に置く
