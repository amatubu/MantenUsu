# cf_card_window.py
# ロガー(EDR-X7000)の CF カードを読み込む(イメージにする)・初期化する画面(Mac 専用。UsuMantenViewer とは別に動かす)
#   使い方(プロジェクトのフォルダで): python3 scripts/cf_card_window.py
#
#   読み込み ... カードの中身をそのままイメージ(raw/image/<拠点名>_<日付>out_<番号>.img)にし、SHA-256 を .sha256 に書く
#               もう一度読んで同じかも確かめる(Win32 Disk Imager や dd とハッシュで比べていたのの代わり)
#               2 枚使う設定のカードなら、1 枚目のあとに取り出して、2 枚目を入れると続けて読む
#               読み終えたら、記録の期間・抜け・GPS との時刻のずれ・カードがいっぱいかを表示する(raw_to_win.py --info と同じ)
#   初期化   ... 設定値(1 KB)を書き、残りを 0xFF にする(設定/データ変換ソフトの「Setting」の代わり)。読み直して確かめる
#               設定値は、ソフトが保存した設定値のファイルを元にし、位置・標高はチャンネル表(etc/manten_ch.tbl)の値にできる
#               書く前に、カードにいま入っているもの(拠点ID・記録の有無・読み込んだイメージがあるか)を見せて確かめる
#               2 枚使う設定なら、1 枚目のあとに取り出して、2 枚目を入れると同じ設定値を続けて書く
#
#   カードを開くときに、macOS のパスワード(Touch ID)の画面が出る(1 枚ごとに 1 回。authopen。cf_card.py を参照)
#   CF カードにはファイルシステムが無いので、入れると macOS が「読み取れないディスク」と言う。「無視」を押す
#   (「初期化」は押さない)
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cf_card                                  # noqa: E402
import make_cf_image                            # noqa: E402
import raw_to_win                               # noqa: E402
from cf_card import CardError, Cancelled        # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
ETC_DIR = PROJECT_DIR / "etc"
IMAGE_DIR = PROJECT_DIR / "raw" / "image"
POLL_MS = 100            # 別スレッドの結果と進み具合を見る間隔
DISK_POLL_MS = 1500      # カードの抜き差しを見る間隔


