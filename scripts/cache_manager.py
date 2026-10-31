# cache_manager.py
# キャッシュ(output フォルダ・daily_zips フォルダ)の集計と削除
import shutil


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

# フォルダの中身(ファイル・サブフォルダ)をすべて削除する。フォルダ自体は残す
#   keep ... 削除しないファイルのパスの集合(フォルダの直下にあるもの)
def delete_contents(root, keep=()):
  keep = {p.resolve() for p in keep}
  for child in root.iterdir():
    if child.resolve() in keep:
      continue
    if child.is_dir():
      shutil.rmtree(child)
    else:
      child.unlink()

# ファイルをまとめて削除する
def delete_files(files):
  for p in files:
    p.unlink()
