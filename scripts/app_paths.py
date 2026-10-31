# app_paths.py
# データフォルダ(etc, tools, output など)の場所を決める
# 開発中もビルドしたアプリでも、データフォルダは「プロジェクトのフォルダ」の直下に置く
# あわせて、PyInstaller でビルドしたアプリだけに必要な調整もここで行う
import os
import sys
from pathlib import Path

# PyInstaller でビルドしたアプリとして動いているか
FROZEN = getattr(sys, "frozen", False)


# プロジェクトのフォルダ(etc や tools などがあるフォルダ)を返す
#   開発中(.py を実行)            ... scripts フォルダの1つ上
#   PyInstaller でビルドしたアプリ ... 実行ファイルのあるフォルダ
#                                    (Mac の .app なら、.app のあるフォルダ)
def _find_project_dir():
  if FROZEN:
    exe = Path(sys.executable).resolve()
    # Mac の .app の場合、実行ファイルは <フォルダ>/UsuMantenViewer.app/Contents/MacOS/ にある
    for parent in exe.parents:
      if parent.suffix == ".app":
        return parent.parent
    return exe.parent
  return Path(__file__).resolve().parent.parent


PROJECT_DIR = _find_project_dir()

ETC_DIR = PROJECT_DIR / "etc"                          # win.prm やチャンネル定義ファイルなどの設定
TOOLS_DIR = PROJECT_DIR / "tools"                      # win2sac_32 などの外部ツール
OUTPUT_DIR = PROJECT_DIR / "output"                    # WINファイル・SACファイルなどの出力先(キャッシュ)
ZIPS_DIR = PROJECT_DIR / "daily_zips"                  # 日ごとのZIPファイル
SERVICE_ACCOUNT_DIR = PROJECT_DIR / "service_account"  # Googleドライブのサービスアカウントと設定
BATCH_PLOTS_DIR = PROJECT_DIR / "batch_plots"          # バッチ処理で保存する画像


# ビルドしたアプリで、Matplotlib のフォント一覧のキャッシュを残る場所に置く
# PyInstaller は Matplotlib の設定フォルダを、終了時に消える一時フォルダにする。そのため毎回
# フォント一覧を作り直すことになり、Mac では system_profiler でのフォント検索に約20秒かかって
# 起動が遅くなる。ユーザーのキャッシュフォルダに置けば、作るのは最初の1回だけで済む
# (matplotlib を読み込む前に設定する必要があるので、このモジュールの読み込み時に行う)
def _use_persistent_matplotlib_cache():
  if sys.platform == "darwin":
    base = Path.home() / "Library" / "Caches"
  elif sys.platform.startswith("win"):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
  else:
    base = Path.home() / ".cache"
  cache_dir = base / "UsuMantenViewer" / "matplotlib"
  try:
    cache_dir.mkdir(parents=True, exist_ok=True)
  except OSError:
    return   # 作れなければ PyInstaller の一時フォルダのまま(遅いが動く)
  os.environ["MPLCONFIGDIR"] = str(cache_dir)

if FROZEN:
  _use_persistent_matplotlib_cache()
