# quake_locate_window.py
# 地震の候補の震源の画面: 候補ごとの結果の表と、震央の地図・東西と南北の断面図を並べて表示する
#   表の行を選ぶと地図と断面図にその震源の印を付け、ダブルクリック(または「震源推定を開く」)で
#   その候補の震源推定の画面を開く(到着時刻を手で直して確かめるため)
#   震源推定の画面で「保存して一覧に反映」を押すと、保存した到着時刻から求め直した震源で、その行と図を更新する
import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

import quake_list
import quake_locate
import shortcuts
from quake_features import USU_SUMMIT

# 表の列: (見出し, 幅 [px], 揃え)
COLUMNS = [("番号", 44, "e"), ("開始", 140, "w"), ("判定", 120, "w"), ("手直し", 52, "center"),
           ("深さ[km]", 70, "e"), ("残差[秒]", 70, "e"), ("山頂から[km]", 90, "e"), ("有珠山リスト", 80, "center"),
           ("気象庁の震源リスト", 200, "w")]

# 選んだ震源の印の色
SELECT_COLOR = "#1f77b4"

def fmt(v, spec):
  return "" if v is None else format(v, spec)

# 地震の候補の震源の画面を開く
#   parent  ... 親のウィンドウ
#   items   ... [(候補, 結果), ...](quake_locate.locate_all の結果)。手直しを反映すると、この中の結果を置き換える
#   stations, proj ... 拠点の位置と平面座標
#   title   ... 画面と図のタイトル
#   on_open_event ... (候補, 結果, 反映する関数, 画面を出す直前に呼ぶ関数, この画面) を渡すと、その候補の震源推定の画面を開く関数
#                     (この画面は、震源推定の画面を閉じたときに前に出すため)
#                     反映する関数は、求め直した結果を渡すと、その行と図を更新する
#   on_save ... 「一覧と図を保存」で呼ぶ関数(引数なし)
#   on_revert ... (候補, 結果) を渡すと、保存した到着時刻を消して自動で求め直した結果を返す関数
def open_locate_window(parent, items, stations, proj, title, on_open_event, on_save, on_revert):
  win = tk.Toplevel(parent)
  win.title(f"地震の候補の震源 - {title}")
  win.geometry("1560x900")
  # 手直しを反映したあと、一覧と図を保存していないか
  #   opening ... 震源推定の画面を開いている途中(波形の読み込み中)
  state = {"unsaved": False, "opening": False}

  # --- 左: 結果の表と操作 ---
  left = ttk.Frame(win, padding=8)
  left.pack(side=tk.LEFT, fill=tk.Y)
  summary = ttk.Label(left, wraplength=760, justify=tk.LEFT)
  summary.pack(anchor="w", pady=(0, 6))
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
  ttk.Label(left, text="行を選ぶと図に青い丸で示します。ダブルクリックで震源推定の画面を開き、到着時刻を手で直して"
                       "「保存して一覧に反映」を押すと、この一覧と図に反映します(到着時刻は output/picks に保存され、"
                       "次に開いたときやバッチ処理でも使います)。一覧の CSV と図は「一覧と図を保存」で保存します。"
                       "「有珠山リストに追加・外す」で、選んだ候補を地震リストの有珠山付近の地震に加えます(✓ の行は加え済み。"
                       "もう一度押すと外します)。",
            wraplength=760, justify=tk.LEFT, foreground="#555555").pack(anchor="w", pady=(6, 0))

  # --- 右: 震央の地図と断面図 ---
  right = ttk.Frame(win)
  right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
  fig = Figure(figsize=(9.6, 7.8))
  canvas = FigureCanvasTkAgg(fig, master=right)
  toolbar = NavigationToolbar2Tk(canvas, right)
  toolbar.update()
  canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

  # 図と表を作り直すときに変わるもの: 地図の番号・位置(結果ごと)、選んだ震源の印、表の行、地図と断面図の軸
  view = {"number": {}, "position": {}, "marks": [], "rows": {}, "axes": None}
  # 地震リストの「有珠山付近の地震」にある候補の開始(表の「有珠山リスト」の列に ✓ を付ける)
  usu = {"starts": quake_list.usu_starts()}

  # 地図と断面図を描く(地図の番号は近い地震だけ。結果ごとに、地図での位置も覚えておく)
  def draw_figure():
    fig.clear()
    drawn = quake_locate.draw_location_map(fig, items, stations, proj, quake_locate.map_title(title, items),
                                           listing=False)
    view["number"] = {id(r): i for i, (_cand, r) in enumerate(drawn["local"], 1)}
    view["position"] = {id(r): p for (_cand, r), p in zip(drawn["local"], drawn["points"])}
    view["marks"] = [ax.plot([], [], "o", ms=16, mfc="none", mec=SELECT_COLOR, mew=2.5, zorder=8)[0]
                     for ax in drawn["axes"]]
    view["axes"] = drawn["axes"]
    toolbar.update()   # ズームの履歴を消す(図を作り直したため)

  # 画面の大きさを変えたら、断面図を地図に合わせ直す(地図は縦横の縮尺をそろえるため、枠の大きさが変わる)
  def on_resize(_event):
    if view["axes"]:
      axm, axe, axn = view["axes"]
      quake_locate.align_sections(axm, axe, axn, axe.ew_space)
      canvas.draw_idle()
  canvas.mpl_connect("resize_event", on_resize)

  # 表の1行の値
  def row_values(cand, r):
    loc, q = r["loc"], cand["jma"]
    local = r["kind"] in quake_locate.LOCAL_KINDS
    depth = quake_locate.depth_short(loc) if local else ""
    summit = quake_locate.epicenter_distance(r["lat"], r["lon"], *USU_SUMMIT[:2])
    jma = f"{q['震央地名']} M{fmt(q['M'], 'g')}" if q else ""
    start = f"{cand['start']:%Y-%m-%d %H:%M:%S}"
    return [view["number"].get(id(r), ""), start, r["kind"],
            "○" if r.get("manual") else "", depth, fmt(loc["rms"], ".3f") if loc else "", fmt(summit, ".1f"),
            "✓" if start in usu["starts"] else "", jma]

  # 表を作り直す(選んでいた行は残す)
  def fill_table(select_index=None):
    tree.delete(*tree.get_children())
    view["rows"] = {}
    for k, (cand, r) in enumerate(items):
      iid = tree.insert("", tk.END, values=row_values(cand, r))
      view["rows"][iid] = k
    summary.config(text=f"{len(items)}件: {quake_locate.count_summary(items)}"
                        f"({quake_locate.manual_note(items)})" + ("  ※保存していない手直しがあります" if state["unsaved"] else ""))
    if select_index is not None:
      iid = tree.get_children()[select_index]
      tree.selection_set(iid)
      tree.see(iid)

  def selected_index():
    sel = tree.selection()
    return view["rows"].get(sel[0]) if sel else None

  # 選んだ行の震源に印を付ける(地図に描いていない遠い地震などは印を消す)
  def on_select(_event=None):
    k = selected_index()
    p = view["position"].get(id(items[k][1])) if k is not None else None
    for mark, (fx, fy) in zip(view["marks"], ((0, 1), (0, 2), (2, 1))):
      mark.set_data([p[fx]] if p else [], [p[fy]] if p else [])
    canvas.draw_idle()
  tree.bind("<<TreeviewSelect>>", on_select)

  # 候補の結果を置き換えて、表と図を更新する
  def replace(cand, new_r, unsaved=True):
    k = next((i for i, (c, _r) in enumerate(items) if c is cand), None)
    if k is None or not win.winfo_exists():
      return
    items[k] = (cand, new_r)
    state["unsaved"] = state["unsaved"] or unsaved
    draw_figure()
    fill_table(k)
    on_select()

  # 震源推定の画面を開く
  #   波形の読み込みに数秒かかる間のクリック(ダブルクリックの続きなど)がたまり、開いたあとに処理されて
  #   同じ画面がいくつも開いていたので、開いている途中のクリックは捨てる
  #   (on_open_event は、画面を出す直前に ready() を呼ぶ。たまったクリックをここで処理して捨ててから受け付けに戻す)
  def open_event(_event=None):
    if state["opening"]:
      return
    k = selected_index()
    if k is None:
      return
    cand, r = items[k]
    state["opening"] = True
    win.config(cursor="watch")
    def ready():
      if not state["opening"] or not win.winfo_exists():
        return
      win.update()
      state["opening"] = False
      win.config(cursor="")
    try:
      on_open_event(cand, r, lambda new_r: replace(cand, new_r), ready, win)
    finally:
      ready()   # 画面を開かなかったとき(波形が足りないなど)
  tree.bind("<Double-1>", open_event)

  # 選んだ候補の保存した到着時刻を消し、自動の検出で求め直す
  def revert():
    k = selected_index()
    if k is None:
      return
    cand, r = items[k]
    if not r.get("manual"):
      messagebox.showinfo("自動に戻す", "この候補は自動の検出のままです。", parent=win)
      return
    if not messagebox.askyesno("自動に戻す", f"{cand['start']:%Y-%m-%d %H:%M:%S} の保存した到着時刻"
                               f"({r['manual']})を消して、自動の検出に戻しますか?", parent=win):
      return
    replace(cand, on_revert(cand, r))

  # 選んだ候補を、地震リストの「有珠山付近の地震」に加える(すでにあれば、外すか確かめる)
  #   震源リストと対応した候補は、「既知の地震」にも加える(すでにあれば、そのまま)
  def toggle_usu():
    k = selected_index()
    if k is None:
      return
    cand, r = items[k]
    start = f"{cand['start']:%Y-%m-%d %H:%M:%S}"
    if start in usu["starts"]:
      if not messagebox.askyesno("有珠山リスト", f"{start} を有珠山付近の地震の一覧から外しますか?", parent=win):
        return
      quake_list.toggle_usu(cand, r, add=False)
    else:
      if r["kind"] not in quake_locate.LOCAL_KINDS and not messagebox.askyesno(
          "有珠山リスト", f"この候補の判定は「{r['kind']}」で、近い地震としての震源がありません。"
          "有珠山付近の地震の一覧に加えますか?", parent=win):
        return
      quake_list.toggle_usu(cand, r, add=True)
    usu["starts"] = quake_list.usu_starts()
    fill_table(k)
    on_select()

  # この画面が前に出たら、有珠山リストの印を読み直す(地震リストの画面で外したときなど)
  def on_focus(event):
    if event.widget is win:
      starts = quake_list.usu_starts()
      if starts != usu["starts"]:
        usu["starts"] = starts
        fill_table(selected_index())
  win.bind("<FocusIn>", on_focus)

  def save():
    on_save()
    state["unsaved"] = False
    fill_table(selected_index())

  # 閉じるとき、保存していない手直しがあれば確かめる
  def close():
    if state["unsaved"]:
      answer = messagebox.askyesnocancel("確認", "手直しを反映した一覧と図を保存していません。保存しますか?\n"
                                         "(到着時刻は output/picks に保存済みです)", parent=win)
      if answer is None:
        return
      if answer:
        on_save()
    win.destroy()
  win.protocol("WM_DELETE_WINDOW", close)

  #   ショートカット: 震源推定を開く ↩(Enter)、自動に戻す ⌘R、一覧と図を保存 ⌘S、閉じる ⌘W(Windows は Ctrl)
  ttk.Button(buttons, text=shortcuts.with_label("震源推定を開く", "Return", modifier=False), command=open_event).pack(side=tk.LEFT)
  ttk.Button(buttons, text=shortcuts.with_label("自動に戻す", "r"), command=revert).pack(side=tk.LEFT, padx=(8, 0))
  ttk.Button(buttons, text=shortcuts.with_label("一覧と図を保存", "s"), command=save).pack(side=tk.LEFT, padx=(8, 0))
  ttk.Button(buttons, text=shortcuts.with_label("有珠山リストに追加・外す", "u"), command=toggle_usu).pack(side=tk.LEFT, padx=(8, 0))
  shortcuts.bind(win, "u", toggle_usu)
  shortcuts.bind(win, "Return", open_event, modifier=False)
  shortcuts.bind(win, "KP_Enter", open_event, modifier=False)
  shortcuts.bind(win, "r", revert)
  shortcuts.bind(win, "s", save)
  shortcuts.bind(win, "w", close)
  ttk.Button(buttons, text="閉じる", command=close).pack(side=tk.RIGHT)

  draw_figure()
  fill_table()
  canvas.draw()
  return win
