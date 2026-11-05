# app_paths.py
# ファイルの置き場所を決める(TODO 12)
#   1. アプリに付く物(変更しない): tools、etc の設定ファイルの初期値(win.prm・*.ch・*.tbl・external_stations.txt)
#        開発中 ... プロジェクトのフォルダ / ビルドしたアプリ ... アプリの中(Mac は .app/Contents の中、Windows は exe の横)
#        (前のビルドのように、アプリの横に tools・etc があればそれを使う)
#   2. ユーザーの設定(小さい・作り直せない): 画面のパラメータ、履歴、手で直した到着時刻(picks)、時刻表、
#        Googleドライブの鍵、etc の上書き、データフォルダの設定
#        Mac ... ~/Library/Application Support/UsuMantenViewer / Windows ... %APPDATA%\UsuMantenViewer
#   3. データフォルダ(大きい・作り直せるか取り直せる): daily_zips、output(キャッシュ)、v-net、jma_hypocenter、batch_plots
#        場所は設定(ユーザーの設定の settings.json)で選ぶ。メンテナンス画面で変えられる
#        初期値 ... Mac は ユーザーの設定/data、Windows は %LOCALAPPDATA%\UsuMantenViewer\data
#        設定がまだ無く、アプリのフォルダ(プロジェクトのフォルダ)に前の output・daily_zips などがあれば、そこを使う
#   4. 消えてもよいキャッシュ: 地図のタイル、走時、Matplotlib のフォント一覧(下の USER_CACHE_DIR)
# あわせて、PyInstaller でビルドしたアプリだけに必要な調整もここで行う
import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "UsuMantenViewer"

# PyInstaller でビルドしたアプリとして動いているか
FROZEN = getattr(sys, "frozen", False)


# プロジェクトのフォルダ(前の版でデータフォルダを置いていた場所)を返す
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


# --- 1. アプリに付く物 ---
#   ビルドしたアプリでは、アプリの中に入れた tools・etc(PyInstaller の sys._MEIPASS)を使う。
#   アプリの中に無く、アプリの横にあれば(前のビルド)、そちらを使う
def _find_bundle_dir():
  if FROZEN:
    inner = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
    if (inner / "etc").exists():
      return inner
  return PROJECT_DIR

BUNDLE_DIR = _find_bundle_dir()
TOOLS_DIR = BUNDLE_DIR / "tools"                       # win2sac_32 などの外部ツール


# --- 2. ユーザーの設定 ---
def _user_dir():
  if sys.platform == "darwin":
    base = Path.home() / "Library" / "Application Support"
  elif sys.platform.startswith("win"):
    base = Path(os.environ.get("APPDATA", Path.home()))
  else:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
  return base / APP_NAME

USER_DIR = _user_dir()
USER_ETC_DIR = USER_DIR / "etc"                        # etc の上書き(同じ名前があればアプリの中のものより優先)
SETTINGS_FILE = USER_DIR / "settings.json"             # データフォルダの場所などの設定
PICKS_DIR = USER_DIR / "picks"                         # 震源推定の画面で手で直した到着時刻
HISTORY_FILE = USER_DIR / "history.json"               # メイン画面の実行履歴
QUAKE_LISTS_FILE = USER_DIR / "quake_lists.json"       # 地震リストの画面の一覧(既知の地震・有珠山付近の地震)
PS_PICKER_SETTINGS = USER_DIR / "ps_picker_params.json"     # P波・S波検出の画面のパラメータ
HYPOCENTER_SETTINGS = USER_DIR / "hypocenter_params.json"   # 震源推定の画面のパラメータ
USER_SERVICE_ACCOUNT_DIR = USER_DIR / "service_account"     # Googleドライブの鍵(「鍵ファイルを取り込む」でここにコピー)


# 2つのフォルダを重ねたもの: ファイルは上(ユーザーの設定)にあればそれを、無ければ下(アプリに付く物)を使う
#   「フォルダ / 名前」でパスを返すので、今までのフォルダの Path と同じように使える(書き込みは上のフォルダに行う)
class OverlayDir:
  def __init__(self, upper, lower):
    self.upper, self.lower = Path(upper), Path(lower)

  def __truediv__(self, name):
    path = self.upper / name
    return path if path.exists() else self.lower / name

  # 両方のフォルダのファイル(同じ名前は上のもの)
  def glob(self, pattern):
    found = {p.name: p for p in self.lower.glob(pattern)}
    found.update({p.name: p for p in self.upper.glob(pattern)} if self.upper.exists() else {})
    return iter(sorted(found.values()))

  def exists(self):
    return self.upper.exists() or self.lower.exists()

  def __fspath__(self):
    return str(self.lower)

  def __str__(self):
    return str(self.lower)

ETC_DIR = OverlayDir(USER_ETC_DIR, BUNDLE_DIR / "etc")  # win.prm やチャンネル定義ファイルなどの設定
# Googleドライブの鍵と設定(ユーザーの設定にあればそれを、無ければアプリの横の service_account を使う)
SERVICE_ACCOUNT_DIR = OverlayDir(USER_SERVICE_ACCOUNT_DIR, PROJECT_DIR / "service_account")
TIMETABLE_FILE = USER_ETC_DIR / "train_timetable.csv"   # 洞爺駅の時刻表(各自が作る。書き込み先)


# 設定を読む・書く(settings.json)
def load_settings():
  try:
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}

