# Windows環境（Obspy / Matplotlib）構築ガイド

本ドキュメントは、Windows環境（Windows on ARM含む）において、有珠満点地震計 波形ビューワ（`UsuMantenViewer.py`）の実行環境を構築し、開発・配布向けに整備する手順を記録したものです。

---

## 概要・前提条件

- **ターゲット OS**: Windows 10 / 11 (x86_64 および win-arm64)
- **補足 (ARM機での運用について)**:  
  `win-arm64` ネイティブ版では C/Fortran 依存ライブラリのバイナリが不足し `PackagesNotFoundInChannelsError` が発生するため、本手順では **x86_64版（64-bit / Intel/AMD用）Miniconda** のエミュレーション実行を採用しています。
- **外部ツール**: `tools` フォルダに、防災科学技術研究所の `win2sac_32.exe`（WINファイルをSACファイルに変換）と `wchch_32.exe`（WINファイル内のチャンネル番号の読み替え）が必要です。リポジトリには含まれていないため、防災科研の「[連続波形ダウンロード](https://www.vnet.bosai.go.jp/)」でユーザ登録を行い、ソースコードをダウンロードして Windows 用にビルドしたものを置きます（「7. 外部ツールのビルド」を参照）。

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

# 4. pyinstaller の導入(ビルドも行いたい場合のみ。markdown は操作マニュアルの PDF を作るため)
pip install pyinstaller markdown
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
 ┣ 📁 tools/                  # win2sac_32.exe・wchch_32.exe(防災科研のソースからビルド。Git の管理外)
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
- `etc` の設定ファイル（Git で管理しているもの）と `tools` の外部ツールを、アプリの中に入れる
- 同じフォルダに `service_account` をコピーし、操作マニュアルの PDF（`UsuMantenViewer_manual.pdf`）を作って入れる（`scripts/build_manual.py`。`markdown` パッケージと Edge が必要で、作れなければ入れずに続ける）
- フォルダ全体をプロジェクトのルートフォルダの `UsuMantenViewer.zip` にまとめる

### 配布・実行時の注意

- 別のマシンに移すときは、手で直した到着時刻・RMS・震源リストなど Git の管理外のデータも移します。まとめて zip にする `pack_data_win.bat` と、コピーするフォルダの一覧は、README の「別のマシンに移すとき」を参照してください。

- 外部ツール（`tools`）と設定ファイルの初期値（`etc`）は、アプリの中（`_internal`）に入っています。ユーザーの設定（画面の設定・履歴・地震リスト・手で直した到着時刻・Googleドライブの鍵など）は `%APPDATA%\UsuMantenViewer`、大きいデータ（`daily_zips`・キャッシュなど）はデータフォルダ（初期値は `%LOCALAPPDATA%\UsuMantenViewer\data`）に置きます（`scripts/app_paths.py`）。`UsuMantenViewer.exe` の横に `output` か `daily_zips` があるときは（前の版の構成）、そこをデータフォルダとして使います。データフォルダの場所は、メンテナンス画面の「置き場所」で確かめ・変えられます。
- Googleドライブの鍵は、初回は `UsuMantenViewer.exe` の横の `service_account` を使います。メンテナンス画面の「置き場所」の「Googleドライブの鍵ファイルを取り込む…」でユーザーの設定にコピーしておくと、アプリをどこに置いても使えます。
- `daily_zips` は配布物に含めていません。必要な日付のZIPファイルは、初回の表示時にGoogleドライブからダウンロードされます。
- ビルド直後や配布先での**最初の起動だけ**、Matplotlib のフォント一覧の作成などで時間がかかります。2回目以降は数秒で起動します（フォント一覧は `%LOCALAPPDATA%\UsuMantenViewer\matplotlib` に保存されます）。
- zip には `service_account`（Googleドライブの鍵）が含まれるため、扱いに注意してください。

---

## 7. 外部ツールのビルド（`win2sac_32`・`wchch_32`）

防災科研の「連続波形ダウンロード」から入手したソースコード（`win32tools`）を、MinGW-w64 の gcc でビルドします。ソースには手を入れず、オプション `-std=gnu17 -fcommon` を付けます（理由は README の「外部ツール」を参照）。

### MinGW-w64 の入手（インストール不要）

1. GitHub の [niXman/mingw-builds-binaries](https://github.com/niXman/mingw-builds-binaries/releases) の Releases から、`x86_64-<版>-release-posix-seh-ucrt-<版>.7z` をダウンロードします（動作確認: `x86_64-15.2.0-release-posix-seh-ucrt-rt_v13-rev0.7z`、GCC 15.2.0）。
2. 7-Zip などで展開し、できた `mingw64` フォルダを好きな場所（例: `C:\mingw64`）に置きます。インストールは不要です。
3. ビルドするコマンドプロンプトで、`mingw64\bin` をパスに加えます。

```bash
set PATH=C:\mingw64\bin;%PATH%
gcc --version
```

- `make` は MinGW に入っている `mingw32-make` を使います。UnxUtils などの Unix のコマンドは要りません（ソース全体の Makefile は使わず、ツールごとの `*.pub.mingw.mk` を使うため）。

### ビルド

```bash
cd win32tools\win2sac.src
del *.o
mingw32-make -f win2sac.pub.mingw.mk CFLAGS="-I. -DMINGW -Wall -O3 -std=gnu17 -fcommon"
cd ..\wchch.src
del *.o
mingw32-make -f wchch.pub.mingw.mk CFLAGS="-I. -DMINGW -Wall -O3 -std=gnu17 -fcommon"
```

できた `win2sac.src\win2sac_32.exe`・`wchch.src\wchch_32.exe` を、プロジェクトの `tools` フォルダにコピーします。

- 警告（warning）がたくさん出ますが、古い書き方によるもので、エラーで止まらなければ問題ありません。
- `-std=gnu17 -fcommon` を付け忘れると、`true` や `ctrlc` の型、`multiple definition` のエラーで止まります。

### できたツールの確かめ方

前のツールと同じ WIN ファイルを変換し、結果を比べます（exe そのもののハッシュ値は、ビルドの環境で変わるため比べません）。

- `win2sac_32`: 同じ WIN ファイルから作った SAC ファイルを `fc /b 前.sac 新.sac` で比べます（「相違点は検出されませんでした」なら同じ）。ハッシュ値で比べるときは `certutil -hashfile <ファイル> SHA256` です。
- `wchch_32`: 読み替えが起きる Mimatsu の 2025/7/27〜9/5 のファイル（例: `daily_zips\250815.zip` の中の `0970355225/250815/0970355225_2508151000.win`）で比べます。

```bash
wchch_32.exe etc\0970355225_conv.tbl < 0970355225_2508151000.win > new.win
```
