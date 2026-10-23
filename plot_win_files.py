from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk
import zipfile

# MacのTcl/Tk非推奨警告を抑制
os.environ["TK_SILENCE_DEPRECATION"] = "1"

from obspy import Stream, read
import matplotlib.pyplot as plt


class SeismicPipelineApp:

  def __init__(self, root):
    self.root = root
    self.root.title("Seismic Pipeline Controller")
    self.root.geometry("450x260")
    self.root.resizable(False, False)

    # 安定したテーマの適用
    style = ttk.Style()
    if "clam" in style.theme_names():
      style.theme_use("clam")

    # --- UIレイアウト ---
    # パネルフレームを作成してウィジェットをすっきり整理
    control_panel = tk.Frame(root, padx=20, pady=20)
    control_panel.pack(fill=tk.BOTH, expand=True)

    # 1. 開始日時入力
    tk.Label(
        control_panel, text="開始日時 (YYYYMMDDHHmm):", font=("Arial", 11)
    ).pack(anchor="w", pady=(0, 5))

    self.entry_start = tk.Entry(
        control_panel,
        font=("Arial", 12),
        bg="white",
        fg="black",
        insertbackground="black",
        relief="solid",
        bd=1,
    )
    self.entry_start.insert(0, "202608232245")
    self.entry_start.pack(fill=tk.X, pady=(0, 15))

    # 2. 終了日時入力
    tk.Label(
        control_panel, text="終了日時 (YYYYMMDDHHmm):", font=("Arial", 11)
    ).pack(anchor="w", pady=(0, 5))

    self.entry_end = tk.Entry(
        control_panel,
        font=("Arial", 12),
        bg="white",
        fg="black",
        insertbackground="black",
        relief="solid",
        bd=1,
    )
    self.entry_end.insert(0, "202608232250")
    self.entry_end.pack(fill=tk.X, pady=(0, 20))

    # 3. 実行ボタン
    btn_run = tk.Button(
        control_panel,
        text="抽出・変換・プロット実行",
        font=("Arial", 11, "bold"),
        bg="#4CAF50",
        fg="white",
        activebackground="#45a049",
        command=self.execute_pipeline,
    )
    btn_run.pack(fill=tk.X, ipady=4)

  def execute_pipeline(self):
    start_str = self.entry_start.get().strip()
    end_str = self.entry_end.get().strip()

    try:
      start_dt = datetime.strptime(start_str, "%Y%m%d%H%M")
      end_dt = datetime.strptime(end_str, "%Y%m%d%H%M")
    except ValueError:
      messagebox.showerror(
          "エラー",
          "日時の形式が正しくありません。\nYYYYMMDDHHmm (例: 202608232245)"
          " で入力してください。",
      )
      return

    if start_dt > end_dt:
      messagebox.showerror(
          "エラー", "開始日時は終了日時より前を指定してください。"
      )
      return

    base_dir = Path(__file__).parent.resolve()
    output_base = base_dir.parent / "output"
    zips_root = base_dir.parent / "daily_zips"
    tools_dir = base_dir.parent / "tools"
    etc_dir = base_dir.parent / "etc"

    dest_dir = output_base / start_str
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not zips_root.exists():
      messagebox.showerror(
          "エラー", f"'daily_zips' フォルダが見つかりません:\n{zips_root}"
      )
      return

    # --- Step 1: 抽出 ---
    target_dates = set()
    curr = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date_only = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    while curr <= end_date_only:
      target_dates.add(curr.strftime("%y%m%d"))
      curr += timedelta(days=1)

    target_zips = []
    for zip_path in zips_root.glob("**/*.zip"):
      if any(d in zip_path.stem for d in target_dates):
        target_zips.append(zip_path)

    if not target_zips:
      messagebox.showwarning(
          "警告", "指定された期間に一致するZIPファイルが見つかりませんでした。"
      )
      return

    extracted_count = 0
    for zip_path in target_zips:
      try:
        with zipfile.ZipFile(zip_path, "r") as zf:
          for internal_path in zf.namelist():
            filename = Path(internal_path).name
            if not filename.endswith(".win"):
              continue
            parts = filename.split("_")
            if len(parts) < 2:
              continue
            datetime_str = parts[1].replace(".win", "")
            if len(datetime_str) != 10 or not datetime_str.isdigit():
              continue

            try:
              f_dt = datetime(
                  2000 + int(datetime_str[0:2]),
                  int(datetime_str[2:4]),
                  int(datetime_str[4:6]),
                  int(datetime_str[6:8]),
                  int(datetime_str[8:10]),
              )
            except ValueError:
              continue

            if start_dt <= f_dt <= end_dt:
              dest_file = dest_dir / filename
              with zf.open(internal_path) as src, open(dest_file, "wb") as dst:
                dst.write(src.read())
              extracted_count += 1
      except Exception as e:
        print(f"ZIP読み込みエラー ({zip_path.name}): {e}")

    if extracted_count == 0:
      messagebox.showwarning(
          "警告",
          "指定範囲内に該当するWINファイルが存在しませんでした（0件抽出）。",
      )
      return

    # --- Step 2: 結合 ＆ SAC変換 ---
    win2sac_path = tools_dir / "win2sac_32"
    win_prm_path = etc_dir / "win.prm"
    manten_tbl_path = etc_dir / "manten_ch.tbl"

    if not win2sac_path.exists() or not win_prm_path.exists():
      messagebox.showerror(
          "エラー", "tools または etc 内に必要な実行ファイル/設定が不足しています。"
      )
      return

    shutil.copy2(manten_tbl_path, dest_dir / "manten_ch.tbl")

    station_files = defaultdict(list)
    for win_file in dest_dir.glob("*.win"):
      if win_file.name.startswith("combined_"):
        continue
      parts = win_file.name.split("_")
      if len(parts) >= 2:
        station_files[parts[0]].append(win_file)

    for station_id, files in station_files.items():
      sorted_files = sorted(files, key=lambda x: x.name)
      combined_win = dest_dir / f"combined_{station_id}.win"

      with open(combined_win, "wb") as wfd:
        for f in sorted_files:
          with open(f, "rb") as rfd:
            wfd.write(rfd.read())

      station_ch_path = etc_dir / f"{station_id}.ch"
      if not station_ch_path.exists():
        station_ch_path = etc_dir / "manten.ch"

      cmd = [
          str(win2sac_path.resolve()),
          str(combined_win.name),
          str(station_ch_path.resolve()),
          f"{station_id}_sac",
          ".",
          f"-p{win_prm_path.resolve()}",
      ]
      subprocess.run(
          cmd, cwd=dest_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
      )

    # --- Step 3: グラフ描画 ---
    st = read(str(dest_dir / "*_sac*"))
    if len(st) == 0:
      messagebox.showerror("エラー", "SACファイルの生成に失敗しました。")
      return

    desired_order = ["U", "N", "E"]
    st = Stream(
        sorted(
            st,
            key=lambda tr: (
                tr.stats.station,
                desired_order.index(tr.stats.channel[-1])
                if tr.stats.channel[-1] in desired_order
                else 99,
            ),
        )
    )

    num_traces = len(st)
    fig_height = max(6, num_traces * 1.0)
    fig, axes = plt.subplots(
        num_traces,
        1,
        sharex=True,
        figsize=(10, fig_height),
        gridspec_kw={"hspace": 0},
    )
    if num_traces == 1:
      axes = [axes]

    component_colors = {"U": "#1f77b4", "N": "#ff7f0e", "E": "#2ca02c"}

    for tr, ax in zip(st, axes):
      times = tr.times()
      data = tr.data
      ch = tr.stats.channel
      comp = ch[-1] if ch else ""
      col = component_colors.get(comp, "#333333")

      ax.plot(times, data, color=col, linewidth=0.7)
      station_name = tr.stats.station
      ax.set_ylabel(
          f"{station_name}\n({ch})",
          fontsize=8,
          rotation=0,
          labelpad=25,
          ha="right",
      )
      ax.yaxis.set_label_coords(-0.08, 0.5)
      ax.grid(True, linestyle="--", alpha=0.3)
      ax.tick_params(axis="y", labelsize=7)
      ax.tick_params(labelbottom=False)

    axes[-1].tick_params(labelbottom=True)
    axes[-1].set_xlabel("Time [s]", fontsize=11)
    plt.suptitle(
        f"Seismic Waveforms ({start_str}) - Total Traces: {num_traces}",
        fontsize=13,
    )
    plt.tight_layout()

    messagebox.showinfo(
        "完了",
        f"抽出・変換・グラフ描画の準備が完了しました！\n抽出ファイル数: {extracted_count} 個",
    )
    plt.show()


def main():
  root = tk.Tk()
  app = SeismicPipelineApp(root)
  root.mainloop()


if __name__ == "__main__":
  main()