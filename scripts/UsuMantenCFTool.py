# UsuMantenCFTool.py
# ロガー(EDR-X7000)の CF カードを読み込む(イメージにする)・初期化する・WIN に変換する画面(UsuMantenViewer の CF カードのメンテナンスツール。Mac 専用。UsuMantenViewer とは別に動かす)
#   使い方(プロジェクトのフォルダで): python3 scripts/UsuMantenCFTool.py
#   バージョンは UsuMantenViewer(APP_VERSION)とは別に付ける(TOOL_VERSION)
#
#   読み込み ... カードの中身をそのままイメージ(raw/image/<拠点名>_<記録の開始の日>in_<番号>.img)にし、SHA-256 を .sha256 に書く
#               もう一度読んで同じかも確かめる(Win32 Disk Imager や dd とハッシュで比べていたのの代わり)
#               2 枚使う設定のカードなら、1 枚目のあとに取り出して、2 枚目を入れると続けて読む
#               読み終えたら、記録の期間・抜け・GPS との時刻のずれ・カードがいっぱいかを表示する(raw_to_win.py --info と同じ)
#   初期化   ... 設定値(1 KB)を書き、残りを 0xFF にする(設定/データ変換ソフトの「Setting」の代わり)。読み直して確かめる
#               設定値は、ソフトが保存した設定値のファイルを元にし、位置・標高はチャンネル表(etc/manten_ch.tbl)の値にできる
#               書く前に、カードにいま入っているもの(拠点ID・記録の有無・読み込んだイメージがあるか)を見せて確かめる
#               2 枚使う設定なら、1 枚目のあとに取り出して、2 枚目を入れると同じ設定値を続けて書く
#
#   変換     ... 読み込んだイメージ(1 回の回収の分)を WIN にする(raw_to_win.py と同じ。変換ツールの代わり)
#               書き出し先は、拠点ごとの ZIP(source_zips/<拠点ID>_<拠点名>(YYMMDD-YYMMDD).zip。変換ツールの ZIP と同じ形)か、
#               日ごとの ZIP(daily_zips/<YYMMDD>.zip)に直接書き足す(repackage_to_daily_zip.py でまとめ直したのと同じ)
#               読み込みのあとに続けて変換することも、前に読み込んだイメージ・抽出ツールの raw を選んで変換することもできる
#               いくつかの拠点・回収の分をまとめて選ぶと、回収(拠点と記録の開始)ごとに順に変換する
#   読み込み・初期化・変換のそれぞれに、かかった時間をログに出す(読み込み・初期化は、段階ごとの速さ MB/s も)
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
import repackage_to_daily_zip                   # noqa: E402
from app_paths import DATA_DIR, ZIPS_DIR        # noqa: E402
from cf_card import CardError, Cancelled        # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
ETC_DIR = PROJECT_DIR / "etc"
IMAGE_DIR = PROJECT_DIR / "raw" / "image"
SOURCE_ZIPS_DIR = DATA_DIR / "source_zips"
TOOL_VERSION = "1.1.0"   # このツールのバージョン(UsuMantenViewer とは別)
POLL_MS = 100            # 別スレッドの結果と進み具合を見る間隔
DISK_POLL_MS = 1500      # カードの抜き差しを見る間隔


# かかった時間(秒)を「1 時間 2 分 3 秒」のように
def duration_text(sec):
  sec = int(round(sec))
  h, m, s = sec // 3600, sec // 60 % 60, sec % 60
  if h:
    return f"{h} 時間 {m} 分 {s} 秒"
  return f"{m} 分 {s} 秒" if m else f"{s} 秒"


# カードの読み書きの段階ごとの、量・時間・速さ(例: 「読み込み 4.08 GB を 4 分 32 秒(15.0 MB/s)」)
def speed_text(size, stats):
  return "、".join(f"{stage} {size / 1e9:.2f} GB を {duration_text(sec)}({size / 1e6 / sec:.1f} MB/s)"
                   for stage, sec in stats.items() if sec > 0)


