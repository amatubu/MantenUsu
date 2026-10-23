from collections import defaultdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
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
    self.root.title("Seismic Pipeline Controller (Pro)")
    self.root.geometry("460x470")
    self.root.resizable(False, False)

    # パス設定
    self.base_dir = Path(__file__).parent.resolve()
    self.output_base = self.base_dir.parent / "output"
    self.zips_root = self.base_dir.parent / "daily_zips"
    self.tools_dir = self.base_dir.parent / "tools"
    self.etc_dir = self.base_dir.parent / "etc"
    self.history_file = self.output_base / "history.json"

    self.output_base.mkdir(parents=True, exist_ok=True)

    # 安定したテーマの適用
    style = ttk.Style()
    if "clam" in style.theme_names():
      style.theme_use("clam")

    # --- UIレイアウト ---
    panel = tk.Frame(root, padx=20, pady=20)
    panel.pack(fill=tk.BOTH, expand=True)

    # 1. 地震タイトル / コメント
    tk.Label(
        panel, text="地震タイトル / コメント:", font=("Arial", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))
    self.entry_title = tk.Entry(
        panel,
        font=("Arial", 11),
        bg="white",
        fg="black",
        insertbackground="black",
        relief="solid",
        bd=1,
    )
    self.entry_title.insert(0, "浦河沖地震")
    self.entry_title.pack(fill=tk.X, pady=(0, 8))

    # 2. 開始日時
    tk.Label(
        panel, text="開始日時 (YYYYMMDDHHmm):", font=("Arial", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))
    self.entry_start = tk.Entry(
        panel,
        font=("Arial", 11),
        bg="white",
        fg="black",
        insertbackground="black",
        relief="solid",
        bd=1,
    )
    self.entry_start.insert(0, "202608232245")
    self.entry_start.pack(fill=tk.X, pady=(0, 8))

    # 3. 終了日時 ＆ コピーボタン用フレーム
    end_frame = tk.Frame(panel)
    end_frame.pack(fill=tk.X, pady=(0, 2))
    tk.Label(
        end_frame, text="終了日時 (YYYYMMDDHHmm):", font=("Arial", 10, "bold")
    ).pack(side=tk.LEFT)

    btn_copy = tk.Button(
        end_frame,
        text="開始時間をコピー",
        font=("Arial", 9),
        command=self.copy_start_to_end,
    )
    btn_copy.pack(side=tk.RIGHT)

    self.entry_end = tk.Entry(
        panel,
        font=("Arial", 11),
        bg="white",
        fg="black",
        insertbackground="black",
        relief="solid",
        bd=1,
    )
    self.entry_end.insert(0, "202608232250")
    self.entry_end.pack(fill=tk.X, pady=(0, 2))

    # 継続時間（●分間）のリアルタイム表示ラベル
    self.lbl_duration = tk.Label(
        panel, text="", font=("Arial", 9, "italic"), fg="#0066cc"
    )
    self.lbl_duration.pack(anchor="w", pady=(0, 12))

    # 入力変更をリアルタイムで検知して継続時間を更新するバインド
    self.entry_start.bind("<KeyRelease>", lambda e: self.update_duration_display())
    self.entry_end.bind("<KeyRelease>", lambda e: self.update_duration_display())

    # 4. 実行ボタン
    btn_run = tk.Button(
        panel,
        text="抽出・変換・プロット実行 (自動判定)",
        font=("Arial", 11, "bold"),
        bg="#4CAF50",
        fg="white",
        activebackground="#45a049",
        command=lambda: self.execute_pipeline(),
    )
    btn_run.pack(fill=tk.X, ipady=4, pady=(0, 15))

    # 5. 履歴セクション
    tk.Label(
        panel, text="過去の実行履歴 (再表示・削除):", font=("Arial", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))

    hist_frame = tk.Frame(panel)
    hist_frame.pack(fill=tk.X, pady=(0, 5))

    self.history_combo = ttk.Combobox(
        hist_frame, font=("Arial", 10), state="readonly"
    )
    self.history_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

    btn_load_hist = tk.Button(
        hist_frame,
        text="再表示",
        font=("Arial", 9),
        command=self.load_from_history,
    )
    btn_load_hist.pack(side=tk.LEFT, padx=(0, 3))

    btn_del_hist = tk.Button(
        hist_frame, text="削除", font=("Arial", 9), fg="red", command=self.delete_history
    )
    btn_del_hist.pack(side=tk.LEFT)

    # 履歴データの読み込みと初期の継続時間計算
    self.update_history_ui()
    self.update_duration_display()

  def update_duration_display(self):
    start_str = self.entry_start.get().strip()
    end_str = self.entry_end.get().strip()

    try:
      start_dt = datetime.strptime(start_str, "%Y%m%d%H%M")
      end_dt = datetime.strptime(end_str, "%Y%m%d%H%M")

      # ★ 両端の分（開始分と終了分）を両方含めた区間（個数・分分）として計算するため +1分を加算
      diff = (end_dt - start_dt) + timedelta(minutes=1)
      secs = int(diff.total_seconds())

      if secs <= 0:
        self.lbl_duration.config(
            text="⚠️ エラー: 終了日時が開始日時より前です", fg="red"
        )
        return

      mins = secs // 60
      rem_secs = secs % 60
      hours = mins // 60
      rem_mins = mins % 60

      if hours > 0:
        duration_text = (
            f"継続時間: {hours}時間 {rem_mins}分 ({secs:,}秒間)"
        )
      elif mins > 0:
        duration_text = f"継続時間: {mins}分 {rem_secs}秒 ({secs:,}秒間)"
      else:
        duration_text = f"継続時間: {secs}秒間"

      self.lbl_duration.config(text=duration_text, fg="#0066cc")
    except ValueError:
      self.lbl_duration.config(
          text="形式: YYYYMMDDHHmm (例: 202608232245)", fg="gray"
      )

  def copy_start_to_end(self):
    start_val = self.entry_start.get()
    self.entry_end.delete(0, tk.END)
    self.entry_end.insert(0, start_val)
    self.update_duration_display()

  def load_history_data(self):
    if not self.history_file.exists():
      return []
    try:
      with open(self.history_file, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      return []

  def save_history_data(self, history_list):
    try:
      with open(self.history_file, "w", encoding="utf-8") as f:
        json.dump(history_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
      print(f"履歴保存エラー: {e}")

  def update_history_ui(self):
    history = self.load_history_data()
    display_list = [
        f"{item['start']} ～ {item['end']} [{item.get('title', '')}]"
        for item in history
    ]
    self.history_combo["values"] = display_list
    if display_list:
      self.history_combo.current(0)
    else:
      self.history_combo.set("")

  def add_history(self, start_str, end_str, title_str):
    history = self.load_history_data()
    new_entry = {"start": start_str, "end": end_str, "title": title_str}
    history = [h for h in history if h != new_entry]
    history.insert(0, new_entry)
    history = history[:20]
    self.save_history_data(history)
    self.update_history_ui()

  def load_from_history(self):
    selected = self.history_combo.get()
    if not selected:
      return
    history = self.load_history_data()
    idx = self.history_combo.current()
    if 0 <= idx < len(history):
      item = history[idx]
      self.entry_title.delete(0, tk.END)
      self.entry_title.insert(0, item.get("title", ""))
      self.entry_start.delete(0, tk.END)
      self.entry_start.insert(0, item["start"])
      self.entry_end.delete(0, tk.END)
      self.entry_end.insert(0, item["end"])
      self.update_duration_display()
      self.execute_pipeline(is_history_reload=True)

  def delete_history(self):
    selected = self.history_combo.get()
    if not selected:
      messagebox.showwarning("警告", "削除する履歴が選択されていません。")
      return

    history = self.load_history_data()
    idx = self.history_combo.current()
    if 0 <= idx < len(history):
      if messagebox.askyesno(
          "確認", f"以下の履歴を削除しますか？\n\n{selected}"
      ):
        del history[idx]
        self.save_history_data(history)
        self.update_history_ui()

  def execute_pipeline(self, is_history_reload=False):
    title_str = self.entry_title.get().strip()
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

    dest_dir = self.output_base / f"{start_str}_{end_str}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- キャッシュチェック (Smart Caching) ---
    existing_sacs = list(dest_dir.glob("*_sac*"))

    if existing_sacs and not is_history_reload:
      self.plot_waveforms(dest_dir, start_str, end_str, title_str)
      self.add_history(start_str, end_str, title_str)
      return

    if is_history_reload and existing_sacs:
      self.plot_waveforms(dest_dir, start_str, end_str, title_str)
      return

    # --- Step 1: 抽出 (なければ実行) ---
    existing_wins = list(dest_dir.glob("*.win"))
    extracted_count = len(existing_wins)

    if not existing_wins:
      if not self.zips_root.exists():
        messagebox.showerror(
            "エラー", f"'daily_zips' フォルダが見つかりません:\n{self.zips_root}"
        )
        return

      target_dates = set()
      curr = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
      end_date_only = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
      while curr <= end_date_only:
        target_dates.add(curr.strftime("%y%m%d"))
        curr += timedelta(days=1)

      target_zips = []
      for zip_path in self.zips_root.glob("**/*.zip"):
        if any(d in zip_path.stem for d in target_dates):
          target_zips.append(zip_path)

      if not target_zips:
        messagebox.showwarning(
            "警告", "指定された期間に一致するZIPファイルが見つかりませんでした。"
        )
        return

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
                with zf.open(internal_path) as src, open(
                    dest_file, "wb"
                ) as dst:
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
    win2sac_path = self.tools_dir / "win2sac_32"
    win_prm_path = self.etc_dir / "win.prm"
    manten_tbl_path = self.etc_dir / "manten_ch.tbl"

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

      window_file_obj = open(combined_win, "wb")
      for f in sorted_files:
        with open(f, "rb") as rfd:
          window_file_obj.write(rfd.read())
      window_file_obj.close()

      station_ch_path = self.etc_dir / f"{station_id}.ch"
      if not station_ch_path.exists():
        station_ch_path = self.etc_dir / "manten.ch"

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

    # --- 履歴に追加してプロット ---
    self.add_history(start_str, end_str, title_str)
    self.plot_waveforms(dest_dir, start_str, end_str, title_str)

  def plot_waveforms(self, dest_dir, start_str, end_str, title_str):
    # 日本語フォント設定を安全に適用
    plt.rcParams["font.family"] = "Hiragino Sans"
    plt.rcParams["axes.unicode_minus"] = False

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
    fig_width = 11.5
    fig_height = max(6, num_traces * 1.0)

    fig, axes = plt.subplots(
        num_traces,
        1,
        sharex=True,
        figsize=(fig_width, fig_height),
        gridspec_kw={"hspace": 0},
    )
    if num_traces == 1:
      axes = [axes]

    fig.subplots_adjust(left=0.09, right=0.97, bottom=0.08, top=0.92)

    component_colors = {"U": "#1f77b4", "N": "#ff7f0e", "E": "#2ca02c"}

    for tr, ax in zip(st, axes):
      times = tr.times()
      data = tr.data
      ch = tr.stats.channel
      comp = ch[-1] if ch else ""
      col = component_colors.get(comp, "#333333")

      ax.plot(times, data, color=col, linewidth=0.4)
      station_name = tr.stats.station

      ax.set_ylabel(
          f"{station_name}\n({ch})",
          fontsize=8,
          rotation=90,
          ha="center",
          va="center",
      )
      ax.yaxis.set_label_coords(-0.06, 0.5)

      ax.grid(True, linestyle="--", alpha=0.3)
      ax.tick_params(axis="y", labelsize=7)
      ax.tick_params(labelbottom=False)

    axes[-1].tick_params(labelbottom=True)
    axes[-1].set_xlabel("Time [s]", fontsize=11)

    title_display = (
        f"{start_str} - {end_str}"
        if not title_str
        else f"{start_str} : {title_str}"
    )
    plt.suptitle(
        f"Seismic Waveforms ({title_display}) - Total Traces: {num_traces}",
        fontsize=13,
    )
    plt.show()


def main():
  root = tk.Tk()
  app = SeismicPipelineApp(root)
  root.mainloop()


if __name__ == "__main__":
  main()