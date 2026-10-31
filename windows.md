# Windows環境（Obspy / Matplotlib）構築ガイド

本ドキュメントは、Windows環境（Windows on ARM含む）において Python の地震波解析ライブラリ `obspy` および `matplotlib` の実行環境を構築し、開発・配布向けに整備する手順を記録したものです。

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
conda create -n obspy_env python obspy matplotlib -y

# 3. 作成した環境の有効化確認
conda activate obspy_env

# 4. pyinstaller の導入(ビルドも行いたい場合のみ)
pip install pyinstaller

# 5. obspy、matplotlib のアップデート(必要な場合)
conda update obspy matplotlib
または
conda update --all

## 3. ワンクリック起動バッチファイル（`.bat`）の作成

毎回の環境有効化の手間を削減し、普段使うシェル（PowerShell等）の起動速度に影響を与えないため、専用の起動バッチを作成します。

### 作成手順

1. テキストエディタを開き、以下の内容を入力します。

```bat
@echo off
rem Minicondaの初期化スクリプトを呼び出して環境を有効化
call "%USERPROFILE%\miniconda3\condabin\conda.bat" activate obspy_env

rem 実行後にプロンプトウィンドウを開いたまま保持する
cmd /k
```

2. ファイル名を `start_env.bat`（例）とし、プロジェクトのルートフォルダまたは作業しやすい場所に保存します。
3. 今後はこの `.bat` ファイルをダブルクリックするだけで、`(obspy_env)` が有効化されたコマンドプロンプトが即座に立ち上がります。

---

## 4. アプリケーションのビルド（PyInstaller `--onedir`）

作成したスクリプトを配布用に `.exe` 化する手順です。

### フォルダ構成（推奨）

```text
📦 プロジェクトルート/
 ┣ 📁 scripts/
     ┣ 📜 UsuMantenViewer.py  # メインのスクリプト
 ┣ 📁 etc/                 # 設定ファイル等
 ┣ 📁 daily_zips/          # 入力データ等
 ┣ 📁 tools/               # ツール類
 ┗ 📁 output/              # 成果物出力先
```

### ビルド実行手順

1. `start_env.bat` から環境を立ち上げます。
2. `scripts` フォルダに移動してビルドを実行します。

```bash
cd scripts
pyinstaller --onedir --collect-all obspy --collect-all matplotlib UsuMantenViewer.py
```

3. ビルド完了後、`scripts/dist/UsuMantenViewer/` フォルダ内に `UsuMantenViewer.exe` および依存ファイル群が生成されます。
4. 配布時は `--onedir` の成果物フォルダ内に `etc` や `daily_zips` などの関連フォルダを同階層に配置して配布します。