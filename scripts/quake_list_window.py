# quake_list_window.py
# 地震リストの画面: 既知の地震・有珠山付近の地震の一覧から選んで、波形をすぐに確かめる
#   一覧の保存と CSV の取り込みは quake_list.py。波形の表示はメイン画面の実行ボタンと同じ処理(履歴には残さない)
#   ↑↓で選び、Enter(またはダブルクリック)で波形を表示する
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import quake_list
import shortcuts
from log_common import log_message
from maintenance_window import open_path


# 「地図にプロット」で書き出す CSV の名前(地震の候補の震源の画面の題名と、保存する一覧の名前にもなる)
USU_MAP_CSV = "地震リスト_有珠山付近の地震.csv"


class QuakeListWindow:
  # app ... メイン画面(execute_pipeline で波形を表示する)
  def __init__(self, parent, app):
    self.app = app
    self.lists = quake_list.load()
    self.last_wave = None   # 最後に開いた波形の画面(「前の波形を閉じる」で閉じる)

    win = tk.Toplevel(parent)
    self.win = win
    win.title("地震リスト")
    win.geometry("1000x560")
    win.protocol("WM_DELETE_WINDOW", self.close)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    # 一覧ごとのタブ
    self.notebook = ttk.Notebook(frame)
    self.notebook.pack(fill=tk.BOTH, expand=True)
    self.trees = {}
    self.kinds = []
    for kind in (quake_list.KNOWN, quake_list.USU):
      tab = ttk.Frame(self.notebook)
      self.notebook.add(tab, text=quake_list.LIST_TITLES[kind])
      self.kinds.append(kind)
      cols = quake_list.COLUMNS[kind]
      tree = ttk.Treeview(tab, columns=[c[0] for c in cols], show="headings", selectmode="browse")
      for name, width, right in cols:
        tree.heading(name, text=name, command=lambda k=kind, n=name: self.sort_by(k, n))
        tree.column(name, width=width, anchor=tk.E if right else tk.W, stretch=(name in ("メモ", "対応")))
      scroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=tree.yview)
      tree.configure(yscrollcommand=scroll.set)
      tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
      scroll.pack(side=tk.LEFT, fill=tk.Y)
      tree.bind("<Double-1>", lambda _e: self.show_selected())
      tree.bind("<Return>", lambda _e: (self.show_selected(), "break")[1])
      tree.bind("<KP_Enter>", lambda _e: (self.show_selected(), "break")[1])
      self.trees[kind] = tree
    self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.on_tab_changed())

    # 下のボタン
    bottom = ttk.Frame(frame)
    bottom.pack(fill=tk.X, pady=(8, 0))
    ttk.Button(bottom, text="波形を表示  Enter", command=self.show_selected).pack(side=tk.LEFT)
    self.image_button = ttk.Button(bottom, text=shortcuts.with_label("画像を開く", "i"), command=self.open_image)
    self.image_button.pack(side=tk.LEFT, padx=(6, 0))
    self.close_previous = tk.BooleanVar(value=True)
    ttk.Checkbutton(bottom, text="前の波形を閉じる", variable=self.close_previous).pack(side=tk.LEFT, padx=(12, 0))
    ttk.Button(bottom, text="一覧から外す", command=self.remove_selected).pack(side=tk.RIGHT)
    self.import_button = ttk.Button(bottom, text="CSV を取り込む…", command=self.import_csv)
    self.import_button.pack(side=tk.RIGHT, padx=(0, 6))
    # 有珠山付近の地震のタブだけに出す(一覧を地震の候補の CSV にして、地震の候補の震源の画面で開く)
    self.map_button = ttk.Button(bottom, text=shortcuts.with_label("地図にプロット", "p"), command=self.show_on_map)
    # メモの編集も、メモの列がある有珠山付近の地震のタブだけに出す
    self.memo_button = ttk.Button(bottom, text=shortcuts.with_label("メモを編集", "e"), command=self.edit_memo)
    ttk.Label(frame, text=f"↑↓ 選ぶ　Enter・ダブルクリック 波形を表示　{shortcuts.label('i')} 画像　"
                          f"{shortcuts.label('e')} メモ・{shortcuts.label('p')} 地図にプロット(有珠山付近の地震)　"
                          f"{shortcuts.label('Left')}{shortcuts.label('Right')} タブの切り替え　{shortcuts.label('w')} 閉じる",
              foreground="#666666").pack(anchor="w", pady=(6, 0))

    shortcuts.bind(win, "i", self.open_image)
    shortcuts.bind(win, "p", lambda: self.show_on_map() if self.current_kind() == quake_list.USU else None)
    shortcuts.bind(win, "e", lambda: self.edit_memo() if self.current_kind() == quake_list.USU else None)
    shortcuts.bind(win, "w", self.close)
    shortcuts.bind(win, "Left", lambda: self.switch_tab(-1))
    shortcuts.bind(win, "Right", lambda: self.switch_tab(+1))

    # 地震の候補を探す処理(メンテナンス画面など)が一覧に加えることがあるので、この画面が前に出たときに読み直す
    self.loaded_mtime = self.file_mtime()
    win.bind("<FocusIn>", lambda e: self.reload_if_changed() if e.widget is win else None)

    self.sort_keys = {}
    self.refresh()
    self.focus_tree()
    if not self.lists[quake_list.KNOWN] and not self.lists[quake_list.USU]:
      log_message("地震リストは空です。「CSV を取り込む…」で、地震の候補の一覧や有珠山付近の地震の一覧を取り込んでください")

  @staticmethod
  def file_mtime():
    try:
      return quake_list.app_paths.QUAKE_LISTS_FILE.stat().st_mtime
    except OSError:
      return None

  def reload_if_changed(self):
    mtime = self.file_mtime()
    if mtime != self.loaded_mtime:
      self.lists = quake_list.load()
      self.loaded_mtime = mtime
      self.refresh()

  def current_kind(self):
    return self.kinds[self.notebook.index(self.notebook.select())]

  def focus_tree(self):
    tree = self.trees[self.current_kind()]
    tree.focus_set()
    sel = tree.selection()
    if not sel and tree.get_children():
      first = tree.get_children()[0]
      tree.selection_set(first)
      tree.focus(first)

  def on_tab_changed(self):
    if self.current_kind() == quake_list.USU:
      self.map_button.pack(side=tk.RIGHT, padx=(0, 6), before=self.import_button)
      self.memo_button.pack(side=tk.LEFT, padx=(6, 0), after=self.image_button)
    else:
      self.map_button.pack_forget()
      self.memo_button.pack_forget()
    self.focus_tree()

  # 有珠山付近の地震を、地震の候補の震源の画面(地図と断面図)で開く
  #   一覧を地震の候補の CSV(quake_detect.OUTPUT_DIR/USU_MAP_CSV)に書き出し、メイン画面の
  #   「候補の一覧(CSV)を選んで震源を表示」と同じ処理で開く(前に計算した震源を使い回すかも、メイン画面の設定に従う)
  def show_on_map(self):
    import quake_detect
    self.reload_if_changed()
    if not self.lists[quake_list.USU]:
      messagebox.showinfo("地図にプロット", "有珠山付近の地震の一覧が空です。", parent=self.win)
      return
    path = quake_detect.OUTPUT_DIR / USU_MAP_CSV
    try:
      n = quake_list.write_usu_candidates(path, self.lists)
    except OSError as e:
      messagebox.showerror("エラー", f"一覧を書き出せませんでした。\n{e}", parent=self.win)
      return
    log_message(f"有珠山付近の地震 {n}件を書き出しました: {path}")
    self.app.open_quake_locations(path, parent=self.win)

  def switch_tab(self, step):
    n = len(self.kinds)
    self.notebook.select((self.notebook.index(self.notebook.select()) + step) % n)

  # 一覧の表示を作り直す
  def refresh(self):
    usu_starts = {e["start"] for e in self.lists[quake_list.USU]}
    for i, kind in enumerate(self.kinds):
      tree = self.trees[kind]
      selected = tree.selection()
      tree.delete(*tree.get_children())
      entries = self.ordered(kind)
      for idx, e in entries:
        f = dict(e["fields"])
        if kind == quake_list.KNOWN:
          f["有珠山付近"] = "○" if e["start"] in usu_starts else ""
        tree.insert("", tk.END, iid=str(idx), values=[f.get(c[0], "") for c in quake_list.COLUMNS[kind]])
      if selected and tree.exists(selected[0]):
        tree.selection_set(selected[0])
        tree.focus(selected[0])
        tree.see(selected[0])
      self.notebook.tab(i, text=f"{quake_list.LIST_TITLES[kind]} ({len(entries)})")

  # 表示の順(列の見出しを押すと、その列で並べ替える。もう一度押すと逆順)
  #   戻り値 ... [(一覧の中の番号, 地震)]
  def ordered(self, kind):
    items = list(enumerate(self.lists[kind]))
    key = self.sort_keys.get(kind)
    if key:
      name, reverse = key
      def value(item):
        text = item[1]["fields"].get(name, "")
        try:
          return (0, float(text), "")
        except ValueError:
          return (1, 0.0, text)
      items.sort(key=value, reverse=reverse)
    return items

  def sort_by(self, kind, name):
    if name == "有珠山付近":
      return
    old = self.sort_keys.get(kind)
    self.sort_keys[kind] = (name, not old[1] if old and old[0] == name else False)
    self.refresh()

  def selected(self):
    kind = self.current_kind()
    sel = self.trees[kind].selection()
    if not sel:
      return kind, None
    return kind, self.lists[kind][int(sel[0])]

  def show_selected(self):
    kind, entry = self.selected()
    if entry is None:
      return
    start, end = quake_list.display_range(entry)
    app = self.app
    for widget, text in ((app.entry_title, quake_list.title_of(kind, entry)), (app.entry_start, start), (app.entry_end, end)):
      widget.delete(0, tk.END)
      widget.insert(0, text)
    app.update_duration_display()
    previous = self.last_wave
    wave = app.execute_pipeline(return_to=self.win, add_to_history=False)
    if wave is not None:
      if self.close_previous.get() and previous is not None and previous is not wave:
        try:
          if previous.winfo_exists():
            # 閉じる処理(Matplotlib の後片付け)は画面の閉じるボタンと同じにする
            previous.event_generate("<<CloseWaveform>>")
        except tk.TclError:
          pass
      self.last_wave = wave
      # 開いた波形の画面を前に出す(波形の画面を閉じると、この一覧に戻る)
      self.win.after(200, lambda: self.bring_front(wave))

  def bring_front(self, win):
    try:
      if win.winfo_exists():
        win.lift()
        win.focus_force()
    except tk.TclError:
      pass

  def open_image(self):
    _kind, entry = self.selected()
    if entry is None:
      return
    path = quake_list.image_path(entry)
    if path is None:
      messagebox.showinfo("画像", "この地震の確認用の画像がありません(データフォルダの中に見つかりません)。", parent=self.win)
      return
    open_path(path)

  # 選んだ地震のメモを書き換える(小さな画面で入力。Enter で保存、Esc でやめる。空にするとメモを消す)
  def edit_memo(self):
    self.reload_if_changed()
    kind, entry = self.selected()
    if entry is None:
      return
    dlg = tk.Toplevel(self.win)
    dlg.title("メモを編集")
    dlg.transient(self.win)
    dlg.resizable(True, False)
    body = ttk.Frame(dlg, padding=10)
    body.pack(fill=tk.BOTH, expand=True)
    ttk.Label(body, text=f"{quake_list.LIST_TITLES[kind]}  {entry['time']}").pack(anchor="w")
    memo = tk.StringVar(value=entry.get("fields", {}).get("メモ", ""))
    field = ttk.Entry(body, textvariable=memo, width=60)
    field.pack(fill=tk.X, pady=(6, 8))
    buttons = ttk.Frame(body)
    buttons.pack(anchor="e")

    def save(_e=None):
      quake_list.set_memo(entry, memo.get())
      quake_list.save(self.lists)
      self.loaded_mtime = self.file_mtime()
      dlg.destroy()
      self.refresh()
      self.focus_tree()
      return "break"

    def cancel(_e=None):
      dlg.destroy()
      self.focus_tree()
      return "break"
    ttk.Button(buttons, text="やめる  Esc", command=cancel).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="保存  Enter", command=save).pack(side=tk.RIGHT, padx=(0, 6))
    field.bind("<Return>", save)
    field.bind("<KP_Enter>", save)
    dlg.bind("<Escape>", cancel)
    dlg.protocol("WM_DELETE_WINDOW", cancel)
    field.focus_set()
    field.icursor(tk.END)
    dlg.grab_set()

  def remove_selected(self):
    self.reload_if_changed()
    kind, entry = self.selected()
    if entry is None:
      return
    if not messagebox.askyesno("確認", f"{quake_list.LIST_TITLES[kind]}の一覧から外しますか?\n{entry['time']}",
                               parent=self.win):
      return
    quake_list.remove(self.lists, kind, entry)
    quake_list.save(self.lists)
    self.loaded_mtime = self.file_mtime()
    self.refresh()
    self.focus_tree()

  def import_csv(self):
    path = filedialog.askopenfilename(
      parent=self.win, title="取り込む一覧(CSV)を選んでください",
      initialdir=str(self.app.batch_output_dir / "quakes") if (self.app.batch_output_dir / "quakes").exists() else None,
      filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*.*")])
    if not path:
      return
    try:
      new = quake_list.read_csv(path)
    except Exception as e:
      messagebox.showerror("エラー", f"CSV を読めませんでした: {e}", parent=self.win)
      return
    if new is None or not (new[quake_list.KNOWN] or new[quake_list.USU]):
      messagebox.showwarning(
        "警告", "取り込める地震がありません。\n"
        "地震の候補の一覧(震源リストと対応した行)か、震源の一覧(「山頂からの距離[km]」の列があるもの)を選んでください。",
        parent=self.win)
      return
    self.reload_if_changed()
    from_usu = bool(new[quake_list.USU])
    lines = [f"{quake_list.LIST_TITLES[k]}: {len(new[k])}件" for k in (quake_list.KNOWN, quake_list.USU) if new[k]]
    if not messagebox.askyesno("確認", "次の地震を取り込みますか?\n" + "\n".join(lines), parent=self.win):
      return
    result = quake_list.merge(self.lists, new, keep_existing_known=from_usu)
    quake_list.save(self.lists)
    self.loaded_mtime = self.file_mtime()
    self.refresh()
    for k, (added, replaced) in result.items():
      if added or replaced:
        log_message(f"地震リスト({quake_list.LIST_TITLES[k]})に {added}件を加え、{replaced}件を置き換えました")
    self.focus_tree()

  def close(self):
    self.win.destroy()
