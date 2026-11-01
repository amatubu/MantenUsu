# quake_locate_window.py
# 地震の候補の震源の画面: 候補ごとの結果の表と、震央の地図・東西と南北の断面図を並べて表示する
#   表の行を選ぶと地図と断面図にその震源の印を付け、ダブルクリック(または「震源推定を開く」)で
#   その候補の震源推定の画面を開く(到着時刻を手で直して確かめるため)
import tkinter as tk
from tkinter import ttk

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

import quake_locate
from quake_features import USU_SUMMIT

# 表の列: (見出し, 幅 [px], 揃え)
COLUMNS = [("番号", 44, "e"), ("開始", 140, "w"), ("判定", 120, "w"), ("深さ[km]", 70, "e"),
           ("残差[秒]", 70, "e"), ("山頂から[km]", 90, "e"), ("気象庁の震源リスト", 200, "w")]

# 選んだ震源の印の色
SELECT_COLOR = "#1f77b4"

def fmt(v, spec):
  return "" if v is None else format(v, spec)

# 地震の候補の震源の画面を開く
#   parent  ... 親のウィンドウ
#   items   ... [(候補, 結果), ...](quake_locate.locate_all の結果)
#   stations, proj ... 拠点の位置と平面座標
#   title   ... 画面と図のタイトル
#   on_open_event ... (候補, 結果) を渡すと、その候補の震源推定の画面を開く関数
#   on_save ... 「一覧と図を保存」で呼ぶ関数(引数なし)
def open_locate_window(parent, items, stations, proj, title, on_open_event, on_save):
  win = tk.Toplevel(parent)
  win.title(f"地震の候補の震源 - {title}")
  win.geometry("1560x900")

  # --- 左: 結果の表と操作 ---
  left = ttk.Frame(win, padding=8)
  left.pack(side=tk.LEFT, fill=tk.Y)
  ttk.Label(left, text=f"{len(items)}件: {quake_locate.count_summary(items)}", wraplength=720,
            justify=tk.LEFT).pack(anchor="w", pady=(0, 6))
  table_frame = ttk.Frame(left)
  table_frame.pack(fill=tk.BOTH, expand=True)
  tree = ttk.Treeview(table_frame, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse")
  for name, width, anchor in COLUMNS:
    tree.heading(name, text=name)
    tree.column(name, width=width, anchor=anchor, stretch=False)
  scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
  tree.configure(yscrollcommand=scroll.set)
  tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
  scroll.pack(side=tk.LEFT, fill=tk.Y)

  buttons = ttk.Frame(left)
  buttons.pack(fill=tk.X, pady=(8, 0))
  ttk.Label(left, text="行を選ぶと図に青い丸で示します。ダブルクリックで震源推定の画面を開き、到着時刻を手で直して確かめられます"
                       "(画面での手直しは、この一覧には反映されません)。", wraplength=720, justify=tk.LEFT,
            foreground="#555555").pack(anchor="w", pady=(6, 0))

  # --- 右: 震央の地図と断面図 ---
  right = ttk.Frame(win)
  right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
  fig = Figure(figsize=(9.6, 7.8))
  canvas = FigureCanvasTkAgg(fig, master=right)
  drawn = quake_locate.draw_location_map(fig, items, stations, proj, title, listing=False)
  toolbar = NavigationToolbar2Tk(canvas, right)
  toolbar.update()
  canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

  # 地図の番号(近い地震だけ)。結果ごとに、地図での位置も覚えておく
  number = {id(r): i for i, (_cand, r) in enumerate(drawn["local"], 1)}
  position = {id(r): p for (_cand, r), p in zip(drawn["local"], drawn["points"])}
  axm, axe, axn = drawn["axes"]
  marks = [ax.plot([], [], "o", ms=16, mfc="none", mec=SELECT_COLOR, mew=2.5, zorder=8)[0] for ax in (axm, axe, axn)]

  # 表の行
  rows = {}
  for cand, r in items:
    loc, q = r["loc"], cand["jma"]
    local = r["kind"] in (quake_locate.KIND_LOCAL, quake_locate.KIND_LOCAL_POOR)
    depth = ("不定" if loc["at_top"] else fmt(loc["depth"], ".1f")) if local else ""
    summit = quake_locate.epicenter_distance(r["lat"], r["lon"], *USU_SUMMIT[:2])
    jma = f"{q['震央地名']} M{fmt(q['M'], 'g')}" if q else ""
    iid = tree.insert("", tk.END, values=[number.get(id(r), ""), f"{cand['start']:%Y-%m-%d %H:%M:%S}", r["kind"],
                                          depth, fmt(loc["rms"], ".3f") if loc else "", fmt(summit, ".1f"), jma])
    rows[iid] = (cand, r)

  def selected():
    sel = tree.selection()
    return rows.get(sel[0]) if sel else None

  # 選んだ行の震源に印を付ける(地図に描いていない遠い地震などは印を消す)
  def on_select(_event=None):
    item = selected()
    p = position.get(id(item[1])) if item else None
    for mark, (fx, fy) in zip(marks, ((0, 1), (0, 2), (2, 1))):
      mark.set_data([p[fx]] if p else [], [p[fy]] if p else [])
    canvas.draw_idle()
  tree.bind("<<TreeviewSelect>>", on_select)

  def open_event(_event=None):
    item = selected()
    if item:
      on_open_event(*item)
  tree.bind("<Double-1>", open_event)

  ttk.Button(buttons, text="震源推定を開く", command=open_event).pack(side=tk.LEFT)
  ttk.Button(buttons, text="一覧と図を保存", command=on_save).pack(side=tk.LEFT, padx=(8, 0))
  ttk.Button(buttons, text="閉じる", command=win.destroy).pack(side=tk.RIGHT)

  canvas.draw()
  return win
