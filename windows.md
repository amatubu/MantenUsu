# Windows環境（Obspy / Matplotlib）構築ガイド

本ドキュメントは、Windows環境（Windows on ARM含む）において、有珠満点地震計 波形ビューワ（`UsuMantenViewer.py`）の実行環境を構築し、開発・配布向けに整備する手順を記録したものです。

---

## 概要・前提条件

- **ターゲット OS**: Windows 10 / 11 (x86_64 および win-arm64)
- **補足 (ARM機での運用について)**:  
  `win-arm64` ネイティブ版では C/Fortran 依存ライブラリのバイナリが不足し `PackagesNotFoundInChannelsError` が発生するため、本手順では **x86_64版（64-bit / Intel/AMD用）Miniconda** のエミュレーション実行を採用しています。

---

## 1. Miniconda (x86_64版) のインストール

1. 過去に別アーキテクチャ（ARM版等）のMinicondaを導入している場合は、事前にアンインストールしておきます。
2. [Miniconda公式サイト](https://docs.anaconda.com/miniconda/) から **「Miniconda3 Windows 64-bit (x86_64)」** インストーラーをダウンロードします。
3. インストーラーを実行し、画面の指示に従ってインストールを完了させます（設定は基本的にデフォルトで問題ありません）。

---

## 2. 仮想環境の作成とパッケージ導入

スタートメニューから **「Anaconda Prompt (Miniconda3)」** を起動し、以下のコマンドを順番に実行します。

```bash
# 1. パッケージ取得元として conda-forge チャンネルを追加し、優先度を設定
conda config --add channels conda-forge
conda config --set channel_priority strict

# 2. Python 環境の作成と必要ライブラリの一括インストール
#    google-api-python-client / google-auth は Googleドライブからのダウンロードに使う
conda create -n obspy_env python obspy matplotlib google-api-python-client google-auth -y

# 3. 作成した環境の有効化
conda activate obspy_env

# 4. pyinstaller の導入(ビルドも行いたい場合のみ)
pip install pyinstaller
```

### すでに `obspy_env` を作ってある場合

Googleドライブ用のライブラリが入っていない環境では、次のコマンドで追加します。

```bash
conda activate obspy_env
conda install google-api-python-client google-auth -y
```

### インストールの確認

次のコマンドで `OK` と表示されれば、必要なライブラリは揃っています。

```bash
python -c "import obspy, matplotlib, googleapiclient, google.oauth2; print('OK')"
```

### ライブラリの更新(必要な場合)

```bash
conda update obspy matplotlib google-api-python-client google-auth
```

または、環境内のすべてを更新する場合:

```bash
conda update --all
```

### 補足: 開発用スクリプトで使うライブラリ

- `scripts/show_statistics.py`（データの統計表示）は `pandas` を使います。使う場合のみ `conda install pandas -y` で追加してください。アプリ本体には不要です。

---

## 3. ワンクリック起動バッチファイル（`start_env.bat`）

毎回の環境有効化の手間を削減し、普段使うシェル（PowerShell等）の起動速度に影響を与えないため、専用の起動バッチを使います。リポジトリのルートフォルダに `start_env.bat` として用意してあります。

```bat
@echo off
rem Minicondaの初期化スクリプトを呼び出して環境を有効化
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate obspy_env

rem 実行後にプロンプトウィンドウを開いたまま保持する
cmd /k
```

- この `.bat` ファイルをダブルクリックするだけで、`(obspy_env)` が有効化されたコマンドプロンプトが立ち上がります。
- Miniconda を既定以外の場所にインストールした場合は、`%USERPROFILE%\miniconda3` の部分を実際のインストール先に書き換えてください。

---

## 4. Googleドライブの設定（`service_account` フォルダ）

ローカルの `daily_zips` に無い日付のZIPファイルは、Googleドライブから自動でダウンロードします。そのために、プロジェクトのルートフォルダに `service_account` フォルダを作り、次の2つのファイルを置きます。

```text
📁 service_account/
 ┣ 📜 manten_usu_downloader.json  # Googleドライブのサービスアカウントの鍵(管理者から入手)
 ┗ 📜 folder_config.json          # ZIPファイルを置いているGoogleドライブのフォルダID
```

`folder_config.json` の書式:

```json
{
  "daily_zips_id": "GoogleドライブのフォルダID"
}
```

- これらのファイルは鍵を含むため、Git の管理外（`.gitignore` で除外）です。他の人に渡すときは扱いに注意してください。
- `service_account` が無い場合でも、ローカルの `daily_zips` にあるZIPファイルだけで動作します。

---

## 5. アプリケーションの起動

`start_env.bat` から環境を立ち上げ、プロジェクトのルートフォルダで次のコマンドを実行します。

```bash
python scripts\UsuMantenViewer.py
```

主なオプション:

| オプション | 内容 |
|---|---|
| `-v` / `--verbose` | 画面のログをコンソールにも表示する |
| `--batch` | 画面を出さずに、1時間スタック波形の画像を一括で保存する（`batch_plots` フォルダ） |
| `-c` / `--channel` | バッチ処理の対象チャンネル（例: `Hokudai.U`） |
| `-s` / `--start-date`、`-e` / `--end-date` | バッチ処理の対象期間（`YYYYMMDD`） |
| `--overwrite` | バッチ処理で、既存の画像を上書きする |

例: 2026年8月26日の Hokudai.U をバッチ処理する

```bash
python scripts\UsuMantenViewer.py --batch -c Hokudai.U -s 20260826 -e 20260826
```

---

## 6. アプリケーションのビルド（PyInstaller `--onedir`）

配布用に `.exe` 化する手順です。手順は `build_win.bat`（リポジトリのルートフォルダ）にまとめてあります。

### フォルダ構成

```text
📦 プロジェクトルート/
 ┣ 📁 scripts/
 ┃   ┗ 📜 UsuMantenViewer.py  # メインのスクリプト
 ┣ 📁 etc/                    # 設定ファイル等
 ┣ 📁 daily_zips/             # 入力データ(日ごとのZIPファイル)
 ┣ 📁 tools/                  # win2sac_32 などの外部ツール
 ┣ 📁 service_account/        # Googleドライブの設定(4. を参照)
 ┣ 📁 output/                 # 出力先(キャッシュ・履歴)
 ┣ 📜 start_env.bat
 ┗ 📜 build_win.bat
```

### ビルド実行手順

1. `start_env.bat` から環境を立ち上げます（`pyinstaller` を導入済みであること）。
2. プロジェクトのルートフォルダで `build_win.bat` を実行します。

```bash
build_win.bat
```

`build_win.bat` は次の処理を行います。

- PyInstaller で `scripts/dist/UsuMantenViewer/` にアプリ(`UsuMantenViewer.exe` と `_internal` フォルダ)を作る
  - `--collect-all` で obspy・matplotlib・Googleドライブ用のライブラリのデータも含める
  - `--debug noarchive` でモジュールを書庫にまとめずにファイルのまま置く。obspy はカラーマップのデータを自分のソースファイルの場所から探すため、これが無いと起動時に `KeyError: 'viridis'` で止まる
- 同じフォルダに `etc`・`service_account`・`tools`・`output\history.json` をコピーする
- フォルダ全体をプロジェクトのルートフォルダの `UsuMantenViewer.zip` にまとめる

### 配布・実行時の注意

- アプリは、`UsuMantenViewer.exe` と同じフォルダにある `etc`・`tools`・`output`・`daily_zips`・`service_account` を使います（`scripts/app_paths.py`）。zip を展開したフォルダの構成は変えないでください。
- `daily_zips` は配布物に含めていません。必要な日付のZIPファイルは、初回の表示時にGoogleドライブからダウンロードされます。
- ビルド直後や配布先での**最初の起動だけ**、Matplotlib のフォント一覧の作成などで時間がかかります。2回目以降は数秒で起動します（フォント一覧は `%LOCALAPPDATA%\UsuMantenViewer\matplotlib` に保存されます）。
- zip には `service_account`（Googleドライブの鍵）が含まれるため、扱いに注意してください。
