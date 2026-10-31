# cache_manager.py
# キャッシュ(output フォルダ・daily_zips フォルダ)の集計と削除
from fnmatch import fnmatch
import os
import shutil

import win_pipeline

# output フォルダ内のファイルの種類: (キー, 表示名)
# どれにも当てはまらないファイル(チャンネル定義ファイルのコピーなど)は "other"
OUTPUT_KINDS = [
  ("raw_win",    "結合前のWINファイル"),
  ("merged_win", "結合後のWINファイル (combined_ / corrected_)"),
  ("sac",        "SACファイル"),
]


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
          if p.is_file() and p.resolve() not in keep]

# ファイルをまとめて削除する
def delete_files(files):
  for p in files:
    p.unlink()


# output フォルダ内のファイル名から種類(OUTPUT_KINDS のキー、または "other")を返す
def classify(name):
  if name.endswith(win_pipeline.WIN_SUFFIX):
    if name.startswith((win_pipeline.COMBINED_PREFIX, win_pipeline.CORRECTED_PREFIX)):
      return "merged_win"
    return "raw_win"
  if fnmatch(name, win_pipeline.SAC_PATTERN):
    return "sac"
  return "other"

# output フォルダの下の各フォルダ(期間ごと・1時間ごと)を返す。直下のファイル(履歴など)は含めない
def _cache_dirs(root):
  with os.scandir(root) as it:
    return [e.path for e in it if e.is_dir(follow_symlinks=False)]

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
        os.remove(path)
        deleted += 1
        if deleted % 100 == 0:
          if cancelled():
            return deleted
          if on_progress:
            on_progress(deleted)
      elif kind != "other":
        has_data = True
    if not has_data:
      shutil.rmtree(d)
  return deleted