class CfCardWindow:
  def __init__(self, root):
    self.root = root
    root.title("CF カード(読み込み・初期化)")
    self.calls = queue.Queue()     # 別スレッドから画面側で呼んでほしい関数
    self.progress = None           # (済んだ, 全体, 段階)
    self.cancel = False
    self.busy = False
    self.disks = []
    self.waiting = None            # 2 枚目を待っているとき: {"mode", "gone", ...}
    self.stations = cf_card.load_stations(ETC_DIR)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)
    self.busy_widgets = []

    # --- カード ---
    row = ttk.Frame(frame)
    row.pack(fill=tk.X)
    ttk.Label(row, text="カード:").pack(side=tk.LEFT)
    self.var_disk = tk.StringVar()
    self.cmb_disk = ttk.Combobox(row, textvariable=self.var_disk, state="readonly", width=60)
    self.cmb_disk.pack(side=tk.LEFT, padx=(4, 0))
    self.btn(row, "取り出す", self.eject_selected).pack(side=tk.LEFT, padx=(6, 0))
    self.lbl_disks = ttk.Label(frame, text="", foreground="#666666")
    self.lbl_disks.pack(anchor="w", pady=(2, 8))

    notebook = ttk.Notebook(frame)
    notebook.pack(fill=tk.X)

    # --- 読み込み ---
    rd = ttk.Frame(notebook, padding=10)
    notebook.add(rd, text="読み込み")
    row = ttk.Frame(rd)
    row.pack(fill=tk.X)
    ttk.Label(row, text="保存先:").pack(side=tk.LEFT)
    self.var_folder = tk.StringVar(value=str(IMAGE_DIR))
    ttk.Entry(row, textvariable=self.var_folder, width=55).pack(side=tk.LEFT, padx=(4, 0))
    self.btn(row, "選ぶ", self.choose_folder).pack(side=tk.LEFT, padx=(4, 0))
    self.var_read_verify = tk.BooleanVar(value=True)
    ttk.Checkbutton(rd, text="もう一度読んで、同じか確かめる(時間は倍になる)",
                    variable=self.var_read_verify).pack(anchor="w", pady=(6, 0))
    self.btn(rd, "読み込む", self.start_read).pack(anchor="w", pady=(8, 0))
    ttk.Label(rd, foreground="#666666", justify=tk.LEFT,
              text="イメージの名前は <拠点名>_<今日の日付>out_<カードの番号>.img(例: bamba_20261115out_1.img)。\n"
                   "2 枚使う設定なら、1 枚目を読み終えると止まるのでカードを取り出し、2 枚目を入れると続けて読みます。"
              ).pack(anchor="w", pady=(8, 0))

    # --- 初期化 ---
    ini = ttk.Frame(notebook, padding=10)
    notebook.add(ini, text="初期化")
    row = ttk.Frame(ini)
    row.pack(fill=tk.X)
    ttk.Label(row, text="拠点:").pack(side=tk.LEFT)
    self.var_station = tk.StringVar()
    self.cmb_station = ttk.Combobox(row, textvariable=self.var_station, state="readonly", width=30,
                                    values=[f"{s.name}({s.id})" for s in self.stations])
    self.cmb_station.pack(side=tk.LEFT, padx=(4, 0))
    self.cmb_station.bind("<<ComboboxSelected>>", lambda e: self.station_selected())
    row = ttk.Frame(ini)
    row.pack(fill=tk.X, pady=(6, 0))
    ttk.Label(row, text="設定値のファイル:").pack(side=tk.LEFT)
    self.var_setting_file = tk.StringVar()
    ttk.Entry(row, textvariable=self.var_setting_file, width=50).pack(side=tk.LEFT, padx=(4, 0))
    self.btn(row, "選ぶ", self.choose_setting_file).pack(side=tk.LEFT, padx=(4, 0))
    self.var_use_table = tk.BooleanVar(value=True)
    ttk.Checkbutton(ini, text="位置・標高はチャンネル表(etc/manten_ch.tbl)の値にする", variable=self.var_use_table,
                    command=self.station_selected).pack(anchor="w", pady=(6, 0))

    grid = ttk.Frame(ini)
    grid.pack(fill=tk.X, pady=(6, 0))
    self.fields = {}
    for i, (key, label, width) in enumerate([("id", "ID", 12), ("lat", "緯度(度 分)", 14), ("lon", "経度(度 分)", 14),
                                             ("alt", "標高 m", 8), ("channels", "チャンネル", 16),
                                             ("cards", "カードの枚数", 4), ("sensor", "Sensor check level", 6)]):
      ttk.Label(grid, text=label).grid(row=i // 4 * 2, column=i % 4, sticky="w", padx=(0, 10))
      var = tk.StringVar()
      ttk.Entry(grid, textvariable=var, width=width).grid(row=i // 4 * 2 + 1, column=i % 4, sticky="w", padx=(0, 10))
      self.fields[key] = var
    self.var_write_verify = tk.BooleanVar(value=True)
    ttk.Checkbutton(ini, text="書いたあとに読み直して確かめる(時間は倍になる)",
                    variable=self.var_write_verify).pack(anchor="w", pady=(6, 0))
    self.btn(ini, "初期化して書き込む", self.start_write).pack(anchor="w", pady=(8, 0))
    ttk.Label(ini, foreground="#666666", justify=tk.LEFT,
              text="書く前に、カードの中身(拠点ID・記録の有無・読み込んだイメージがあるか)を見せて確かめます。\n"
                   "2 枚使う設定なら、1 枚目を書き終えると止まるのでカードを取り出し、2 枚目を入れると同じ設定値を続けて書きます。"
              ).pack(anchor="w", pady=(8, 0))

    # --- 進み具合・ログ ---
    row = ttk.Frame(frame)
    row.pack(fill=tk.X, pady=(10, 0))
    self.bar = ttk.Progressbar(row, maximum=1000, length=420)
    self.bar.pack(side=tk.LEFT)
    self.lbl_progress = ttk.Label(row, text="", width=40)
    self.lbl_progress.pack(side=tk.LEFT, padx=(8, 0))
    self.btn_cancel = ttk.Button(row, text="中止", command=self.request_cancel, state=tk.DISABLED)
    self.btn_cancel.pack(side=tk.RIGHT)
    self.txt = tk.Text(frame, height=16, width=100, wrap="word")
    self.txt.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
    self.txt.configure(state=tk.DISABLED)

    self.log("CF カードを入れて、macOS が「読み取れないディスク」と言ったら「無視」を押してください(「初期化」は押さない)。")
    self.log("カードを開くときに、macOS のパスワードの画面が出ます(1 枚ごとに 1 回)。")
    self.refresh_disks()
    self.root.after(POLL_MS, self.poll)

  # ---------------------------------------------------------------- 画面の部品

  def btn(self, parent, text, command):
    b = ttk.Button(parent, text=text, command=command)
    self.busy_widgets.append(b)
    return b

  def log(self, text):
    self.txt.configure(state=tk.NORMAL)
    self.txt.insert(tk.END, text + "\n")
    self.txt.see(tk.END)
    self.txt.configure(state=tk.DISABLED)

  def set_busy(self, busy):
    self.busy = busy
    for b in self.busy_widgets:
      b.configure(state=tk.DISABLED if busy else tk.NORMAL)
    self.cmb_disk.configure(state=tk.DISABLED if busy else "readonly")
    self.btn_cancel.configure(state=tk.NORMAL if busy or self.waiting else tk.DISABLED)
    if not busy:
      self.progress = None

  def request_cancel(self):
    if self.waiting:
      self.log(f"{self.waiting['card']} 枚目を待つのをやめました。")
      self.waiting = None
      self.lbl_progress.configure(text="")
      self.btn_cancel.configure(state=tk.DISABLED)
    else:
      self.cancel = True

  def choose_folder(self):
    d = filedialog.askdirectory(initialdir=self.var_folder.get() or str(IMAGE_DIR))
    if d:
      self.var_folder.set(d)

  def choose_setting_file(self):
    p = filedialog.askopenfilename(initialdir=str(cf_card.SETTING_DIR) if cf_card.SETTING_DIR.is_dir() else None)
    if p:
      self.var_setting_file.set(p)
      self.fill_fields()

  # ---------------------------------------------------------------- 別スレッド

  # fn を別スレッドで動かし、終わったら画面側で on_done(結果) を呼ぶ(エラーは画面側でログに書く)
  def run(self, fn, on_done, on_error=None):
    self.cancel = False
    self.set_busy(True)

    def body():
      try:
        result = fn()
      except Cancelled:
        self.calls.put(lambda: self.finish_error("中止しました。", on_error))
      except (CardError, raw_to_win.RawError, make_cf_image.SettingError, OSError) as e:
        self.calls.put(lambda e=e: self.finish_error(f"エラー: {e}", on_error))
      except Exception as e:   # 思いがけないエラーも、画面に出す
        self.calls.put(lambda e=e: self.finish_error(f"エラー({type(e).__name__}): {e}", on_error))
      else:
        self.calls.put(lambda: (self.set_busy(False), on_done(result)))
    threading.Thread(target=body, daemon=True).start()

  def finish_error(self, text, on_error):
    self.set_busy(False)
    self.log(text)
    self.lbl_progress.configure(text="")
    if on_error:
      on_error()

  def report(self, done, total, stage):
    self.progress = (done, total, stage, time.monotonic())

  def poll(self):
    while True:
      try:
        self.calls.get_nowait()()
      except queue.Empty:
        break
    if self.progress:
      done, total, stage, _ = self.progress
      self.bar["value"] = 1000 * done / total if total else 0
      # total は確かめる分(もう一度読む)も含めた量なので、GB ではなく全体の割合で出す
      self.lbl_progress.configure(text=f"{stage}(全体の {100 * done / total if total else 0:.0f}%)")
    self.root.after(POLL_MS, self.poll)

  # ---------------------------------------------------------------- カードの一覧

  def refresh_disks(self):
    if not self.busy:
      try:
        disks = cf_card.list_disks()
      except CardError as e:
        disks = []
        self.lbl_disks.configure(text=str(e))
      self.update_disks(disks)
      self.check_waiting()
    self.root.after(DISK_POLL_MS, self.refresh_disks)

  def update_disks(self, disks):
    cards = [d for d in disks if d.is_card]
    old = self.selected_disk()
    if [d.node for d in cards] != [d.node for d in self.disks]:
      self.disks = cards
      self.cmb_disk.configure(values=[self.disk_label(d) for d in cards])
      keep = next((d for d in cards if old and d.node == old.node), None)
      self.var_disk.set(self.disk_label(keep or cards[0]) if cards else "")
    others = len(disks) - len(cards)
    text = "CF カードが見つかりません(カードを入れてください)" if not cards else f"CF カード {len(cards)} 枚"
    if others:
      text += f"(ほかの外付けディスク {others} 台は、大きさが違うので選べません)"
    self.lbl_disks.configure(text=text)

  @staticmethod
  def disk_label(d):
    return f"{d.node}  {d.media}  {d.size / 1e9:.2f} GB"

  def selected_disk(self):
    label = self.var_disk.get()
    return next((d for d in self.disks if self.disk_label(d) == label), None)

  def eject_selected(self):
    d = self.selected_disk()
    if d:
      self.eject(d)

  def eject(self, disk):
    try:
      cf_card.eject(disk)
      self.log(f"{disk.node} を取り出しました。")
    except CardError as e:
      self.log(f"※ {e}(カードリーダーから抜く前に、Finder などで取り出してください)")

  # 2 枚目を待っているとき: いまのカードが抜けたあと、カードが入ったら続ける
  def check_waiting(self):
    w = self.waiting
    if not w:
      return
    if not self.disks:
      w["gone"] = True
      return
    if w["gone"]:
      self.waiting = None
      self.btn_cancel.configure(state=tk.DISABLED)
      disk = self.disks[0]
      self.log(f"{w['card']} 枚目のカードが入りました({disk.node})。")
      if w["mode"] == "read":
        self.read_card(disk, w)
      else:
        self.write_card(disk, w)

  def wait_next(self, disk, session):
    self.eject(disk)
    session["card"] += 1
    session["gone"] = False
    self.waiting = session
    self.btn_cancel.configure(state=tk.NORMAL)
    self.lbl_progress.configure(text=f"{session['card']} 枚目のカードを待っています")
    self.log(f"{session['card'] - 1} 枚目のカードを抜いて、{session['card']} 枚目を入れてください"
             "(やめるときは「中止」)。")

  # ---------------------------------------------------------------- 読み込み

  def start_read(self):
    disk = self.selected_disk()
    if not disk:
      messagebox.showinfo("CF カード", "CF カードが見つかりません。")
      return
    folder = Path(self.var_folder.get())
    try:
      folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
      messagebox.showerror("CF カード", f"保存先を作れません: {e}")
      return
    self.read_card(disk, {"mode": "read", "card": 1, "folder": folder, "images": [], "station": None})

  def read_card(self, disk, session):
    folder = session["folder"]
    if shutil.disk_usage(folder).free < disk.size * 1.02:
      self.log(f"エラー: 保存先の空きが足りません({disk.size / 1e9:.1f} GB 要ります)。")
      return
    which = f"{session['card']} 枚目のカード" if session["images"] else "カード"
    self.log(f"--- {which}を読み込みます({disk.node})。パスワードの画面が出たら入力してください。")
    state = {}

    def open_card():
      state["dev"] = cf_card.Device.open(disk)
      return cf_card.card_info(state["dev"].head())

    def opened(info):
      dev = state["dev"]
      self.log(cf_card.card_summary(info))
      problem = self.read_problem(info, session)
      if problem:
        dev.close()
        self.log(f"エラー: {problem}")
        return
      # 初期化したままのカード(番号なし・記録なし)は読まない。2 枚使う設定で 2 枚目まで記録しなかったときもここ
      if not info.has_data:
        dev.close()
        self.log("このカードには記録がない(初期化したまま)ので、読み込みません。")
        self.eject(disk)
        self.show_info(session)
        return
      # カードの番号はヘッダーのもの(記録したカードには、ロガーが 01・02 と書く)。2 枚目だけを読むこともある
      index = info.index or session["card"]
      session["card"] = index
      session["cards"] = info.cards
      session["station"] = session["station"] or info.station
      name = self.station_name(info.station)
      session["name"] = name
      out = folder / cf_card.image_name(name, index)
      if out.exists() and not messagebox.askyesno("CF カード", f"{out.name} はもうあります。上書きしますか?",
                                                   default=messagebox.NO):
        dev.close()
        self.log("読み込みをやめました。")
        return
      self.log(f"{out} に読み込んでいます...")
      verify = self.var_read_verify.get()

      def read():
        try:
          return cf_card.read_card(dev, out, verify, self.report, lambda: self.cancel)
        finally:
          dev.close()

      def done(digest):
        self.log(f"読み込みました: {out.name}  SHA-256 {digest}" + ("(2 回読んで同じでした)" if verify else ""))
        session["images"].append(out)
        if index < info.cards:
          self.wait_next(disk, session)
        else:
          self.eject(disk)
          self.show_info(session)
      self.run(read, done)

    self.run(open_card, opened)

  # 読み込もうとしたカードがおかしいとき、その理由
  def read_problem(self, info, session):
    if not info.station:
      return "ロガーのカードではないようです。"
    if session["station"] and info.station != session["station"]:
      return f"1 枚目と違う拠点のカードです(1 枚目 {session['station']}、このカード {info.station})。"
    if session["card"] > 1 and info.index and info.index < session["card"]:
      return f"{info.index} 枚目のカードです({session['card']} 枚目を入れてください)。"
    return None

  @staticmethod
  def image_index(path):
    try:
      return int(path.stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
      return None

  def station_name(self, station_id):
    s = next((s for s in self.stations if s.id == station_id), None)
    return s.name if s else station_id

  # 読み込んだイメージの記録の期間など(raw_to_win.py --info と同じ)
  def show_info(self, session):
    images = list(session["images"])
    if not images:
      return
    # この回で読まなかったカード(先に読んであった 1 枚目など)も、同じ日の名前のイメージがあれば合わせて調べる
    have = {self.image_index(p) for p in images}
    for i in range(1, session.get("cards", 1) + 1):
      other = images[0].with_name(cf_card.image_name(session["name"], i))
      if i not in have and other.exists():
        self.log(f"{i} 枚目は、先に読んであった {other.name} を合わせて調べます。")
        images.append(other)
    self.log("--- 記録の範囲を調べています...")
    lines = []

    def info():
      files, start = raw_to_win.open_inputs(images)
      raw_to_win.print_info(files, start, lines.append)
      raw_to_win.print_corrections(raw_to_win.gps_corrections(files), start, True, lines.append)
      # 最後のカードがいっぱいなら、そのあとの記録は残っていない(1 枚目がいっぱいなのは、2 枚目に続くのでふつう)
      last = max(files, key=lambda f: f.header["card"])
      if last.end >= len(last.mm):
        if last.header["card"] >= session.get("cards", 1):
          lines.append(f"  ※ {last.path.name}(最後のカード)はいっぱいです。記録はカードの終わりで止まっていて、"
                       "そのあと回収までの記録はありません")
        else:
          lines.append(f"  ※ {last.path.name} はいっぱいです。続きは {last.header['card'] + 1} 枚目にあります")
      return None

    def done(_):
      for line in lines:
        self.log(line)
      self.lbl_progress.configure(text="読み込みが終わりました")
    self.run(info, done, lambda: [self.log(line) for line in lines])

  # ---------------------------------------------------------------- 初期化

  def selected_station(self):
    i = self.cmb_station.current()
    return self.stations[i] if i >= 0 else None

  def station_selected(self):
    s = self.selected_station()
    if not s:
      return
    # 設定値のファイルは、その拠点のものでなければ、いちばん新しいものにする
    if not Path(self.var_setting_file.get()).name.startswith(s.id + "_"):
      f = cf_card.latest_setting_file(s.id)
      self.var_setting_file.set(str(f) if f else "")
      if not f:
        self.log(f"※ {cf_card.SETTING_DIR} に {s.id} の設定値のファイルがありません(フォルダがつながっていない?)。")
    self.fill_fields()

  # 設定値のファイル(あれば)とチャンネル表から、入力欄を埋める
  def fill_fields(self):
    s = self.selected_station()
    base = None
    path = self.var_setting_file.get()
    if path:
      try:
        base = make_cf_image.describe(make_cf_image.read_setting(path))
      except (OSError, make_cf_image.SettingError) as e:
        self.log(f"※ 設定値のファイルが読めません: {e}")
    f = self.fields
    if base:
      f["id"].set(base["ID"])
      f["lat"].set(base["緯度"].split(" ", 1)[1].replace("°", "").replace("′", ""))
      f["lon"].set(base["経度"].split(" ", 1)[1].replace("°", "").replace("′", ""))
      f["alt"].set(base["標高"].replace(" m", ""))
      f["channels"].set(base["チャンネル"])
      f["cards"].set(str(base["カードの枚数"]))
      f["sensor"].set(str(base["Sensor check level"]))
    elif s:
      f["id"].set(s.id)
      f["channels"].set(" ".join(s.channels))
      f["cards"].set(f["cards"].get() or "2")
      if not f["sensor"].get():
        f["sensor"].set("500")
        self.log("※ 設定値のファイルが無いので、Sensor check level は 500 にしました(拠点の設定を確かめてください)。")
    if s and self.var_use_table.get() and s.lat is not None:
      d, m = cf_card.deg_min(s.lat)
      f["lat"].set(f"{d} {m:.4f}")
      d, m = cf_card.deg_min(s.lon)
      f["lon"].set(f"{d} {m:.4f}")
      f["alt"].set(f"{s.alt:.1f}")
    if s and f["id"].get() != s.id:
      self.log(f"※ 設定値のファイルの ID({f['id'].get()})が、拠点の ID({s.id})と違います。")

  def make_setting(self):
    f = {k: v.get().strip() for k, v in self.fields.items()}
    try:
      lat, lon = f["lat"].split(), f["lon"].split()
      if len(lat) != 2 or len(lon) != 2:
        raise make_cf_image.SettingError("緯度・経度は「度 分」の形で入れてください(例: 42 32.1199)")
      return make_cf_image.make_setting(f["id"], lat, lon, float(f["alt"]), f["channels"].split(),
                                        int(f["cards"]), int(f["sensor"]))
    except ValueError as e:
      raise make_cf_image.SettingError(f"数字が読めません: {e}")

  def start_write(self):
    disk = self.selected_disk()
    if not disk:
      messagebox.showinfo("CF カード", "CF カードが見つかりません。")
      return
    try:
      setting = self.make_setting()
    except make_cf_image.SettingError as e:
      messagebox.showerror("CF カード", str(e))
      return
    self.write_card(disk, {"mode": "write", "card": 1, "setting": setting})

  def write_card(self, disk, session):
    setting = session["setting"]
    desc = make_cf_image.describe(setting)
    self.log(f"--- {session['card']} 枚目を初期化します({disk.node})。パスワードの画面が出たら入力してください。")
    state = {}

    def open_card():
      state["dev"] = cf_card.Device.open(disk, write=True)
      head = state["dev"].head()
      return cf_card.card_info(head), cf_card.find_image(head, self.var_folder.get()), head

    def state_head_is(setting, result):
      return result[2][:cf_card.SETTING_SIZE] == setting

    def opened(result):
      info, images, _ = result
      dev = state["dev"]
      now = cf_card.card_summary(info)
      self.log(f"いまの中身: {now}")
      if info.has_data:
        saved = (f"このカードを読み込んだイメージ: {', '.join(images)}" if images
                 else "※ このカードを読み込んだイメージが保存先にありません。記録は消えます。")
      elif info.setting and state_head_is(setting, result):
        saved = "記録はありません(いま書こうとしている設定値と同じ中身です。初期化したカードをもう一度入れた?)。"
      else:
        saved = "記録はありません。"
      self.log(saved)
      new = "\n".join(f"  {k}: {v}" for k, v in desc.items() if v is not None)
      warn = info.has_data and not images
      ok = messagebox.askyesno(
        "CF カードの初期化",
        f"{disk.node}({disk.size / 1e9:.2f} GB)の中身を全部消して、設定値を書きます。\n\n"
        f"いまの中身: {now}\n{saved}\n\n書く設定値({session['card']} 枚目):\n{new}\n\nよろしいですか?",
        icon=messagebox.WARNING if warn else messagebox.QUESTION, default=messagebox.NO)
      if ok and warn:
        ok = messagebox.askyesno("CF カードの初期化", "読み込んでいない記録が消えます。本当に初期化しますか?",
                                 icon=messagebox.WARNING, default=messagebox.NO)
      if not ok:
        dev.close()
        self.log("初期化をやめました。")
        return
      verify = self.var_write_verify.get()

      def write():
        try:
          cf_card.write_card(dev, setting, verify, self.report, lambda: self.cancel)
        finally:
          dev.close()

      def done(_):
        self.log(f"{session['card']} 枚目を初期化しました" + ("(読み直して確かめました)" if verify else "") + "。")
        if session["card"] < desc["カードの枚数"]:
          self.wait_next(disk, session)
        else:
          self.eject(disk)
          self.lbl_progress.configure(text="初期化が終わりました")
      self.run(write, done)

    self.run(open_card, opened)


def main():
  if sys.platform != "darwin":
    print("Mac 専用です(macOS の authopen・diskutil を使う)", file=sys.stderr)
    return 1
  root = tk.Tk()
  CfCardWindow(root)
  root.mainloop()
  return 0


if __name__ == "__main__":
  sys.exit(main())