def save_settings(settings):
  USER_DIR.mkdir(parents=True, exist_ok=True)
  tmp = SETTINGS_FILE.with_suffix(".tmp")
  tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
  tmp.replace(SETTINGS_FILE)


# --- 3. データフォルダ ---
# データフォルダの中の名前(前の版のプロジェクトのフォルダと同じ並び)
DATA_SUBDIRS = ("output", "daily_zips", "batch_plots", "jma_hypocenter", "v-net")

def default_data_dir():
  if sys.platform.startswith("win"):
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME / "data"
  return USER_DIR / "data"

# 前の版のデータ(プロジェクトのフォルダの output・daily_zips など)があるか
def _has_legacy_data():
  return any((PROJECT_DIR / name).exists() for name in ("output", "daily_zips"))

# データフォルダの場所: 設定にあればそれ。無ければ、前の版のデータがあるプロジェクトのフォルダか初期値(設定に書いておく)
def _find_data_dir():
  settings = load_settings()
  if settings.get("data_dir"):
    return Path(settings["data_dir"])
  data_dir = PROJECT_DIR if _has_legacy_data() else default_data_dir()
  try:
    save_settings({**settings, "data_dir": str(data_dir)})
  except OSError:
    pass   # 書けなくても、毎回同じように決まる
  return data_dir

# データフォルダの場所を変える(次に起動したときから使う。今のデータは移さない)
def set_data_dir(path):
  save_settings({**load_settings(), "data_dir": str(Path(path))})

DATA_DIR = _find_data_dir()
OUTPUT_DIR = DATA_DIR / "output"                       # WINファイル・SACファイルなどの出力先(キャッシュ)
ZIPS_DIR = DATA_DIR / "daily_zips"                     # 日ごとのZIPファイル
BATCH_PLOTS_DIR = DATA_DIR / "batch_plots"             # バッチ処理で保存する画像
JMA_HYPOCENTER_DIR = DATA_DIR / "jma_hypocenter"       # 気象庁の震源リスト(日ごとのCSV)
VNET_DIR = DATA_DIR / "v-net"                          # ほかの機関の観測点の波形の ZIP(防災科研のページからダウンロード)


# --- 前の版からの移行 ---
# 前の版でプロジェクトのフォルダ(output・etc・service_account)に置いていた小さな設定を、ユーザーの設定へ写す
#   (移す先に同じものがあれば写さない。元のファイルは残す。一度だけ行い、印のファイルを置く)
MIGRATION_MARK = USER_DIR / ".migrated_v1"
LEGACY_ITEMS = [
  (PROJECT_DIR / "output" / "picks", PICKS_DIR),
  (PROJECT_DIR / "output" / "history.json", HISTORY_FILE),
  (PROJECT_DIR / "etc" / "ps_picker_params.json", PS_PICKER_SETTINGS),
  (PROJECT_DIR / "etc" / "hypocenter_params.json", HYPOCENTER_SETTINGS),
  (PROJECT_DIR / "etc" / "train_timetable.csv", TIMETABLE_FILE),
  (PROJECT_DIR / "service_account", USER_SERVICE_ACCOUNT_DIR),
]

def migrate_legacy():
  if MIGRATION_MARK.exists():
    return []
  copied = []
  for src, dst in LEGACY_ITEMS:
    try:
      if not src.exists() or dst.exists():
        continue
      dst.parent.mkdir(parents=True, exist_ok=True)
      if src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("._*", ".DS_Store"))
      else:
        shutil.copy2(src, dst)
      copied.append(dst)
    except OSError:
      pass   # 写せなかったものは、次に起動したときにもう一度試す
  try:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    MIGRATION_MARK.write_text("前の版のプロジェクトのフォルダから設定を写しました\n", encoding="utf-8")
  except OSError:
    pass
  return copied

MIGRATED = migrate_legacy()


# ユーザーのキャッシュフォルダ(消えても作り直せるもの: フォント一覧、地図のタイルなど)
def _user_cache_dir():
  if sys.platform == "darwin":
    base = Path.home() / "Library" / "Caches"
  elif sys.platform.startswith("win"):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
  else:
    base = Path.home() / ".cache"
  return base / "UsuMantenViewer"

USER_CACHE_DIR = _user_cache_dir()
MAP_TILES_DIR = USER_CACHE_DIR / "map_tiles"           # 地理院タイル(震源推定の地図の背景)


# ビルドしたアプリで、Matplotlib のフォント一覧のキャッシュを残る場所に置く
# PyInstaller は Matplotlib の設定フォルダを、終了時に消える一時フォルダにする。そのため毎回
# フォント一覧を作り直すことになり、Mac では system_profiler でのフォント検索に約20秒かかって
# 起動が遅くなる。ユーザーのキャッシュフォルダに置けば、作るのは最初の1回だけで済む
# (matplotlib を読み込む前に設定する必要があるので、このモジュールの読み込み時に行う)
def _use_persistent_matplotlib_cache():
  cache_dir = USER_CACHE_DIR / "matplotlib"
  try:
    cache_dir.mkdir(parents=True, exist_ok=True)
  except OSError:
    return   # 作れなければ PyInstaller の一時フォルダのまま(遅いが動く)
  os.environ["MPLCONFIGDIR"] = str(cache_dir)

if FROZEN:
  _use_persistent_matplotlib_cache()
