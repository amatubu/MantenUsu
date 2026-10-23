import os
from collections import defaultdict


def main():
  source_root = "."

  if not os.path.exists(source_root):
    print(f"エラー: コピー元フォルダが見つかりません: {source_root}")
    return

  # 集計用の辞書
  # daily_stats: { "260823": {"count": 0, "size": 0} }
  # monthly_stats: { "2608": {"count": 0, "size": 0} }
  daily_stats = defaultdict(lambda: {"count": 0, "size": 0})
  monthly_stats = defaultdict(lambda: {"count": 0, "size": 0})

  total_files = 0
  total_size = 0

  print("ファイル構造をスキャン中... しばらくお待ちください。\n")

  for id_folder in os.listdir(source_root):
    id_path = os.path.join(source_root, id_folder)
    if not os.path.isdir(id_path) or not id_folder.isdigit() or len(id_folder) != 10:
      continue

    for date_folder in os.listdir(id_path):
      date_path = os.path.join(id_path, date_folder)
      if (
          not os.path.isdir(date_path)
          or not date_folder.isdigit()
          or len(date_folder) != 6
      ):
        continue

      # 日付フォルダ名から月を抽出 (例: "260823" -> 年:26, 月:08 -> "2608")
      month_key = date_folder[0:4]

      for filename in os.listdir(date_path):
        if not filename.endswith(".win"):
          continue

        file_path = os.path.join(date_path, filename)
        try:
          file_size = os.path.getsize(file_path)
        except OSError:
          continue

        total_files += 1
        total_size += file_size

        # 日別の集計
        daily_stats[date_folder]["count"] += 1
        daily_stats[date_folder]["size"] += file_size

        # 月別の集計
        monthly_stats[month_key]["count"] += 1
        monthly_stats[month_key]["size"] += file_size

  # 容量を読みやすい単位（KB, MB, GB）に変換する関数
  def format_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
      if size_bytes < 1024.0:
        return f"{size_bytes:.2f} {unit}"
      size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

  # --- 結果の表示 ---
  print("=" * 50)
  print(f"全体サマリー: 総ファイル数 {total_files} 個, 総容量 {format_size(total_size)}")
  print("=" * 50)

  print("\n【月別シミュレーション（月単位Zipのイメージ）】")
  print(f"{'月':<10} | {'ファイル数':<10} | {'合計サイズ':<12}")
  print("-" * 38)
  for month in sorted(monthly_stats.keys()):
    c = monthly_stats[month]["count"]
    s = format_size(monthly_stats[month]["size"])
    print(f"20{month[:2]}年{month[2:]}月 ({month})" + f" | {c:<10} | {s}")

  print("\n【日別シミュレーション（日単位Zipのイメージ - 一部抜粋）】")
  print(f"{'日付':<10} | {'ファイル数':<10} | {'合計サイズ':<12}")
  print("-" * 38)
  # 日別は数が多いかもしれないので上位・あるいは全て表示（今回は全て、または見やすく）
  for date in sorted(daily_stats.keys()):
    c = daily_stats[date]["count"]
    s = format_size(daily_stats[date]["size"])
    print(f"20{date[:2]}年{date[2:4]}月{date[4:]}日 ({date})" + f" | {c:<10} | {s}")
  print("=" * 50)


if __name__ == "__main__":
  main()