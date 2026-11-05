# cache_manager.py
# キャッシュ(output フォルダ・daily_zips フォルダ)の集計と削除
from fnmatch import fnmatch
import os
import shutil
import sys

import win_pipeline

# output フォルダ内のファイルの種類: (キー, 表示名)
# どれにも当てはまらないファイル(チャンネル定義ファイルのコピーなど)は "other"
OUTPUT_KINDS = [
  ("raw_win",    "結合前のWINファイル"),
  ("merged_win", "結合後のWINファイル (combined_ / corrected_)"),
  ("sac",        "SACファイル"),
]


# Mac が外付けドライブ(exFAT など)に作る付随ファイル(._ で始まる。中身はファイルの属性で、WIN や SAC ではない)か
#   外付けドライブでは、ファイルを消すと macOS が対の ._ ファイルも消す。そのため、集計にも削除の対象にも入れない
#   (先に一覧にしておいた ._ ファイルを消そうとすると、もう無くてエラーになる)
def is_appledouble(name):
  return name.startswith(win_pipeline.APPLEDOUBLE_PREFIX)

# ファイルを消す(すでに無ければ何もしない。対のファイルと一緒に macOS が消したときなど)
#   戻り値 ... 消したら True
def remove_file(path):
  try:
    os.remove(path)
    return True
  except FileNotFoundError:
    return False

# フォルダを中身ごと消す(途中で、すでに無くなったファイルがあっても続ける。理由は is_appledouble と同じ)
def remove_tree(path):
  def ignore_missing(func, p, exc):
    error = exc if isinstance(exc, BaseException) else exc[1]
    if not isinstance(error, FileNotFoundError):
      raise error
  if sys.version_info >= (3, 12):
    shutil.rmtree(path, onexc=ignore_missing)
  else:
    shutil.rmtree(path, onerror=ignore_missing)

# 容量を読みやすい単位(B, KB, MB, GB, TB)の文字列にする
def format_size(size_bytes):
  for unit in ["B", "KB", "MB", "GB"]:
    if size_bytes < 1024.0:
      return f"{size_bytes:.1f} {unit}"
    size_bytes /= 1024.0
  return f"{size_bytes:.1f} TB"

# ファイルの合計サイズ(バイト)
def total_size(files):
  return sum(p.stat().st_size for p in files if p.is_file())

# フォルダ内(サブフォルダも含む)のファイルを集める
#   root    ... 対象のフォルダ
#   pattern ... ファイル名のパターン(例: "*.zip")
#   keep    ... 対象から除くファイルのパスの集合
def collect_files(root, pattern="*", keep=()):
  keep = {p.resolve() for p in keep}
  return [p for p in root.glob(f"**/{pattern}")
          if p.is_file() and not is_appledouble(p.name) and p.resolve() not in keep]

# ファイルをまとめて削除する
def delete_files(files):
  for p in files:
    remove_file(p)


# output フォルダ内のファイル名から種類(OUTPUT_KINDS のキー、または "other")を返す
#   Mac の付随ファイル(._ で始まる)は "other"(._xxx.win も WIN ファイルとして数えない)
def classify(name):
  if is_appledouble(name):
    return "other"
  if name.endswith(win_pipeline.WIN_SUFFIX):
    if name.startswith((win_pipeline.COMBINED_PREFIX, win_pipeline.CORRECTED_PREFIX)):
      return "merged_win"
    return "raw_win"
  if fnmatch(name, win_pipeline.SAC_PATTERN):
    return "sac"
  return "other"

# キャッシュとして扱わない output フォルダの下のフォルダ
#   picks ... 震源推定の画面で保存した到着時刻(pick_store.py)。作り直せないので消さない
#   quake_rms ... 地震の候補を探す処理の揺れの大きさ(quake_detect.py)。作り直すのに時間がかかるので消さない
#   vnet ... ほかの機関の観測点の変換済みの SAC(external_stations.py)。メンテナンス画面の「ほかの機関の観測点」で消す
#   picks で始まるフォルダ(picks_backup_... など、到着時刻のバックアップ)も消さない
#   quake_features ... 地震の候補ごとの波形の解析結果(quake_detect.py)。作り直すのに時間がかかるので消さない
KEEP_DIRS = {"picks", "quake_rms", "vnet", "quake_features"}

# output フォルダの下の各フォルダ(期間ごと・1時間ごと)を返す。直下のファイル(履歴など)と KEEP_DIRS は含めない
def _cache_dirs(root):
  with os.scandir(root) as it:
    return [e.path for e in it if e.is_dir(follow_symlinks=False)
            and e.name not in KEEP_DIRS and not e.name.startswith("picks")]

# フォルダ内のファイルを、サブフォルダも含めて順に返す: (パス, ファイル名, os.DirEntry)
def _walk_files(path):
  with os.scandir(path) as it:
    for e in it:
      if e.is_dir(follow_symlinks=False):
        yield from _walk_files(e.path)
      elif e.is_file(follow_symlinks=False):
        yield e.path, e.name, e

# output フォルダのキャッシュを種類ごとに集計する(ファイル数が多いと時間がかかる)
#   root        ... output フォルダ
#   on_progress ... 途中経過を受け取る関数(それまでに数えたファイル数)。100件ごとに呼ぶ
#   cancelled   ... 中止するときに True を返す関数
#   戻り値       ... {種類: [ファイル数, 合計サイズ]}。中止したら None
def scan_output(root, on_progress=None, cancelled=lambda: False):
  stats = {key: [0, 0] for key, _ in OUTPUT_KINDS + [("other", "")]}
  count = 0
  for d in _cache_dirs(root):
    for _path, name, entry in _walk_files(d):
      st = stats[classify(name)]
      st[0] += 1
      st[1] += entry.stat(follow_symlinks=False).st_size
      count += 1
      if count % 100 == 0:
        if cancelled():
          return None
        if on_progress:
          on_progress(count)
  return stats

# output フォルダのキャッシュのうち、指定した種類のファイルを削除する
# 削除後、WINファイルもSACファイルも残っていないフォルダは、残りのファイルごと削除する
#   root        ... output フォルダ(直下のファイル(履歴など)は削除しない)
#   kinds       ... 削除する種類(OUTPUT_KINDS のキー)の集合
#   on_progress ... 途中経過を受け取る関数(それまでに削除したファイル数)。100件ごとに呼ぶ
#   cancelled   ... 中止するときに True を返す関数(途中まで削除した状態で終わる)
#   戻り値       ... 削除したファイル数
def delete_output_kinds(root, kinds, on_progress=None, cancelled=lambda: False):
  deleted = 0
  for d in _cache_dirs(root):
    has_data = False   # WINファイルかSACファイルが残っているか
    for path, name, _entry in _walk_files(d):
      kind = classify(name)
      if kind in kinds:
        if not remove_file(path):
          continue
        deleted += 1
        if deleted % 100 == 0:
          if cancelled():
            return deleted
          if on_progress:
            on_progress(deleted)
      elif kind != "other":
        has_data = True
    if not has_data:
      remove_tree(d)
  return deleted
