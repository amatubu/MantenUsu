# Mac環境 構築・ビルドガイド

本ドキュメントは、Mac において有珠満点地震計 波形ビューワ（`UsuMantenViewer.py`）の実行環境を構築し、PyInstaller で配布用のアプリにまとめる手順を記録したものです。Windows の手順は `windows.md` を参照してください。

---

## 概要・前提条件

- **ターゲット**: Apple Silicon（M1 以降、arm64）の Mac
- **Python**: Homebrew の Python（動作確認: Python 3.14 / Tk 9.1）と、その venv（仮想環境）
- **外部ツール**: `tools` フォルダに、防災科学技術研究所の `win2sac_32`（WINファイルをSACファイルに変換）と `wchch_32`（WINファイル内のチャンネル番号の読み替え）が必要です。リポジトリには含まれていないため、防災科研の「[連続波形ダウンロード](https://www.vnet.bosai.go.jp/)」でユーザ登録を行い、ソースコードをダウンロードしてビルドしたものを置きます（README の「外部ツール」を参照）。
- **補足**: `tools` フォルダの `win2sac_32` などは arm64 用にビルドしたものを使っているため、Intel の Mac では動きません（Intel の Mac で使う場合は、その Mac でビルドし直してください）。

---

## 1. Homebrew と Python のインストール

[Homebrew](https://brew.sh/) を入れたあと、ターミナルで Python と tkinter（画面表示用）を入れます。Homebrew の Python には tkinter が含まれないため、`python-tk` も必要です（Python と同じバージョンを指定します）。

```bash
brew install python@3.14 python-tk@3.14
```

---

## 2. 仮想環境（venv）の作成とパッケージ導入

プロジェクトのフォルダ（`MantenUsu`）の**隣**に `venv` を作ります。`build_mac.sh` はこの場所を自動で探します（プロジェクト内の `venv` / `.venv` でも可）。

```bash
cd MantenUsu
/opt/homebrew/bin/python3.14 -m venv ../venv
source ../venv/bin/activate
python3 -m pip install -r requirements-build.txt
```

- `requirements-build.txt` は、アプリに必要なライブラリ（`requirements.txt`）と PyInstaller をまとめて入れます。ビルドしない場合は `requirements.txt` だけで構いません。
- 開発用スクリプト `scripts/show_statistics.py` を使う場合のみ、`python3 -m pip install pandas openpyxl` も実行します。

### インストールの確認

次のコマンドで `OK` と Tk のバージョンが表示されれば、必要なものは揃っています。

```bash
python3 -c "import tkinter, obspy, matplotlib, googleapiclient, google.oauth2; print('OK / Tk', tkinter.TkVersion)"
```

- `No module named '_tkinter'` と出る場合は、1. の `python-tk` が入っていないか、Python とバージョンが合っていません。
- 以降、新しいターミナルで作業するときは `source ../venv/bin/activate` で venv を有効にします。

---

## 3. データ・設定フォルダの準備

プロジェクトのフォルダの直下に、次のフォルダを用意します（`etc` 以外は Git の管理外です）。

```text
📦 MantenUsu/
 ┣ 📁 scripts/            # スクリプト
 ┣ 📁 etc/                # 設定ファイル等(Git で管理)
 ┣ 📁 tools/              # win2sac_32・wchch_32 などの外部ツール(Mac 用の arm64 版)
 ┣ 📁 service_account/    # Googleドライブの設定(windows.md の 4. を参照)
 ┣ 📁 daily_zips/         # 日ごとのZIPファイル(無ければ自動で作られ、Googleドライブから取得)
 ┣ 📁 output/             # 出力先(キャッシュ・履歴。自動で作られる)
 ┗ 📜 build_mac.sh
```

- `tools` には、防災科研の「連続波形ダウンロード」から入手したソースコードをビルドしてできた `win2sac_32`・`wchch_32` を置きます（ソースに含まれる `catwin32`・`wck_32` などは使いません）。ビルドの手順は「7. 外部ツールのビルド」を参照してください。
- `tools` の中のファイルには実行権限が必要です。動かない場合は `chmod +x tools/*` を実行してください。
- `service_account` の中身（鍵と `folder_config.json`）は Windows と同じです。`windows.md` の「4. Googleドライブの設定」を参照してください。

---

## 4. アプリケーションの起動

venv を有効にした状態で、プロジェクトのフォルダで次のコマンドを実行します。

```bash
python3 scripts/UsuMantenViewer.py
```

- オプション（`-v`、`--batch` など）は Windows と同じです。`windows.md` の「5. アプリケーションの起動」を参照してください。
- 1時間スタック画面での右クリックは、トラックパッドでは2本指クリック（または control＋クリック）です。

---

## 5. アプリケーションのビルド（PyInstaller）

手順は `build_mac.sh`（プロジェクトのフォルダ）にまとめてあります。venv を有効にしていなくても、上記の場所にある venv を自動で探して使います。

```bash
./build_mac.sh
```

または、`.app` 形式（Finder からダブルクリックで起動）にする場合:

```bash
./build_mac.sh --app
```

| 形式 | 起動のしかた | 向いている用途 |
|---|---|---|
| フォルダ形式（既定） | ターミナルで `scripts/dist/UsuMantenViewer-mac/UsuMantenViewer` を実行 | 動作確認（ログやエラーがターミナルに出る） |
| `.app` 形式（`--app`） | Finder で `UsuMantenViewer.app` をダブルクリック | 配布・普段使い |

`build_mac.sh` は次の処理を行います（約3分）。

- 必要なライブラリが入った Python を探す（有効にした環境 → `../venv` → `venv` → `.venv`）
- PyInstaller でアプリを作る
  - `--collect-all` で obspy・matplotlib・Googleドライブ用のライブラリのデータも含める
  - `--debug noarchive` でモジュールを書庫にまとめずにファイルのまま置く。obspy はカラーマップのデータを自分のソースファイルの場所から探すため、これが無いと起動時に `KeyError: 'viridis'` で止まる
- `etc` の設定ファイル（Git で管理しているもの）と `tools` の外部ツールを、アプリの中に入れる
- `scripts/dist/UsuMantenViewer-mac/` に、アプリと `service_account`、操作マニュアルの PDF（`UsuMantenViewer_manual.pdf`）を並べる（マニュアルは `scripts/build_manual.py` で作る。`markdown` パッケージと Chrome か Edge が必要で、無ければ入れずに続ける）
- そのフォルダをプロジェクトのフォルダの `UsuMantenViewer-mac.zip` にまとめる（`.app` の中身を崩さないよう `ditto` を使う）

ビルドの生成物（`scripts/build`、`scripts/dist`、`*.spec`、zip）は Git の管理外です。

---

## 6. 配布・実行時の注意

- 別のマシンに移すときは、手で直した到着時刻・RMS・震源リストなど Git の管理外のデータも移します。まとめて zip にする `./pack_data_mac.sh` と、コピーするフォルダの一覧は、README の「別のマシンに移すとき」を参照してください。

- **フォルダの構成**: 外部ツール（`tools`）と設定ファイルの初期値（`etc`）は、アプリの中に入っています。ユーザーの設定（画面の設定・履歴・地震リスト・手で直した到着時刻・Googleドライブの鍵など）は `~/Library/Application Support/UsuMantenViewer`、大きいデータ（`daily_zips`・キャッシュなど）はデータフォルダ（初期値は `~/Library/Application Support/UsuMantenViewer/data`）に置きます（`scripts/app_paths.py`）。アプリの横に `output` か `daily_zips` があるときは（前の版の構成）、そこをデータフォルダとして使います。データフォルダの場所は、メンテナンス画面の「置き場所」で確かめ・変えられます。
- **Googleドライブの鍵**: 初回は、アプリの横の `service_account` を使います。メンテナンス画面の「置き場所」の「Googleドライブの鍵ファイルを取り込む…」でユーザーの設定にコピーしておくと、アプリをどこに置いても使えます。
- **最初の起動は遅い**: ビルド直後や配布先での最初の1回は、Matplotlib のフォント一覧の作成と macOS による新しいファイルのチェックで、数十秒〜1分半ほどかかります。2回目以降は数秒で起動します（フォント一覧は `~/Library/Caches/UsuMantenViewer/matplotlib` に保存されます）。
- **ダウンロードした zip から使う場合**: 署名していないアプリと `tools` の外部ツールを macOS が止めることがあります。展開したフォルダで次のコマンドを実行して、隔離情報を外してください。

  ```bash
  xattr -dr com.apple.quarantine UsuMantenViewer-mac
  ```

  隔離情報が付いたまま `.app` を開くと、macOS が `.app` を別の場所に移して実行することがあり（App Translocation）、その場合は横の `etc` などが見つからずに正しく動きません。
- **鍵の扱い**: zip には `service_account`（Googleドライブの鍵）が含まれるため、扱いに注意してください。
- **ログ**: `.app` 形式ではターミナルが開かないため、ログは画面のログ欄で確認します。ターミナルでログを見たい場合は、フォルダ形式でビルドするか、`UsuMantenViewer.app/Contents/MacOS/UsuMantenViewer -v` を直接実行します。
- **起動時のメッセージ**: ターミナルから起動すると `error messaging the mach port for IMKCFRunLoopWakeUpReliable` と表示されることがありますが、macOS の文字入力の仕組みが出す記録で、動作には影響しません。

---

## 7. 外部ツールのビルド（`win2sac_32`・`wchch_32`）

防災科研の「連続波形ダウンロード」から入手したソースコード（`win32tools`）を、Xcode のコマンドラインツール（clang。`gcc` で呼べます）でビルドします。入っていなければ `xcode-select --install` で入れます。ソースには手を入れず、オプション `-std=gnu17 -fcommon` を付けます（理由は README の「外部ツール」を参照）。

```bash
cd win32tools/win2sac.src
make clean
make CFLAGS="-I. -I$(xcrun --show-sdk-path)/usr/include/malloc -Wall -O3 -std=gnu17 -fcommon"
cd ../wchch.src
make clean
make CFLAGS="-I. -Wall -O3 -std=gnu17 -fcommon"
cd ..
cp win2sac.src/win2sac_32 wchch.src/wchch_32 <プロジェクトのフォルダ>/tools/
chmod +x <プロジェクトのフォルダ>/tools/*
```

- win2sac は `malloc.h` を読みますが、macOS では `malloc/malloc.h` にあるため、`-I$(xcrun --show-sdk-path)/usr/include/malloc` を足します。
- 警告（warning）がたくさん出ますが、古い書き方によるもので、エラーで止まらなければ問題ありません。
- できるのは、ビルドした Mac の種類（Apple Silicon なら arm64）用のファイルです。