class CfCardWindow:
  def __init__(self, root):
    self.root = root
    root.title(f"UsuMantenCFTool {TOOL_VERSION} — CF カード(読み込み・初期化・変換)")
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
    self.var_read_convert = tk.BooleanVar(value=True)
    ttk.Checkbutton(rd, text="読み終えたら、続けて WIN に変換する(書き出し先は「変換」タブで選ぶ)",
                    variable=self.var_read_convert).pack(anchor="w", pady=(2, 0))
    self.btn(rd, "読み込む", self.start_read).pack(anchor="w", pady=(8, 0))
    ttk.Label(rd, foreground="#666666", justify=tk.LEFT,
              text="イメージの名前は <拠点名>_<記録の開始の日>in_<カードの番号>.img(例: bamba_20260826in_1.img)。\n"
                   "2 枚使う設定なら、1 枚目を読み終えると止まるのでカードを取り出し、2 枚目を入れると続けて読みます。\n"
                   "初期化したままのカード(記録なし)は読みません。"
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

    # --- 変換 ---
    cv = ttk.Frame(notebook, padding=10)
    notebook.add(cv, text="変換")
    self.notebook = notebook
    self.tab_convert = cv
    row = ttk.Frame(cv)
    row.pack(fill=tk.X)
    ttk.Label(row, text="イメージ・raw:").pack(side=tk.LEFT, anchor="n")
    self.lst_images = tk.Listbox(row, height=5, width=60)
    self.lst_images.pack(side=tk.LEFT, padx=(4, 0))
    self.btn(row, "選ぶ", self.choose_images).pack(side=tk.LEFT, padx=(4, 0), anchor="n")
    self.convert_images = []
    ttk.Label(cv, text="書き出し先:").pack(anchor="w", pady=(8, 0))
    self.var_convert_to = tk.StringVar(value="source")
    ttk.Radiobutton(cv, variable=self.var_convert_to, value="source",
                    text=f"拠点ごとの ZIP({SOURCE_ZIPS_DIR}/<拠点ID>_<拠点名>(YYMMDD-YYMMDD).zip)"
                    ).pack(anchor="w", padx=(12, 0))
    ttk.Radiobutton(cv, variable=self.var_convert_to, value="daily",
                    text=f"日ごとの ZIP に直接書き足す({ZIPS_DIR}/YYMMDD.zip)"
                    ).pack(anchor="w", padx=(12, 0))
    self.btn(cv, "WIN に変換する", self.start_convert).pack(anchor="w", pady=(8, 0))
    ttk.Label(cv, foreground="#666666", justify=tk.LEFT,
              text="1 回の回収の分(カード 2 枚なら 2 つとも)を選びます。いくつかの拠点・回収の分をまとめて選ぶと、\n"
                   "回収ごとに順に変換します。カードのイメージ(.img)のほか、\n"
                   "Windows の抽出ツールが取り出した raw(.raw)も選べます。GPS による時刻の補正は、変換ツールと同じくします。\n"
                   "日ごとの ZIP には、もう入っている WIN ファイルは書きません。書いている間は UsuMantenViewer を閉じてください。\n"
                   "書き足した日の ZIP は、Googleドライブのフォルダに保存し直してください。"
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

  def choose_images(self):
    ps = filedialog.askopenfilenames(initialdir=str(IMAGE_DIR), filetypes=[("イメージ・raw", "*.img *.raw"), ("すべて", "*")])
    if ps:
      self.set_convert_images([Path(p) for p in ps])

  def set_convert_images(self, paths):
    self.convert_images = sorted(paths)
    self.lst_images.delete(0, tk.END)
    for p in self.convert_images:
      self.lst_images.insert(tk.END, p.name)

  def choose_setting_file(self):
    p = filedialog.askopenfilename(initialdir=str(cf_card.SETTING_DIR) if cf_card.SETTING_DIR.is_dir() else None)
    if p:
      self.var_setting_file.set(p)
      self.fill_fields()

  # ---------------------------------------------------------------- 別スレッド

  # fn を別スレッドで動かし、終わったら画面側で on_done(結果) を呼ぶ(エラーは画面側でログに書く)
  #   中止の印(self.cancel)はここでは戻さない。続けて動かす処理(カードを開く → 読む、準備 → 変換)の
  #   途中で押された「中止」が、次の処理で消えないように。戻すのは、ボタンで新しく始めるとき(start_*)
  def run(self, fn, on_done, on_error=None):
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

  # 新しい処理を始めるとき: 前の処理の「終わりました」・100% のままにしない
  def clear_progress(self, text=""):
    self.progress = None
    self.bar["value"] = 0
    self.lbl_progress.configure(text=text)

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
    self.cancel = False
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
    self.clear_progress("カードを開いています")
    state = {}

    def open_card():
      state["dev"] = cf_card.Device.open(disk)
      info = cf_card.card_info(state["dev"].head())
      state["started"] = cf_card.record_start(state["dev"]) if info.has_data else None
      return info

    def opened(info):
      dev = state["dev"]
      started = state["started"]
      self.log(cf_card.card_summary(info)
               + (f"・記録の開始 {started:%Y-%m-%d %H:%M:%S}" if started else ""))
      problem = self.read_problem(info, session, started)
      if problem:
        dev.close()
        self.log(f"エラー: {problem}")
        return
      if self.cancel:
        dev.close()
        self.log("中止しました。")
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
      session["started"] = session.get("started") or started
      out = folder / cf_card.image_name(name, index, session["started"])
      if out.exists() and not messagebox.askyesno("CF カード", f"{out.name} はもうあります。上書きしますか?",
                                                   default=messagebox.NO):
        dev.close()
        self.log("読み込みをやめました。")
        return
      self.log(f"{out} に読み込んでいます...")
      verify = self.var_read_verify.get()
      t0 = time.monotonic()
      stats = {}

      def read():
        try:
          return cf_card.read_card(dev, out, verify, self.report, lambda: self.cancel, stats)
        finally:
          dev.close()

      def done(digest):
        self.log(f"読み込みました: {out.name}  SHA-256 {digest}" + ("(2 回読んで同じでした)" if verify else "")
                 + f"。かかった時間 {duration_text(time.monotonic() - t0)}")
        self.log(f"  速さ: {speed_text(disk.size, stats)}")
        session["images"].append(out)
        if index < info.cards:
          self.wait_next(disk, session)
        else:
          self.eject(disk)
          self.show_info(session)
      self.run(read, done)

    self.run(open_card, opened)

  # 読み込もうとしたカードがおかしいとき、その理由
  def read_problem(self, info, session, started=None):
    if not info.station:
      return "ロガーのカードではないようです。"
    if session["station"] and info.station != session["station"]:
      return f"1 枚目と違う拠点のカードです(1 枚目 {session['station']}、このカード {info.station})。"
    if session.get("started") and started and started != session["started"]:
      return (f"1 枚目と記録の開始が違います(別の回収のカード?。1 枚目 {session['started']:%Y-%m-%d %H:%M:%S}、"
              f"このカード {started:%Y-%m-%d %H:%M:%S})。")
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
      other = images[0].with_name(cf_card.image_name(session["name"], i, session.get("started")))
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
      self.set_convert_images(images)
      if self.var_read_convert.get():
        if self.cancel:
          self.log("中止したので、WIN への変換はしません。")
        else:
          self.start_convert(fresh=False)
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
    self.cancel = False
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
    self.clear_progress("カードを開いています")
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
      if self.cancel:
        dev.close()
        self.log("中止しました。")
        return
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
      t0 = time.monotonic()
      stats = {}

      def write():
        try:
          cf_card.write_card(dev, setting, verify, self.report, lambda: self.cancel, stats)
        finally:
          dev.close()

      def done(_):
        self.log(f"{session['card']} 枚目を初期化しました" + ("(読み直して確かめました)" if verify else "")
                 + f"。かかった時間 {duration_text(time.monotonic() - t0)}")
        self.log(f"  速さ: {speed_text(disk.size, stats)}")
        if session["card"] < desc["カードの枚数"]:
          self.wait_next(disk, session)
        else:
          self.eject(disk)
          self.lbl_progress.configure(text="初期化が終わりました")
      self.run(write, done)

    self.run(open_card, opened)


  # ---------------------------------------------------------------- 変換

  # 選んだイメージ・raw を、回収(拠点と記録の開始)ごとに分けて、順に変換する
  #   いくつかの拠点の分をまとめて選べる(カードの読み込みだけを先にしておき、あとでまとめて変換する)
  #   書く前に、全部の回収の書き出し先をまとめて確かめる。途中で中止・エラーになったら、残りの回収はしない
  def start_convert(self, fresh=True):
    if fresh:
      self.cancel = False
    images = list(self.convert_images)
    if not images:
      messagebox.showinfo("CF カード", "変換するイメージを選んでください。")
      return
    daily = self.var_convert_to.get() == "daily"
    self.log(f"--- WIN に変換します: {', '.join(p.name for p in images)}")
    self.clear_progress("変換の準備(記録の範囲を調べています)")
    lines = []

    def prepare():
      jobs = []
      cal = raw_to_win.load_cal()
      for files, start in raw_to_win.open_groups(images, lines.append):
        header = files[0].header
        name = self.station_name(header["station"])
        lines.append(f"{name}({header['station']}): {', '.join(f.path.name for f in files)}")
        offsets, gains = raw_to_win.calibration_for(header, cal, lines.append)
        corrections = raw_to_win.gps_corrections(files)
        raw_to_win.print_corrections(corrections, start, True, lines.append)
        a, b = raw_to_win.record_range(files, start)
        jobs.append(dict(files=files, start=start, header=header, offsets=offsets, gains=gains,
                         corrections=corrections, name=name, period=f"{a:%Y-%m-%d %H:%M} 〜 {b:%Y-%m-%d %H:%M}",
                         target=ZIPS_DIR if daily else
                         SOURCE_ZIPS_DIR / raw_to_win.source_zip_name(header["station"], name, files, start)))
      return jobs

    def stop(text="変換をやめました。"):
      self.log(text)
      self.lbl_progress.configure(text="")

    def prepared(jobs):
      for line in lines:
        self.log(line)
      if self.cancel:
        return stop("中止しました。")
      plan = "\n".join(f"  {j['name']}({j['header']['station']}): {j['period']}" for j in jobs)
      if daily:
        ok = messagebox.askyesno("CF カード", f"次の WIN を、\n{ZIPS_DIR} の日ごとの ZIP に書き足します。\n\n{plan}\n\n"
                                 "UsuMantenViewer を開いていたら、先に閉じてください。続けますか?")
      else:
        ok = messagebox.askyesno("CF カード", "次の WIN を、拠点ごとの ZIP に書きます。\n\n"
                                 + "\n".join(f"{p}\n    → {j['target']}" for p, j in zip(plan.split("\n"), jobs))
                                 + "\n\n続けますか?")
        exist = [j["target"] for j in jobs if j["target"].exists()]
        if ok and exist:
          ok = messagebox.askyesno("CF カード", "次の ZIP はもうあります。上書きしますか?\n\n"
                                   + "\n".join(p.name for p in exist), default=messagebox.NO)
      if not ok:
        return stop()
      self.convert_jobs(jobs, daily)
    self.run(prepare, prepared, lambda: [self.log(line) for line in lines])

  # 回収ごとに順に変換する(1 つ終わったら次)
  def convert_jobs(self, jobs, daily):
    t_all = time.monotonic()
    appended = set()   # 日ごとの ZIP に書き足した時間(全部の回収の分)
    new_days = set()   # この回で新しくできた日(あとの回収がその日に書いても、前からある日とはしない)
    added = False

    def finish():
      if daily:
        if appended:
          stale = repackage_to_daily_zip.stale_caches(appended)
          if stale:
            self.log("※ 書き足した日について、アプリが前の ZIP から作った次の結果は古いまま使われます。"
                     "アプリを閉じて、消してください:")
            for p in stale:
              self.log(f"    {p}{'/' if p.is_dir() else ''}")
          self.log("※ 地震の候補の検出は、書き足した日を含めて探し直してください。")
        if added:
          self.log("新しくできた日・書き足した日の ZIP は、Googleドライブのフォルダに保存してください。")
      if len(jobs) > 1:
        self.log(f"--- {len(jobs)} 個の回収を変換しました。かかった時間(全体) {duration_text(time.monotonic() - t_all)}")
      self.lbl_progress.configure(text="変換が終わりました")

    def start(i):
      if i == len(jobs):
        return finish()
      j = jobs[i]
      if self.cancel:
        self.log("中止しました。")
        self.lbl_progress.configure(text="")
        return
      stage = f"変換 {j['name']}" + (f"({i + 1}/{len(jobs)})" if len(jobs) > 1 else "")
      if daily:
        writer = raw_to_win.DailyZipWriter(ZIPS_DIR)
      else:
        if j["target"].exists():
          j["target"].unlink()
        writer = raw_to_win.ZipWriter(j["target"], j["header"]["station"])
      self.log(f"{j['name']} の {j['period']} を {j['target']} に書いています...")
      self.clear_progress(f"{stage}(全体の 0%)")
      lines = []
      t0 = time.monotonic()

      def progress(done, total):
        if self.cancel:
          raise Cancelled()
        self.report(done, total, stage)

      def work():
        return raw_to_win.convert(j["files"], j["start"], j["header"], j["offsets"], j["gains"], writer,
                                  j["corrections"], progress, lines.append)

      def done(w):
        nonlocal added
        for line in lines:
          self.log(line)
        self.log(f"変換しました: {j['name']} の WIN ファイル {w.count} 個 → {j['target']}。"
                 f"かかった時間 {duration_text(time.monotonic() - t0)}")
        if daily:
          for d in new_days & set(w.appended):
            del w.appended[d]
          new_days.update(d for d in w.added if d not in w.appended)
          raw_to_win.print_daily_result(w, self.log)
          appended.update(*w.appended.values())
          added = added or bool(w.added)
        start(i + 1)

      def failed():
        for line in lines:
          self.log(line)
        if daily and writer.added:
          self.log(f"※ {j['name']} の書き終えた日の ZIP はそのまま残っています: {', '.join(sorted(writer.added))}"
                   "(もう一度変換すると、残りの分だけを書きます)")
        rest = [x["name"] for x in jobs[i + 1:]]
        if rest:
          self.log(f"※ 残りの {', '.join(rest)} は変換していません。")
      self.run(work, done, failed)
    start(0)


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
