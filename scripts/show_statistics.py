from datetime import datetime, timedelta
from pathlib import Path
import zipfile
import matplotlib.pyplot as plt
import pandas as pd

# daily_zipsフォルダのパスを指定（必要に応じて変更してください）
daily_zips_dir = Path("../daily_zips")

print("ZIPファイル群をスキャン中（解凍はしません）...")
records = []

# daily_zipsフォルダ内（サブフォルダ含む）のすべての .zip ファイルを走査
for zip_path in daily_zips_dir.glob("**/*.zip"):
  try:
    with zipfile.ZipFile(zip_path, "r") as zf:
      # ZIP内の全ファイル名リストを取得
      for filename in zf.namelist():
        fname = Path(filename).name
        if fname.endswith(".win"):
          parts = fname.split("_")
          if len(parts) >= 2:
            station_id = parts[0]
            time_str = parts[1].replace(".win", "")
            if len(time_str) == 10:
              try:
                # YYMMDDHHMM 形式から日時へ変換
                dt = datetime.strptime(time_str, "%y%m%d%H%M")
                records.append(
                  {"station": station_id, "time": dt, "zip_name": zip_path.name}
                )
              except ValueError:
                pass
  except Exception as e:
    print(f"警告: {zip_path.name} の読み込みに失敗しました ({e})")

if not records:
  print("有効なWINファイル名がZIP内で見つかりませんでした。パスを確認してください。")
else:
  df = pd.DataFrame(records)
  df = df.sort_values(["station", "time"]).reset_index(drop=True)

  # 拠点ごとにデータを集計し、連続している期間（ブロック）を抽出する
  # （ファイル間の空き時間が「2時間以上」空いたら別の収録期間とみなす設定）
  summary_list = []
  gap_threshold = timedelta(hours=2)

  for station, group in df.groupby("station"):
    times = group["time"].tolist()
    if not times:
      continue

    block_start = times[0]
    prev_t = times[0]

    for t in times[1:]:
      if t - prev_t > gap_threshold:
        summary_list.append({
          "Station": station,
          "Start_Time": block_start,
          "End_Time": prev_t,
          "File_Count": len(
            group[
              (group["time"] >= block_start)
              & (group["time"] <= prev_t)
            ]
          ),
        })
        block_start = t
      prev_t = t

    # 最後のブロックを追加
    summary_list.append({
      "Station": station,
      "Start_Time": block_start,
      "End_Time": prev_t,
      "File_Count": len(
        group[
          (group["time"] >= block_start) & (group["time"] <= prev_t)
        ]
      ),
    })

  summary_df = pd.DataFrame(summary_list)

  # 結果をコンソール表示 ＆ Excel/CSVに保存
  print("\n=== データ可用性サマリー (ZIP直接スキャン) ===")
  print(summary_df)

  summary_df.to_excel("data_availability_summary.xlsx", index=False)
  summary_df.to_csv("data_availability_summary.csv", index=False)
  print(
    "\n[保存完了] 'data_availability_summary.xlsx' および .csv"
    " に出力しました。"
  )

  # ガントチャート（タイムライン風）で視覚化
  plt.figure(figsize=(12, 6))
  stations = summary_df["Station"].unique()
  y_mapping = {st: i for i, st in enumerate(stations)}

  for _, row in summary_df.iterrows():
    y = y_mapping[row["Station"]]
    plt.barh(
      y,
      row["End_Time"] - row["Start_Time"],
      left=row["Start_Time"],
      height=0.4,
      color="skyblue",
      edgecolor="navy",
    )

  plt.yticks(list(y_mapping.values()), list(y_mapping.keys()))
  plt.xlabel("Timeline", fontsize=12)
  plt.ylabel("Station ID", fontsize=12)
  plt.title(
    "Data Availability Timeline per Station (from ZIP files)", fontsize=14
  )
  plt.grid(axis="x", linestyle="--", alpha=0.5)
  plt.tight_layout()
  plt.show()