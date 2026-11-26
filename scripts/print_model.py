# print_model.py(試作)
# 3Dプリント用の模式的な模型(山体・地下の層・震源・P波の通り道・拠点・川)を、部品ごと・色ごとの STL に書き出す
#   --layout cake(既定): 山頂と昭和新山を通る東西の面と、山頂から発破とみられる震源への面で、ケーキのように3つに切る。
#       切り口に震源(赤の球)と、満点の拠点への P波の通り道(白の管。切り口に写したもの)を置く。発破の通り道は、山頂の位置で
#       折り返して東西の切り口へ続く。通り道は地表より少し下で止まり、底の近くでは途切れる。形の足し引きに manifold3d を使う
#   --layout core: 前の形。1 mm の柱に色を積み、東西の面で南北に割り、発破はコア(引き抜ける円柱)に入れる
#   出力: batch_plots/print_model/<名前>/ に STL と、確認用の preview.html(plotly.js)、説明の README.txt
#       Bambu Studio では、部品ごとに STL をまとめて読み込み、「1つの物体の複数のパーツとして読み込む」を選んで、
#       パーツごとにフィラメントを割り当てる
#   使い方: python3 scripts/print_model.py --above 1.5 --south 6 [--scale 20] [--exaggeration 1.0] [--rays manten] [--name 名前]
#     --exaggeration は縦の倍率(全体)。--above は海面より上だけの倍率(深さはそのまま)。--south は東西の切り口から南の奥行き [km]
#     --magnets [--magnet-size 5x2] [--magnet-z 5,25]: 合わせ面に磁石を閉じ込める U字の溝を開ける(cake のとき。TODO 47)。
#       一時停止の高さと磁石の向きは README.txt、溝の位置の図は magnets.png
import argparse
import json
import math
import struct
import tempfile
from pathlib import Path

import numpy as np

import app_paths
import gsi_tiles
import quake_3d_view
import velocity_model
from hypocenter import LocalProjection, load_stations

SCALE_MM_PER_KM = 20.0     # 1 km を何 mm にするか
CELL_MM = 1.0              # 柱の大きさ [mm](地形の細かさ。1 mm で 50 m)
PREVIEW_CELL_MM = 2.0      # 確認用の HTML は、柱を大きくして作り直す(三角形を減らして、ブラウザで軽く開けるように)
BOTTOM_KM = 2.0            # ブロックの底(海面からの深さ [km])
X_RANGE_KM = (-6.0, 5.0)   # 東西の範囲(拠点の中心から [km])
HALF_DEPTH_KM = 4.0        # 断面から南北それぞれの奥行き [km]
WATER_MM = 0.6             # 湖・海の水面の色の厚さ [mm]
LAND_SKIN_MM = 0.6         # 陸の地表の山体の色の厚さの最小 [mm](海岸の低い陸で、山体が薄すぎて下の層の色が地表に出ないように)
QUAKE_RADIUS_MM = 3.0      # 震源の球の半径 [mm]
RAY_WIDTH_MM = 0.8         # 通り道の線の太さ [mm](断面の上の高さ方向)
RAY_DEPTH_CELLS = 1        # 通り道を断面から何列の柱に入れるか
RAY_MAX_OFFSET_KM = 1.6    # 断面からこの距離より近い拠点への通り道だけ描く
RAY_MAX_SLOPE = 0.5        # (ケーキの形)拠点の切り口からの距離が、切り口に沿った震源からの距離のこの割合より大きい通り道は描かない
                           # (震源のほぼ真横の拠点への通り道は、切り口に写すとほぼ縦の線になり、山頂の印のように見えるため)
PIN_RADIUS_MM = 1.6        # 拠点の印の半径 [mm]
PIN_HEIGHT_MM = 2.0        # 拠点の印の高さ [mm]
CORE_RADIUS_MM = 5.0       # コアの半径 [mm]
CORE_HOLE_MM = 5.8         # コアを入れる穴の半径 [mm](すき間 0.8 mm)
CORE_CAP_MM = 0.6          # コアの上の印(採石場など、地表の場所)の厚さ [mm]

# 層の境目(海面からの深さ [km])。上から 0〜0.5、0.5〜1、1〜底
LAYERS = [(0.0, 0.5, "layer1"), (0.5, 1.0, "layer2"), (1.0, BOTTOM_KM, "layer3")]

# 色(部品の名前, 説明, 確認用の色)。7色まで
MATERIALS = {
  "mountain": ("山体(海面より上)", "#c9b48a"),
  "water":    ("湖・海の水面", "#4a90d9"),
  "layer1":   ("海面下 0〜0.5 km(P波 2.4〜3.0 km/s)", "#b9d7a6"),
  "layer2":   ("海面下 0.5〜1 km(P波 3.0〜3.5 km/s)", "#6fae94"),
  "layer3":   ("海面下 1 km〜底(P波 3.5〜4.7 km/s)", "#2f6f74"),
  "quake":    ("震源", "#d62728"),
  "marker":   ("拠点・P波の通り道(core の形ではコアの上の印も)", "#f5f5f5"),
}

PREVIEW_COLORS = {**MATERIALS, "magnet": ("磁石の溝(確認用。STL には無い)", "#e000e0")}

# 代表の地震(有珠山付近の地震の一覧の開始。分まで)。断面の上に置くものと、コアに入れるもの
DEFAULT_SECTION_QUAKES = ["2026-05-20 23:51", "2025-09-03 23:23"]   # 火口原直下、昭和新山の下
DEFAULT_CORE_QUAKES = ["2026-07-08 11:20"]                          # 発破とみられるもの
SHOWA_SHINZAN = (42.5427, 140.8644)


# --- 柱の積み重ね ---

class Columns:
  """平面を柱に分け、柱ごとに下からの色の区間 [z0, z1, 色] を持つ(z は底からの mm)"""
  def __init__(self, nx, ny):
    self.nx, self.ny = nx, ny
    self.stacks = [[[] for _ in range(nx)] for _ in range(ny)]

  def paint(self, i, j, z0, z1, mat):
    """柱 (i: 東西, j: 南北) の z0〜z1 を色 mat にする(柱の外にはみ出す分は塗らない)"""
    stack = self.stacks[j][i]
    if not stack or z1 <= z0:
      return
    z0, z1 = max(z0, stack[0][0]), min(z1, stack[-1][1])
    if z1 <= z0:
      return
    out = []
    for a, b, m in stack:
      if b <= z0 or a >= z1:
        out.append([a, b, m])
        continue
      if a < z0:
        out.append([a, z0, m])
      if b > z1:
        out.append([z1, b, m])
    out.append([z0, z1, mat])
    out.sort()
    self.stacks[j][i] = out

  def intervals(self, mat):
    """色ごとの区間 {(i, j): [(z0, z1), ...]}(となり合う同じ色はつなぐ)"""
    res = {}
    for j in range(self.ny):
      for i in range(self.nx):
        segs = []
        for a, b, m in self.stacks[j][i]:
          if m != mat:
            continue
          if segs and abs(segs[-1][1] - a) < 1e-9:
            segs[-1][1] = b
          else:
            segs.append([a, b])
        if segs:
          res[(i, j)] = segs
    return res


def subtract(seg, others):
  """区間 seg から、区間の並び others を除いた残り"""
  parts = [list(seg)]
  for a, b in others:
    nxt = []
    for p0, p1 in parts:
      if b <= p0 or a >= p1:
        nxt.append([p0, p1])
        continue
      if a > p0:
        nxt.append([p0, a])
      if b < p1:
        nxt.append([b, p1])
    parts = nxt
  return [p for p in parts if p[1] - p[0] > 1e-9]


def mesh(cols, mat, x0, y0, cell):
  """色 mat の閉じた形の三角形 (n, 3, 3) [mm]。柱の上下の面と、となりと違う所の側面
  4つの柱が接する角の縦の線の上で、どの面の辺もそろうように、側面をその線の上のすべての高さで区切る
  (辺の途中に別の面の角が来ない形にする。スライサーで「多様体でない」と言われないように)"""
  iv = cols.intervals(mat)
  # 角(i, j)(柱の左下の角が (i, j))ごとの、面の角が来る高さ
  breaks = {}
  for (i, j), segs in iv.items():
    for ci, cj in ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)):
      b = breaks.setdefault((ci, cj), set())
      for z0, z1 in segs:
        b.add(z0)
        b.add(z1)
  tris = []
  def quad(a, b, c, d):
    tris.append((a, b, c))
    tris.append((a, c, d))
  def wall(pa, pb, w0, w1):
    """角 pa から角 pb への辺の上の、高さ w0〜w1 の側面(外から見て pa が左)。両端の縦の線を区切りの高さで分ける"""
    za = sorted(z for z in breaks.get(pa, ()) if w0 - 1e-9 <= z <= w1 + 1e-9)
    zb = sorted(z for z in breaks.get(pb, ()) if w0 - 1e-9 <= z <= w1 + 1e-9)
    za = sorted(set([w0, w1] + za))
    zb = sorted(set([w0, w1] + zb))
    xa, ya = x0 + pa[0] * cell, y0 + pa[1] * cell
    xb, yb = x0 + pb[0] * cell, y0 + pb[1] * cell
    A = [(xa, ya, z) for z in za]
    B = [(xb, yb, z) for z in zb]
    # 2本の縦の線の点を下から順に結ぶ(はしごの形の三角形)
    ia = ib = 0
    while ia < len(A) - 1 or ib < len(B) - 1:
      if ib < len(B) - 1 and (ia == len(A) - 1 or B[ib + 1][2] <= A[ia + 1][2]):
        tris.append((A[ia], B[ib], B[ib + 1]))
        ib += 1
      else:
        tris.append((A[ia], B[ib], A[ia + 1]))
        ia += 1
  for (i, j), segs in iv.items():
    xa, xb = x0 + i * cell, x0 + (i + 1) * cell
    ya, yb = y0 + j * cell, y0 + (j + 1) * cell
    for z0, z1 in segs:
      quad((xa, ya, z1), (xb, ya, z1), (xb, yb, z1), (xa, yb, z1))   # 上(外向きは +z)
      quad((xa, ya, z0), (xa, yb, z0), (xb, yb, z0), (xb, ya, z0))   # 下
    # 側面: 東・西・北・南のとなりにない区間の部分(外から見て左の角 → 右の角)
    for di, dj, pa, pb in ((1, 0, (i + 1, j), (i + 1, j + 1)), (-1, 0, (i, j + 1), (i, j)),
                           (0, 1, (i + 1, j + 1), (i, j + 1)), (0, -1, (i, j), (i + 1, j))):
      nb = iv.get((i + di, j + dj), [])
      for z0, z1 in segs:
        for w0, w1 in subtract((z0, z1), nb):
          wall(pa, pb, w0, w1)
  return np.array(tris, dtype=np.float32).reshape(-1, 3, 3)


def write_stl(path, tris, name):
  """バイナリ STL(法線は三角形の向きから求める)"""
  n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
  ln = np.linalg.norm(n, axis=1, keepdims=True)
  n = np.where(ln > 0, n / np.where(ln > 0, ln, 1), 0).astype(np.float32)
  rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
  rec["n"], rec["v"] = n, tris
  with open(path, "wb") as f:
    f.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
    f.write(struct.pack("<I", len(tris)))
    f.write(rec.tobytes())


# --- 模型 ---

def representative(starts):
  """有珠山付近の地震の一覧から、開始(分まで)が starts のものの (候補, 震源の結果)"""
  import quake_list
  import quake_locate
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "usu.csv"
    quake_list.write_usu_candidates(p)
    cands = [c for c in quake_locate.read_candidates(p) if f"{c['start']:%Y-%m-%d %H:%M}" in starts]
  items, _ = quake_locate.locate_all_cached(cands, quake_3d_view.load_hour_from_zips, stations, proj)
  return items


#   exaggeration ... 縦の倍率(海面より下)。above ... 海面より上の縦の倍率(None なら exaggeration と同じ)
def build(section_starts, core_starts, scale=SCALE_MM_PER_KM, exaggeration=1.0, above=None):
  above = exaggeration if above is None else above
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  model = velocity_model.MODELS[velocity_model.DEFAULT]
  sx, sy = proj.to_xy(*quake_3d_view.USU_SUMMIT)
  _, ssy = proj.to_xy(*SHOWA_SHINZAN)
  y_cut = (sy + ssy) / 2   # 山頂と昭和新山を通る東西の面
  cell_km = CELL_MM / scale
  nx = int(round((X_RANGE_KM[1] - X_RANGE_KM[0]) / cell_km))
  ny = int(round(HALF_DEPTH_KM / cell_km))
  sea_mm = BOTTOM_KM * scale * exaggeration
  def zmm(depth_km):
    """海面からの深さ [km](上は負) → 底からの高さ [mm]。海面の上と下で縦の倍率が違ってもよい"""
    if depth_km >= 0:
      return (BOTTOM_KM - depth_km) * scale * exaggeration
    return sea_mm - depth_km * scale * above

  # 地形(柱の中心の標高)。南の部品は断面の南、北の部品は断面の北
  xs = X_RANGE_KM[0] + (np.arange(nx) + 0.5) * cell_km
  pieces = {}
  for name, sign in (("south", -1), ("north", 1)):
    ys = y_cut + sign * (np.arange(ny) + 0.5) * cell_km
    if sign < 0:
      ys = ys[::-1]   # j が北向きに増えるように
    gx, gy = np.meshgrid(xs, ys)
    lat, lon = proj.to_latlon(gx, gy)
    h = gsi_tiles.elevations(lat, lon) / 1000.0
    sea = ~np.isfinite(h)
    h = np.where(sea, 0.0, h)
    water = sea | quake_3d_view.lake_mask(h, sea)
    cols = Columns(nx, ny)
    for j in range(ny):
      for i in range(nx):
        top = sea_mm + max(h[j, i], 0.0) * scale * above
        stack = [[zmm(b), zmm(a), m] for a, b, m in LAYERS]
        stack.sort()
        if top > sea_mm + 1e-6:
          stack.append([sea_mm, top, "mountain"])
        cols.stacks[j][i] = stack
        if water[j, i]:
          cols.paint(i, j, top - WATER_MM, top, "water")
    pieces[name] = {"cols": cols, "ys": ys, "h": h,
                    "y0_mm": (ys[0] - cell_km / 2 - (y_cut - HALF_DEPTH_KM)) * scale}

  def cell_of(x, y):
    """(部品の名前, i, j)。範囲の外なら None"""
    i = int(math.floor((x - X_RANGE_KM[0]) / cell_km))
    name = "south" if y < y_cut else "north"
    j = int(math.floor((y - y_cut) / cell_km)) if name == "north" else ny - 1 - int(math.floor((y_cut - y) / cell_km))
    if not (0 <= i < nx and 0 <= j < ny):
      return None
    return name, i, j

  def ball(cols, ci, cj, zc, r_mm, mat, only=None):
    """柱 (ci, cj) を中心に、半径 r_mm の球を塗る(only の部品の範囲だけ)"""
    rc = int(math.ceil(r_mm / CELL_MM))
    for dj in range(-rc, rc + 1):
      for di in range(-rc, rc + 1):
        i, j = ci + di, cj + dj
        if not (0 <= i < nx and 0 <= j < ny):
          continue
        d2 = (di * CELL_MM) ** 2 + (dj * CELL_MM) ** 2
        if d2 > r_mm ** 2:
          continue
        dz = math.sqrt(r_mm ** 2 - d2)
        cols.paint(i, j, zc - dz, zc + dz, mat)

  notes = []
  # 断面の震源: 断面の上(両方の部品に半分ずつ)に球を置き、断面に近い拠点への P波の通り道を断面の列に塗る
  for cand, r in representative(section_starts):
    qx, qy = proj.to_xy(r["lat"], r["lon"])
    depth = r["loc"]["depth"]
    i = int(math.floor((qx - X_RANGE_KM[0]) / cell_km))
    zc = zmm(depth)
    for name, p in pieces.items():
      # 断面に接する列(南の部品は北の端、北の部品は南の端)からの球。中心は断面の上
      edge = ny - 1 if name == "south" else 0
      inward = -1 if name == "south" else 1
      rc = int(math.ceil(QUAKE_RADIUS_MM / CELL_MM))
      for dj in range(rc + 1):
        for di in range(-rc, rc + 1):
          ii, jj = i + di, edge + inward * dj
          if not (0 <= ii < nx and 0 <= jj < ny):
            continue
          d2 = (di * CELL_MM) ** 2 + ((dj + 0.5) * CELL_MM) ** 2
          if d2 <= QUAKE_RADIUS_MM ** 2:
            dz = math.sqrt(QUAKE_RADIUS_MM ** 2 - d2)
            p["cols"].paint(ii, jj, zc - dz, zc + dz, "quake")
      # 通り道(断面に投影して、断面に接する列に塗る)
      for nm in sorted(r.get("picks") or {}):
        if nm not in stations:
          continue
        la, lo, el = stations[nm]
        px, py = proj.to_xy(la, lo)
        if abs(py - y_cut) > RAY_MAX_OFFSET_KM:
          continue
        dist = math.hypot(qx - px, qy - py)
        path = velocity_model.ray_path(model, "P", el / 1000.0, dist, depth)
        ux = (qx - px) / dist if dist > 0 else 0.0
        pts = [(px + ux * pr, pz) for pr, pz in path]
        for (xa, za), (xb, zb) in zip(pts, pts[1:]):
          steps = max(2, int(abs(xb - xa) / cell_km * 2) + 2)
          for t in np.linspace(0, 1, steps):
            x = xa + (xb - xa) * t
            z = zmm(za + (zb - za) * t)
            ii = int(math.floor((x - X_RANGE_KM[0]) / cell_km))
            if not (0 <= ii < nx):
              continue
            for dj in range(RAY_DEPTH_CELLS):
              jj = edge + inward * dj
              stack = p["cols"].stacks[jj][ii]
              # 地表より上には描かない(拠点の手前で地表に出たら止める)
              top = stack[-1][1] if stack else 0
              p["cols"].paint(ii, jj, z - RAY_WIDTH_MM / 2, min(z + RAY_WIDTH_MM / 2, top), "marker")
    notes.append(f"断面: {cand['start']:%Y-%m-%d %H:%M} 深さ {depth:.2f} km、東西 {qx:+.2f} km、断面から {qy - y_cut:+.2f} km")

  # 拠点の印(地表の上に出る円柱。満点の拠点)
  for nm, (la, lo, el) in stations.items():
    px, py = proj.to_xy(la, lo)
    c = cell_of(px, py)
    if c is None:
      continue
    name, ci, cj = c
    cols = pieces[name]["cols"]
    rc = int(math.ceil(PIN_RADIUS_MM / CELL_MM))
    for dj in range(-rc, rc + 1):
      for di in range(-rc, rc + 1):
        i, j = ci + di, cj + dj
        if 0 <= i < nx and 0 <= j < ny and (di ** 2 + dj ** 2) * CELL_MM ** 2 <= PIN_RADIUS_MM ** 2:
          stack = cols.stacks[j][i]
          top = stack[-1][1]
          stack.append([top, top + PIN_HEIGHT_MM, "marker"])

  # コア: 震源の真上の円柱を部品から抜き、別の部品にする。中に震源の球、上に地表の印
  cores = []
  for cand, r in representative(core_starts):
    qx, qy = proj.to_xy(r["lat"], r["lon"])
    c = cell_of(qx, qy)
    if c is None:
      continue
    name, ci, cj = c
    cols = pieces[name]["cols"]
    rc = int(math.ceil(CORE_HOLE_MM / CELL_MM))
    core = Columns(2 * rc + 1, 2 * rc + 1)
    for dj in range(-rc, rc + 1):
      for di in range(-rc, rc + 1):
        i, j = ci + di, cj + dj
        if not (0 <= i < nx and 0 <= j < ny):
          continue
        d2 = (di ** 2 + dj ** 2) * CELL_MM ** 2
        if d2 <= CORE_RADIUS_MM ** 2:
          core.stacks[dj + rc][di + rc] = [list(s) for s in cols.stacks[j][i]]
        if d2 <= CORE_HOLE_MM ** 2:
          cols.stacks[j][i] = []
    for dj in range(-rc, rc + 1):
      for di in range(-rc, rc + 1):
        st = core.stacks[dj + rc][di + rc]
        if st:
          core.paint(di + rc, dj + rc, st[-1][1] - CORE_CAP_MM, st[-1][1], "marker")
    depth = r["loc"]["depth"]
    ball(core, rc, rc, zmm(depth), QUAKE_RADIUS_MM, "quake")
    cores.append({"cols": core, "i0": ci - rc, "j0": cj - rc, "piece": name})
    notes.append(f"コア: {cand['start']:%Y-%m-%d %H:%M} 深さ {depth:.2f} km、東西 {qx:+.2f} km、断面から {qy - y_cut:+.2f} km"
                 f"(コアの直径 {2 * CORE_RADIUS_MM:.0f} mm、穴 {2 * CORE_HOLE_MM:.1f} mm)")

  size = (nx * CELL_MM, 2 * ny * CELL_MM, sea_mm + float(np.nanmax([p["h"].max() for p in pieces.values()])) * scale * above)
  return pieces, cores, notes, size, ny


def write_model(out_dir, pieces, cores, notes, size, ny, scale, exaggeration, above=None):
  above = exaggeration if above is None else above
  out_dir.mkdir(parents=True, exist_ok=True)
  files, preview = [], []
  parts = [(name, p["cols"], 0.0, (0 if name == "south" else ny) * CELL_MM) for name, p in pieces.items()]
  for k, c in enumerate(cores, 1):
    j0 = c["j0"] + (0 if c["piece"] == "south" else ny)
    parts.append((f"core{k}", c["cols"], c["i0"] * CELL_MM, j0 * CELL_MM))
  for name, cols, x0, y0 in parts:
    for mat in MATERIALS:
      tris = mesh(cols, mat, x0, y0, CELL_MM)
      if len(tris) == 0:
        continue
      fn = f"{name}_{list(MATERIALS).index(mat) + 1}_{mat}.stl"
      write_stl(out_dir / fn, tris, f"{name} {mat}")
      files.append((fn, len(tris)))
      preview.append((name, mat, tris))
  lines = ["3Dプリントの模型(試作)", "",
           f"大きさ: 東西 {size[0]:.0f} mm × 南北 {size[1]:.0f} mm × 高さ {size[2]:.0f} mm(1 km = {scale:g} mm、縦の倍率 海面より下 {exaggeration:g}・上 {above:g})",
           f"部品: south(断面の南)、north(断面の北)" + ("、core(引き抜けるコア)" if cores else ""), "",
           "色(ファイル名の番号 = フィラメントの番号の案):"]
  lines += [f"  {k}. {mat}: {desc}" for k, (mat, (desc, _c)) in enumerate(MATERIALS.items(), 1)]
  lines += ["", "地震:"] + [f"  {n}" for n in notes]
  lines += ["", "Bambu Studio: 部品ごとに STL をまとめて読み込み、「1つの物体の複数のパーツとして読み込む」を選ぶ。",
            "  パーツごとにフィラメントを割り当てる。south・north は、断面を下にして置くと断面がきれいに仕上がる。",
            "  座標はすべて同じ基準(mm)なので、読み込んだ位置のまま重なる。", "",
            f"{gsi_tiles.DEM_ATTRIBUTION}。地下の層と P波の通り道は、震源を求めるのに使う深さで速度が変わる構造(模式的なもの)", "",
            "ファイル(三角形の数):"] + [f"  {fn} ({n})" for fn, n in files]
  (out_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
  return files


def write_preview(path, preview, ny, offsets=None, max_tris=1_500_000):
  """確認用の HTML: 部品を少し離して並べる(既定は北の部品を北へ、コアを上へ。offsets で部品ごとに指定)
  三角形が max_tris より多いときは、間引いて軽くする(見た目の確認用)"""
  data = []
  total = sum(len(t) for _n, _m, t in preview)
  step = max(1, int(math.ceil(total / max_tris)))
  for name, mat, tris in preview:
    tris = tris[::step] if len(tris) > 20000 else tris
    v = tris.reshape(-1, 3).astype(float).copy()
    if offsets is not None:
      v += np.array(offsets.get(name, (0, 0, 0)), float)
    elif name == "north":
      v[:, 1] += 15
    elif name.startswith("core"):
      v[:, 2] += 40
    n = len(tris)
    data.append({"type": "mesh3d", "name": f"{name} {PREVIEW_COLORS[mat][0]}", "x": np.round(v[:, 0], 2).tolist(),
                 "y": np.round(v[:, 1], 2).tolist(), "z": np.round(v[:, 2], 2).tolist(),
                 "i": list(range(0, 3 * n, 3)), "j": list(range(1, 3 * n, 3)), "k": list(range(2, 3 * n, 3)),
                 "color": PREVIEW_COLORS[mat][1], "flatshading": True, "showlegend": True,
                 "lighting": {"ambient": 0.55, "diffuse": 0.7, "specular": 0.05}, "hoverinfo": "name"})
  layout = {"title": {"text": "3Dプリントの模型(試作)の確認 [mm]"}, "margin": {"l": 0, "r": 0, "t": 40, "b": 0},
            "scene": {"aspectmode": "data", "camera": {"eye": {"x": -0.6, "y": -1.6, "z": 0.9}}}}
  page = (f"<!DOCTYPE html><html lang=\"ja\"><head><meta charset=\"utf-8\"><title>模型の確認</title>"
          f"<script src=\"{quake_3d_view.PLOTLY_URL}\"></script><style>html,body{{margin:0;height:100%;background:white}}"
          f"#v{{width:100%;height:100%}}</style></head><body><div id=\"v\"></div><script>"
          f"Plotly.newPlot('v',{json.dumps(data, separators=(',', ':'))},{json.dumps(layout, ensure_ascii=False)},"
          f"{{responsive:true,displaylogo:false}});</script></body></html>")
  path.write_text(page, encoding="utf-8")


# --- ケーキのように3つに切る形(manifold3d で形を足し引きする) ---
#   山頂と昭和新山を通る東西の面で南北に割り、南の側をさらに山頂から発破とみられる震源への面で2つに割る
#   (北・南西のくさび・南〜南東の3つ)。地形はなめらかな面、震源は球、P波の通り道は細い管で、切り口の面の上に置く
#   (震央の地表には印を付けない。観測の拠点の印とまぎらわしいため)
#   (切り口の両側の部品に半分ずつ入る)。切り口から RAY_MAX_OFFSET_KM より近い拠点への通り道だけ、切り口に写して描く
GRID_MM = 0.5            # 地形の格子の間隔 [mm]
RAY_RADIUS_MM = 0.45     # 通り道の管の半径 [mm]
RAY_TOP_MARGIN_MM = 1.5  # 通り道の中心は、その場所の地表よりこれだけ下で止める [mm](地表の色の層や川の溝の下に収まる)
MIN_ISLAND_MM2 = 4.0     # これより小さい陸・水の飛び地は、まわりに合わせる [mm2](印刷できない点のような島をなくす)
MIN_PART_MM3 = 0.5       # 切り抜きで残るこれより小さいかけらは捨てる [mm3](白の線は MIN_PART_MM3 / 10)
RAY_BOTTOM_MM = 0.2      # 通り道はブロックの底からこの高さ(1層分)より下を描かない(底より深い所を通る線は、そこで途切れる)
SPHERE_SEGMENTS = 32     # 球・管の丸さ(円を何角形にするか)
DEFAULT_BLAST_QUAKES = DEFAULT_CORE_QUAKES

# 川: 地理院の河川中心線(名前が無い)を、始まりと終わりの点(経度, 緯度)の間でたどる。地表を彫り込み、水面の色で埋める
#   長流川: 範囲の東の端(壮瞥の側)から、伊達市長和の河口の手前まで
RIVERS = {"長流川": ((140.906, 42.552), (140.843, 42.484))}
RIVER_WIDTH_MM = 1.6     # 川の溝の幅 [mm]
RIVER_DEPTH_MM = 1.0     # 溝の深さ(地表から) [mm]
RIVER_FILL_MM = 0.6      # 溝の底から水面の色を入れる厚さ [mm](地表より 0.4 mm 低くなる)
RIVER_STEP_MM = 1.0      # 川の線を描く点の間隔 [mm]


def river_path(lines, start, end):
  """河川中心線の並び lines から、start に近い点から end に近い点までの最短の道 [(経度, 緯度), ...]"""
  from scipy.sparse import coo_matrix
  from scipy.sparse.csgraph import dijkstra
  from scipy.spatial import cKDTree
  pts, edges = [], []
  for line in lines:
    base = len(pts)
    pts += list(line)
    edges += [(base + i, base + i + 1) for i in range(len(line) - 1)]
  pts = np.array(pts, float)
  if len(pts) < 2:
    return []
  # タイルの境目で切れた線の端どうし(同じ点)もつなぐ
  edges += list(cKDTree(pts).query_pairs(2e-5))
  e = np.array(edges)
  km = lambda a, b: np.hypot((a[..., 0] - b[..., 0]) * 111.0 * math.cos(math.radians(pts[0, 1])), (a[..., 1] - b[..., 1]) * 111.0)
  g = coo_matrix((km(pts[e[:, 0]], pts[e[:, 1]]) + 1e-9, (e[:, 0], e[:, 1])), shape=(len(pts), len(pts))).tocsr()
  i0 = int(np.argmin(km(pts, np.array(start))))
  dist, pred = dijkstra(g, directed=False, indices=i0, return_predecessors=True)
  ok = np.nonzero(np.isfinite(dist))[0]
  i1 = int(ok[np.argmin(km(pts[ok], np.array(end)))])
  path = [i1]
  while path[-1] != i0:
    path.append(int(pred[path[-1]]))
  return [tuple(pts[i]) for i in path[::-1]]


def resample(points, step):
  """折れ線を step ごとの点にする"""
  pts = np.asarray(points, float)
  seg = np.hypot(*np.diff(pts, axis=0).T)
  s = np.concatenate([[0], np.cumsum(seg)])
  t = np.arange(0, s[-1] + step / 2, step)
  return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], 1)


def _solid(verts, tris):
  import manifold3d
  m = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(verts, np.float32),
                                          tri_verts=np.asarray(tris, np.uint32)))
  if m.status() != manifold3d.Error.NoError:
    raise ValueError(f"閉じた形になりませんでした: {m.status()}")
  return m


def heightfield_solid(top):
  """高さ top [mm] の格子(ny × nx、間隔 GRID_MM)の下に、底(z = 0)までを詰めた閉じた形"""
  ny, nx = top.shape
  xs, ys = np.arange(nx) * GRID_MM, np.arange(ny) * GRID_MM
  gx, gy = np.meshgrid(xs, ys)
  n = nx * ny
  verts = np.concatenate([np.stack([gx.ravel(), gy.ravel(), top.ravel()], 1),
                          np.stack([gx.ravel(), gy.ravel(), np.zeros(n)], 1)])
  idx = np.arange(n).reshape(ny, nx)
  a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
  tris = [np.stack([a, b, c], 1), np.stack([a, c, d], 1),                # 上(外向きは +z)
          np.stack([a + n, c + n, b + n], 1), np.stack([a + n, d + n, c + n], 1)]   # 下
  # 側面(外から見て反時計回り)
  ring = list(idx[0, :]) + list(idx[1:, -1]) + list(idx[-1, -2::-1]) + list(idx[-2:0:-1, 0])
  for k in range(len(ring)):
    p, q = ring[k], ring[(k + 1) % len(ring)]
    tris.append(np.array([[p, p + n, q + n], [p, q + n, q]]))
  return _solid(verts, np.concatenate(tris))


def remove_small_regions(mask, min_cells):
  """True の領域のうち、min_cells より小さい飛び地を False にする(斜めのつながりも同じ領域とみなす)"""
  from scipy import ndimage
  labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
  if n == 0:
    return mask
  sizes = np.bincount(labels.ravel())
  keep = sizes >= min_cells
  keep[0] = False
  return keep[labels]


def split_runs(points, zmin, ztop=None):
  """点の並びを、高さ zmin 以上(ztop を渡すと、さらに ztop(点) 以下)の区間ごとに分ける
  範囲の境目には、ちょうど境目になる点を入れる"""
  def margin(p):
    g = p[2] - zmin
    return min(g, ztop(p) - p[2]) if ztop is not None else g
  runs, cur = [], []
  prev = None
  for p in points:
    g = margin(p)
    if prev is not None and (prev[1] >= 0) != (g >= 0):
      a, b = np.array(prev[0], float), np.array(p, float)
      c = tuple(a + (b - a) * prev[1] / (prev[1] - g))
      cur.append(c)
      if g < 0:
        runs.append(cur)
        cur = []
    if g >= 0:
      cur.append(tuple(p))
    prev = (p, g)
  if cur:
    runs.append(cur)
  return [r for r in runs if len(r) > 1]


def drop_small_parts(m, min_volume):
  """つながっていない小さなかけらを捨てる"""
  import manifold3d
  parts = m.decompose()
  kept = [p for p in parts if p.volume() >= min_volume]
  if len(kept) == len(parts):
    return m
  return manifold3d.Manifold.compose(kept) if kept else manifold3d.Manifold()


def fold_ray(xy, z, p0, d):
  """山頂から発破への切り口に写した通り道を、切り口の端(p0)で折り返して東西の切り口へ続ける
  xy ... 通り道の点 (n, 2) [mm](震源が最初、拠点が最後)、z ... 高さ [mm]、p0 ... 切り口の端、d ... 切り口の向き(単位ベクトル)
  切り口の上(p0 より先)は切り口に写し、p0 を越えた分は東西の切り口の上を、p0 から拠点の東西の位置まで進む
  戻り値 ... [(x, y, z), ...]"""
  xy, z, p0, d = np.asarray(xy, float), np.asarray(z, float), np.asarray(p0, float), np.asarray(d, float)
  t = (xy - p0) @ d
  out = []
  for i in range(len(t)):
    if t[i] >= 0:
      q = p0 + t[i] * d
      out.append((float(q[0]), float(q[1]), float(z[i])))
      continue
    if i > 0 and t[i - 1] >= 0:   # 切り口の端を通る点を入れて、線をつなげる
      f = t[i - 1] / (t[i - 1] - t[i])
      out.append((float(p0[0]), float(p0[1]), float(z[i - 1] + (z[i] - z[i - 1]) * f)))
    x = p0[0] + (xy[-1, 0] - p0[0]) * t[i] / t[-1]
    out.append((float(x), float(p0[1]), float(z[i])))
  return out


def tube(points, radius):
  """点の並びをつなぐ管(となりの2点の球を包んだ形の和)"""
  import manifold3d
  ball = manifold3d.Manifold.sphere(radius, 12)
  parts = [manifold3d.Manifold.batch_hull([ball.translate(tuple(a)), ball.translate(tuple(b))])
           for a, b in zip(points, points[1:]) if np.linalg.norm(np.subtract(a, b)) > 1e-6]
  return manifold3d.Manifold.batch_boolean(parts, manifold3d.OpType.Add) if parts else None


#   south ... 東西の切り口から南の奥行き [km](海岸まで入れたいときに広げる。北は HALF_DEPTH_KM)
#   rays ... 描く P波の通り道。"manten": 満点の拠点すべて(模型に印のある拠点)、"near": 切り口の近くの拠点(外部の観測点も含む)
def build_cake(section_starts, blast_starts, scale=SCALE_MM_PER_KM, exaggeration=1.0, above=None, south=HALF_DEPTH_KM,
               rays_mode="manten"):
  """3つの部品の色ごとの形 {部品: {色: Manifold}}、説明、大きさ [mm]"""
  import manifold3d
  M = manifold3d.Manifold
  above = exaggeration if above is None else above
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  model = velocity_model.MODELS[velocity_model.DEFAULT]
  sx, sy = proj.to_xy(*quake_3d_view.USU_SUMMIT)
  _, ssy = proj.to_xy(*SHOWA_SHINZAN)
  y_cut = (sy + ssy) / 2
  y_range = (y_cut - south, y_cut + HALF_DEPTH_KM)
  sea_mm = BOTTOM_KM * scale * exaggeration
  def zmm(depth_km):
    return (BOTTOM_KM - depth_km) * scale * exaggeration if depth_km >= 0 else sea_mm - depth_km * scale * above
  def xmm(x):
    return (x - X_RANGE_KM[0]) * scale
  def ymm(y):
    return (y - y_range[0]) * scale
  width, depth_mm = (X_RANGE_KM[1] - X_RANGE_KM[0]) * scale, (south + HALF_DEPTH_KM) * scale

  # 地形(格子の点の標高)と水面
  nx, ny = int(round(width / GRID_MM)) + 1, int(round(depth_mm / GRID_MM)) + 1
  gx, gy = np.meshgrid(X_RANGE_KM[0] + np.arange(nx) * GRID_MM / scale, y_range[0] + np.arange(ny) * GRID_MM / scale)
  lat, lon = proj.to_latlon(gx, gy)
  h = gsi_tiles.elevations(lat, lon) / 1000.0
  sea = ~np.isfinite(h)
  h = np.where(sea, 0.0, np.maximum(h, 0.0))
  water = sea | quake_3d_view.lake_mask(h, sea)
  # 点のように小さな島・水たまりはなくす(スライサーで浮いた部分として扱われるため)
  min_cells = int(round(MIN_ISLAND_MM2 / GRID_MM ** 2))
  water = ~remove_small_regions(~water, min_cells)
  water = remove_small_regions(water, min_cells)
  top = sea_mm + h * scale * above
  ground = heightfield_solid(top)
  # 水面: 湖・海の格子の点だけ WATER_MM 下げた面(水の下の地面)を作り、地表との差を水面の色にする
  #   (同じ格子の2つの面の差なので、水の上に山体の薄いかけらが残らない)
  under = heightfield_solid(np.where(water, top - WATER_MM, top))
  water_solid = ground - under
  box = lambda z0, z1: M.cube((width + 2, depth_mm + 2, z1 - z0)).translate((-1, -1, z0))
  # 川: 溝(地表から RIVER_DEPTH_MM)を彫り、底から RIVER_FILL_MM を水面の色で埋める
  groove, river_water, river_notes = [], [], []
  la0, lo0 = proj.to_latlon(X_RANGE_KM[0], y_range[0])
  la1, lo1 = proj.to_latlon(X_RANGE_KM[1], y_range[1])
  lines = gsi_tiles.river_lines(la0 - 0.01, la1 + 0.01, lo0 - 0.01, lo1 + 0.01) if RIVERS else []
  for rname, (rs, re_) in RIVERS.items():
    path = river_path(lines, rs, re_)
    if len(path) < 2:
      continue
    xy = np.array([proj.to_xy(la, lo) for lo, la in path])
    pts = resample(np.stack([xmm(xy[:, 0]), ymm(xy[:, 1])], 1), RIVER_STEP_MM)
    inside = (pts[:, 0] > -RIVER_WIDTH_MM) & (pts[:, 0] < width + RIVER_WIDTH_MM) & \
             (pts[:, 1] > -RIVER_WIDTH_MM) & (pts[:, 1] < depth_mm + RIVER_WIDTH_MM)
    pts = pts[inside]
    if len(pts) < 2:
      continue
    # 川の点の高さ: 溝の幅の中でいちばん低い地表(線と標高のずれで、岸の高さを使わないように)
    r_cells = int(math.ceil(RIVER_WIDTH_MM / 2 / GRID_MM))
    zs = []
    for x, y in pts:
      i = min(max(int(round(x / GRID_MM)), 0), nx - 1)
      j = min(max(int(round(y / GRID_MM)), 0), ny - 1)
      zs.append(float(top[max(j - r_cells, 0):j + r_cells + 1, max(i - r_cells, 0):i + r_cells + 1].min()))
    disc = lambda h: M.cylinder(h, RIVER_WIDTH_MM / 2, RIVER_WIDTH_MM / 2, 16)
    for (xa, ya), (xb, yb), za, zb in zip(pts, pts[1:], zs, zs[1:]):
      ba, bb = za - RIVER_DEPTH_MM, zb - RIVER_DEPTH_MM
      groove.append(M.batch_hull([disc(top.max() + 10 - ba).translate((xa, ya, ba)), disc(top.max() + 10 - bb).translate((xb, yb, bb))]))
      river_water.append(M.batch_hull([disc(RIVER_FILL_MM).translate((xa, ya, ba)), disc(RIVER_FILL_MM).translate((xb, yb, bb))]))
    river_notes.append(f"川: {rname}(地理院の河川中心線から {len(pts) * RIVER_STEP_MM / scale:.1f} km、"
                       f"幅 {RIVER_WIDTH_MM:g} mm・深さ {RIVER_DEPTH_MM:g} mm の溝に水面の色)")
  if groove:
    groove_solid = M.batch_boolean(groove, manifold3d.OpType.Add)
    river_solid = (M.batch_boolean(river_water, manifold3d.OpType.Add) ^ ground) - water_solid
    under = under - groove_solid
    water_solid = (water_solid - groove_solid) + river_solid
  # 山体: 海面より上と、陸の地表から LAND_SKIN_MM 下まで(海岸の低い陸では海面より下にも少し入る)
  #   (水のある所は、底の面を地表より上にして何も足さない。水の下の面と同じ高さにすると、厚さ 0 の面が残るため)
  skin_floor = heightfield_solid(np.where(water, top + 1.0, top - LAND_SKIN_MM))
  mountain = (under ^ box(sea_mm, top.max() + 5)) + (under - skin_floor)
  solids = {"water": water_solid, "mountain": mountain}
  for a, b, mat in LAYERS:
    solids[mat] = (under ^ box(zmm(b), zmm(a))) - mountain

  # 切り口: 東西の面(y = y_cut)と、山頂の東西の位置から発破とみられる震源への面
  blast = representative(blast_starts)
  p0 = np.array([xmm(sx), ymm(y_cut)])
  if blast:
    bx, by = proj.to_xy(blast[0][1]["lat"], blast[0][1]["lon"])
    d = np.array([xmm(bx), ymm(by)]) - p0
    d /= np.linalg.norm(d)
  else:
    d = np.array([-np.cos(np.radians(34)), -np.sin(np.radians(34))])
  normal = np.array([-d[1], d[0]])    # 発破への向きの左(南東の側)

  quakes, rays, notes = [], [], list(river_notes)
  r_cells = int(math.ceil(RAY_RADIUS_MM / GRID_MM))
  def ray_ceiling(p):
    """点 p で、通り道の中心が上がってよい高さ(管の太さの範囲でいちばん低い地表より RAY_TOP_MARGIN_MM 下)"""
    i = min(max(int(round(p[0] / GRID_MM)), 0), nx - 1)
    j = min(max(int(round(p[1] / GRID_MM)), 0), ny - 1)
    return float(top[max(j - r_cells, 0):j + r_cells + 1, max(i - r_cells, 0):i + r_cells + 1].min()) - RAY_TOP_MARGIN_MM

  def add_rays(r, qx_km, qy_km, depth, project, fold=False):
    """震源から拠点への P波の通り道を、切り口に写して管にする(rays_mode で描く拠点を選ぶ)
    "near" では、拠点が切り口から遠い(RAY_MAX_OFFSET_KM より遠い)か、震源のほぼ真横(切り口からの距離が、
    切り口に沿った距離の RAY_MAX_SLOPE 倍より大きい)のときは描かない"""
    positions = dict(stations)
    if rays_mode == "near":
      positions.update(r.get("external") or {})
    q_src = np.array(project(xmm(qx_km), ymm(qy_km), keep=True))
    drawn = []
    for nm in sorted(r.get("picks") or {}):
      if nm not in positions:
        continue
      la, lo, el = positions[nm]
      px, py = proj.to_xy(la, lo)
      if rays_mode == "near":
        pts2 = project(xmm(px), ymm(py))
        if pts2 is None:
          continue
        offset = float(np.hypot(xmm(px) - pts2[0], ymm(py) - pts2[1]))
        along = float(np.hypot(pts2[0] - q_src[0], pts2[1] - q_src[1]))
        if offset > RAY_MAX_SLOPE * along:
          continue
      dist = math.hypot(qx_km - px, qy_km - py)
      path = velocity_model.ray_path(model, "P", el / 1000.0, dist, depth)
      ux, uy = ((qx_km - px) / dist, (qy_km - py) / dist) if dist > 0 else (0.0, 0.0)
      pts = []
      if fold:   # 発破の切り口: 山頂の位置を越えた分は東西の切り口へ続ける
        pts = fold_ray([(xmm(px + ux * pr), ymm(py + uy * pr)) for pr, _pz in path], [zmm(pz) for _pr, pz in path], p0, d)
        path = []
      for pr, pz in path:
        q = project(xmm(px + ux * pr), ymm(py + uy * pr), keep=True)
        if q is None:
          break   # 切り口の端(山頂の位置)を越えたら、そこで止める
        pts.append((q[0], q[1], zmm(pz)))
      # 管が底の近くの層にかからないよう、中心が RAY_BOTTOM_MM + 半径 より低い所は描かない(線はそこで途切れる)
      # 上は地表より少し下で止める(拠点は切り口から離れているので、切り口の地表より高いことがあり、地上に突き出さないように)
      runs = split_runs(pts, RAY_BOTTOM_MM + RAY_RADIUS_MM, ray_ceiling)
      rays.extend(tube(run, RAY_RADIUS_MM) for run in runs)
      if runs:
        drawn.append(nm)
    return drawn
  # 東西の切り口の上の震源
  def on_ew(x, y, keep=False):
    if not keep and abs(y - ymm(y_cut)) > RAY_MAX_OFFSET_KM * scale:
      return None
    return (x, ymm(y_cut))
  for cand, r in representative(section_starts):
    qx, qy = proj.to_xy(r["lat"], r["lon"])
    depth = r["loc"]["depth"]
    quakes.append(M.sphere(QUAKE_RADIUS_MM, SPHERE_SEGMENTS).translate((xmm(qx), ymm(y_cut), zmm(depth))))
    drawn = add_rays(r, qx, qy, depth, on_ew)
    notes.append(f"東西の切り口: {cand['start']:%Y-%m-%d %H:%M} 深さ {depth:.2f} km、東西 {qx:+.2f} km、切り口から {qy - y_cut:+.2f} km"
                 f"(通り道: {'・'.join(drawn) or 'なし'})")
  # 山頂から発破への切り口の上の震源(切り口は山頂の位置から先だけなので、山頂より手前の点は描かない)
  def on_radial(x, y, keep=False):
    v = np.array([x, y]) - p0
    off = float(v @ normal)
    if not keep and abs(off) > RAY_MAX_OFFSET_KM * scale:
      return None
    t = float(v @ d)
    if t < 0:
      return None   # 切り口は山頂の位置から先だけ
    q = p0 + t * d
    return (float(q[0]), float(q[1]))
  for cand, r in blast:
    qx, qy = proj.to_xy(r["lat"], r["lon"])
    depth = r["loc"]["depth"]
    q = on_radial(xmm(qx), ymm(qy), keep=True)
    quakes.append(M.sphere(QUAKE_RADIUS_MM, SPHERE_SEGMENTS).translate((q[0], q[1], zmm(depth))))
    drawn = add_rays(r, qx, qy, depth, on_radial, fold=rays_mode == "manten")
    # (震央の地表の印は、観測の拠点の印とまぎらわしいので付けない)
    notes.append(f"山頂から発破への切り口: {cand['start']:%Y-%m-%d %H:%M} 深さ {depth:.2f} km、"
                 f"山頂の位置から {np.linalg.norm(np.array([xmm(qx), ymm(qy)]) - p0) / scale:.2f} km"
                 f"(通り道: {'・'.join(drawn) or 'なし'})")
  # 拠点の印(地表の上に出る円柱。満点の拠点)
  pins = []
  for nm, (la, lo, el) in stations.items():
    px, py = proj.to_xy(la, lo)
    if not (X_RANGE_KM[0] <= px <= X_RANGE_KM[1] and y_range[0] <= py <= y_range[1]):
      continue
    k = (int(round(ymm(py) / GRID_MM)), int(round(xmm(px) / GRID_MM)))
    zt = top[k]
    pins.append(M.cylinder(PIN_HEIGHT_MM + 1, PIN_RADIUS_MM, PIN_RADIUS_MM, SPHERE_SEGMENTS).translate((xmm(px), ymm(py), zt - 1)))
  quake = M.batch_boolean(quakes, manifold3d.OpType.Add) if quakes else M()
  # 通り道は底の近くで切る(底の面に白が出ないように。底より深い所を通る線は途切れて、また上がってくる)
  ray_parts = [x for x in rays if x is not None]
  ray_solid = M.batch_boolean(ray_parts, manifold3d.OpType.Add).trim_by_plane((0, 0, 1), RAY_BOTTOM_MM) if ray_parts else M()
  marker = M.batch_boolean([ray_solid] + pins, manifold3d.OpType.Add)
  marker = (marker ^ (ground + M.batch_boolean(pins, manifold3d.OpType.Add) if pins else ground)) - quake
  solids = {mat: sol - quake - marker for mat, sol in solids.items()}
  solids["quake"], solids["marker"] = quake ^ ground, marker

  # 3つの部品
  off = float(normal @ p0)
  north = lambda m: m.trim_by_plane((0, 1, 0), ymm(y_cut))
  south = lambda m: m.trim_by_plane((0, -1, 0), -ymm(y_cut))
  pieces = {"north": {}, "southwest": {}, "southeast": {}}
  for mat, sol in solids.items():
    pieces["north"][mat] = north(sol)
    s_ = south(sol)
    pieces["southeast"][mat] = s_.trim_by_plane((float(normal[0]), float(normal[1]), 0), off)
    pieces["southwest"][mat] = s_.trim_by_plane((-float(normal[0]), -float(normal[1]), 0), -off)
  # 切り抜きで残った小さなかけらを捨てる(震源の球は半分でも小さくないので、そのまま)
  for mats in pieces.values():
    for mat in mats:
      if mat != "quake":
        mats[mat] = drop_small_parts(mats[mat], MIN_PART_MM3 / 10 if mat == "marker" else MIN_PART_MM3)
  size = (width, depth_mm, float(top.max()))
  return pieces, notes, size, {"p0": p0, "d": d, "normal": normal, "y_cut": ymm(y_cut), "width": width, "depth": depth_mm, "sea": sea_mm}


def mesh_triangles(m):
  mesh_ = m.to_mesh()
  v = np.asarray(mesh_.vert_properties)[:, :3]
  t = np.asarray(mesh_.tri_verts)
  return v[t].astype(np.float32)


# --- 磁石の穴(TODO 47) ---
#   合わせ面の両側の部品に、円柱の磁石を、軸を横にして(平らな面を合わせ面に向けて立てて)入れる「上が開いた U字の溝」を開ける。
#   底を下にしてプリントし、溝の上の端の高さで一時停止して磁石を落としこみ、接着剤で止めて続ける(続きの層が溝にふたをする)。
#   合わせ面には MAGNET_WALL_MM の薄い壁が残り、磁石は外から見えない。
#   溝(とまわりの MAGNET_MARGIN_MM)は1つの色の中に収め、通り道(白の管)や震源の球にかからない所を選ぶ。
#   高さは2段(各段に2個、合わせ面ごとに4個)で、段の高さは3つの合わせ面で共通にする(一時停止は2回で済む)
MAGNET_DIAMETER_MM = 5.0   # 磁石の直径 [mm]
MAGNET_HEIGHT_MM = 2.0     # 磁石の高さ(厚さ) [mm]
MAGNET_GAP_MM = 0.3        # 溝のすき間の余裕(直径・厚さに足す) [mm]
MAGNET_WALL_MM = 0.8       # 合わせ面と溝の間の壁の厚さ [mm](ノズル 0.4 mm で2本分)
MAGNET_MARGIN_MM = 0.8     # 溝のまわりに要る同じ色の厚さ [mm](色の境目・通り道・震源からこれだけ離す)
MAGNET_EDGE_MM = 2.0       # 合わせ面の端(3つの部品が出会う所を含む)から溝までの距離 [mm]
MAGNET_FLOOR_MM = 1.2      # ブロックの底と溝の間の厚さの最小 [mm]
MAGNET_Z_TARGETS = (0.125, 0.625)   # 2段の溝の中心の高さの目安(底から海面までの高さとの比。A の模型で 5 mm と 25 mm)
LAYER_MM = 0.2             # 層の厚さ [mm](溝の上の端を層の境目にそろえる)
MAGNET_PX_MM = 0.25        # 位置を探す格子の細かさ [mm]
# 合わせ面: (名前, 側の部品 A, 側の部品 B)。磁石の向き: 「印」の面(磁石を積み重ねて上の面にペンで印を付ける)を
#   合わせ面に向けるか。A と B で逆にするので、どの合わせ面でも引き合う
MAGNET_FACES = [("東西・西(north と southwest)", "north", "southwest"),
                ("東西・東(north と southeast)", "north", "southeast"),
                ("山頂から発破(southwest と southeast)", "southwest", "southeast")]
MAGNET_MARKED = {("north", 0): True, ("southwest", 0): False, ("north", 1): True, ("southeast", 1): False,
                 ("southwest", 2): True, ("southeast", 2): False}


def magnet_face_frames(cut):
  """合わせ面ごとの座標 [(名前, A, B, 原点, 面に沿った向き u, A の内向き nA, s の範囲)](B の内向きは -nA)"""
  p0, d, normal = np.asarray(cut["p0"], float), np.asarray(cut["d"], float), np.asarray(cut["normal"], float)
  w, yc = cut["width"], cut["y_cut"]
  o = np.array([0.0, yc])
  # 山頂から発破の切り口が、ブロックの端(西・南)に届くまでの長さ(d は西南の向き)
  s_end = float(min(-p0[0] / d[0] if d[0] < 0 else np.inf, -p0[1] / d[1] if d[1] < 0 else np.inf))
  names = [f[0] for f in MAGNET_FACES]
  return [(names[0], "north", "southwest", o, np.array([1.0, 0.0]), np.array([0.0, 1.0]), (0.0, float(p0[0]))),
          (names[1], "north", "southeast", o, np.array([1.0, 0.0]), np.array([0.0, 1.0]), (float(p0[0]), float(w))),
          (names[2], "southwest", "southeast", p0, d, -normal, (0.0, s_end))]


def _to_local(o, u, n):
  """世界の座標 → 面の座標 (s: 面に沿って, z: 高さ, t: 部品の内へ) の 3×4 の行列"""
  return np.array([[u[0], u[1], 0.0, -float(u @ o)], [0.0, 0.0, 1.0, 0.0], [n[0], n[1], 0.0, -float(n @ o)]])


def _to_world(o, u, n):
  return np.array([[u[0], 0.0, n[0], o[0]], [u[1], 0.0, n[1], o[1]], [0.0, 1.0, 0.0, 0.0]])


def rasterize(cs, s_grid, z_grid):
  """断面(CrossSection)の内側の格子の点(s_grid × z_grid)を True にする(輪郭の向きで穴を引く)"""
  from matplotlib.path import Path as MPath
  inside = np.zeros((len(z_grid), len(s_grid)), int)
  ss, zz = np.meshgrid(s_grid, z_grid)
  for poly in cs.to_polygons():
    poly = np.asarray(poly, float)
    if len(poly) < 3:
      continue
    area = 0.5 * float(np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - np.roll(poly[:, 0], -1) * poly[:, 1]))
    i0, i1 = np.searchsorted(s_grid, [poly[:, 0].min(), poly[:, 0].max()])
    j0, j1 = np.searchsorted(z_grid, [poly[:, 1].min(), poly[:, 1].max()])
    if i1 <= i0 or j1 <= j0:
      continue
    pts = np.stack([ss[j0:j1, i0:i1].ravel(), zz[j0:j1, i0:i1].ravel()], 1)
    hit = MPath(poly).contains_points(pts).reshape(j1 - j0, i1 - i0)
    inside[j0:j1, i0:i1] += np.where(hit, 1 if area > 0 else -1, 0)
  return inside > 0


def magnet_dims():
  rh = (MAGNET_DIAMETER_MM + MAGNET_GAP_MM) / 2
  return rh, MAGNET_HEIGHT_MM + MAGNET_GAP_MM


def magnet_footprint(px):
  """溝(下が丸く上が開いた U字)とまわりの余裕の、面の上の形(中心が真ん中の格子)"""
  rh, _ = magnet_dims()
  r = rh + MAGNET_MARGIN_MM
  k = int(math.ceil(r / px))
  ds, dz = np.meshgrid(np.arange(-k, k + 1) * px, np.arange(-k, k + 1) * px)
  return (ds ** 2 + dz ** 2 <= r ** 2) | ((np.abs(ds) <= r) & (dz >= 0) & (dz <= r))


def magnet_valid_map(pieces, frame, height):
  """合わせ面の上で、溝の中心を置ける所(両側とも1つの色の中に収まる)の地図 (s の格子, z の格子, 置ける)"""
  from scipy import ndimage
  _name, a, b, o, u, n, (s0, s1) = frame
  px = MAGNET_PX_MM
  rh, length = magnet_dims()
  s_grid = np.arange(s0 + px / 2, s1, px)
  z_grid = np.arange(px / 2, height, px)
  ts = [0.1, MAGNET_WALL_MM, MAGNET_WALL_MM + length / 2, MAGNET_WALL_MM + length, MAGNET_WALL_MM + length + MAGNET_MARGIN_MM]
  foot = magnet_footprint(px)
  ok = np.ones((len(z_grid), len(s_grid)), bool)
  for piece, nn in ((a, n), (b, -n)):
    # 両側とも同じ s・z(合わせ面の上の同じ点)で、t だけ部品の内へ向ける
    m2l = _to_local(o, u, nn)
    side = np.zeros_like(ok)
    for mat, sol in pieces[piece].items():
      if sol.is_empty():
        continue
      loc = sol.transform(m2l)
      every = np.ones_like(ok)
      for t in ts:
        every &= rasterize(loc.slice(t), s_grid, z_grid)
        if not every.any():
          break
      side |= ndimage.binary_erosion(every, structure=foot)
    ok &= side
  # 合わせ面の端から MAGNET_EDGE_MM + 溝の半径 より内だけ
  edge = MAGNET_EDGE_MM + rh
  ok &= ((s_grid > s0 + edge) & (s_grid < s1 - edge))[None, :]
  return s_grid, z_grid, ok


def place_magnets(pieces, cut, height, per_row=2, rows=2, z_levels=None, targets=None):
  """磁石の溝の位置 [{face, a, b, s, z, top, center_a, ...}] と、段の高さ(溝の中心)[mm]"""
  rh, _ = magnet_dims()
  frames = magnet_face_frames(cut)
  maps = [magnet_valid_map(pieces, f, height) for f in frames]
  sep = 2 * rh + 2 * MAGNET_MARGIN_MM   # となりの溝の中心の間
  # 溝の上の端が層の境目になる中心の高さ
  cand = [k * LAYER_MM - rh for k in range(1, int(height / LAYER_MM)) if k * LAYER_MM - 2 * rh >= MAGNET_FLOOR_MM]
  def spots(m, z):
    s_grid, z_grid, ok = m
    j = int(round((z - z_grid[0]) / MAGNET_PX_MM))
    if j < 0 or j >= len(z_grid):
      return np.array([])
    return s_grid[ok[j]]
  def pick(ss, count, ok_more=lambda s: True):
    """置ける所 ss から、count 個をなるべく広げて選ぶ(となりと sep 離す。ok_more は、ほかの合わせ面の溝とのぶつかりの確かめ)"""
    if len(ss) == 0:
      return []
    lo, hi = ss.min(), ss.max()
    targets = [lo + (hi - lo) * (k + 0.5) / count for k in range(count)]
    chosen = []
    for tg in targets:
      for s in ss[np.argsort(np.abs(ss - tg))]:
        if all(abs(s - c) >= sep for c in chosen) and ok_more(s):
          chosen.append(float(s))
          break
    return chosen if len(chosen) == count else []
  lengths = [f[6][1] - f[6][0] for f in frames]
  def spread(z):
    """高さ z で、どの合わせ面でも per_row 個置けるとき、選んだ点の広がり(合わせ面の長さとの比)のいちばん小さいもの"""
    out = 1.0
    for m, ln in zip(maps, lengths):
      ss = pick(spots(m, z), per_row)
      if len(ss) < per_row:
        return None
      out = min(out, (max(ss) - min(ss)) / ln if per_row > 1 else 1.0)
    return out
  if z_levels is None:
    good = {z: sp for z in cand if (sp := spread(z)) is not None}
    z_levels = []
    if good:
      zs = sorted(good)
      if rows >= 2:
        # 2段: 目安の高さ(targets。底から海面までの高さとの比)にいちばん近い組
        tg = [t * cut.get("sea", height) for t in (targets or MAGNET_Z_TARGETS)]
        pairs = [(z1, z2) for i, z1 in enumerate(zs) for z2 in zs[i + 1:] if z2 - z1 >= sep]
        if pairs:
          z_levels = list(min(pairs, key=lambda p: abs(p[0] - tg[0]) + abs(p[1] - tg[1])))
      if not z_levels:
        z_levels = [max(zs, key=lambda z: good[z])]
  else:
    z_levels = [min(cand, key=lambda c: abs(c - z)) for z in z_levels]
  pockets = []
  count = per_row if len(z_levels) >= rows else per_row * rows
  # 同じ部品のほかの合わせ面の溝(南西のくさびの先では、2つの合わせ面が近い)から、溝の奥まで含めて離す
  _rh, length = magnet_dims()
  reach = sep + 2 * (MAGNET_WALL_MM + length + MAGNET_MARGIN_MM)
  for fi, (frame, m) in enumerate(zip(frames, maps)):
    name, a, b, o, u, n, _r = frame
    for z in z_levels:
      others = [pk for pk in pockets if pk["z"] == float(z) and {pk["a"], pk["b"]} & {a, b}]
      def clear(s):
        xy = o + s * u
        return all(math.hypot(xy[0] - pk["xy"][0], xy[1] - pk["xy"][1]) >= reach for pk in others)
      for s in pick(spots(m, z), count, clear):
        xy = o + s * u
        pockets.append({"face": fi, "face_name": name, "a": a, "b": b, "s": s, "z": float(z), "top": float(z + rh),
                        "xy": (float(xy[0]), float(xy[1]))})
  return pockets, z_levels, frames


def magnet_pocket(frame, s, z, inward):
  """1つの溝の形(世界の座標)。inward: 部品の内への向き(A は nA、B は -nA)"""
  import manifold3d
  M = manifold3d.Manifold
  _name, _a, _b, o, u, _n, _r = frame
  rh, length = magnet_dims()
  # 面の座標 (s, z, t) で作る: t の向きに長さ length の円柱と、その上半分から溝の上の端までの箱
  cyl = M.cylinder(length, rh, rh, 48).translate((s, z, MAGNET_WALL_MM))
  slot = M.cube((2 * rh, rh, length)).translate((s - rh, z, MAGNET_WALL_MM))
  return (cyl + slot).transform(_to_world(o, u, inward))


def cut_magnet_holes(pieces, pockets, frames):
  """部品の色ごとの形から、磁石の溝を引く。溝の形 {部品: Manifold} も返す(確認用)"""
  import manifold3d
  holes = {}
  for pk in pockets:
    frame = frames[pk["face"]]
    n = frame[5]
    for piece, inward in ((pk["a"], n), (pk["b"], -n)):
      holes.setdefault(piece, []).append(magnet_pocket(frame, pk["s"], pk["z"], inward))
  out = {}
  for piece, parts in holes.items():
    h = manifold3d.Manifold.batch_boolean(parts, manifold3d.OpType.Add)
    pieces[piece] = {mat: sol - h for mat, sol in pieces[piece].items()}
    out[piece] = h
  return out


def magnet_notes(pockets, z_levels):
  """README に書く、磁石の入れ方と一時停止の高さ"""
  rh, length = magnet_dims()
  lines = ["磁石の溝(TODO 47):",
           f"  磁石: 直径 {MAGNET_DIAMETER_MM:g} mm × 高さ {MAGNET_HEIGHT_MM:g} mm の円柱(ネオジム)。溝は幅 {2 * rh:.2f} mm・"
           f"厚さ {length:.2f} mm の、下が丸く上が開いた U字。合わせ面との間に {MAGNET_WALL_MM:g} mm の壁が残る",
           f"  合わせ面ごとに {len(pockets) // len(MAGNET_FACES)} 個、合わせて {2 * len(pockets)} 個"
           f"(合わせ面の両側に1個ずつで1組、{len(pockets)} 組)", ""]
  tops = sorted({pk["top"] for pk in pockets})
  lines.append("  一時停止する高さ(どの部品も同じ。底を下にして置き、層の厚さ 0.2 mm のとき):")
  for k, top in enumerate(tops, 1):
    lines.append(f"    {k}回目: {round((top + LAYER_MM) / LAYER_MM)} 層目({top + LAYER_MM:.2f} mm の層)の前"
                 f"(溝の上の端 {top:.2f} mm。この層から溝にふたができる)")
  lines += ["",
            "  Bambu Studio での一時停止の入れ方:",
            "    1. スライスしたあと、プレビューの右の縦のスライダーで、上の高さの層を選ぶ(数字は層の上の面の高さ)",
            "    2. スライダーのつまみを右クリック →「一時停止を追加」(Add Pause)。その層を印刷する前に止まる",
            "    3. 1つ下の層で、溝が開いたままなこと、選んだ層で溝にふたがかかることを、プレビューで確かめる",
            "    4. 止まったら、溝に磁石を立てて(平らな面を合わせ面に向けて)落としこみ、瞬間接着剤を少しつけて押さえる。",
            "       磁石の上の端が溝の上の端より下にあること(ノズルが当たらないように)を確かめて、続ける",
            "    (磁石は鉄に引かれるので、ピンセットはプラスチックか木のものがよい。となりの溝の磁石どうしも引き合う)", "",
            "  磁石の向き: 磁石を何個か積み重ねて、いちばん上の面にペンで印を付け、1個ずつ外す(どの磁石も「印」の面が同じ極になる)",
            "    合わせ面ごとに、片方の部品は「印」の面、もう片方は「印のない面」を合わせ面に向ける(引き合う):"]
  seen = set()
  for pk in pockets:
    if pk["face"] in seen:
      continue
    seen.add(pk["face"])
    fi = pk["face"]
    mark = lambda p: "印の面" if MAGNET_MARKED[(p, fi)] else "印のない面"
    lines.append(f"    {pk['face_name']}: {pk['a']} は{mark(pk['a'])}、{pk['b']} は{mark(pk['b'])}を合わせ面に向ける")
  lines += ["", "  溝の位置(合わせ面に沿った位置 s は、東西の面は西の端から、山頂から発破の面は山頂の位置から [mm]):"]
  for pk in pockets:
    lines.append(f"    {pk['face_name']}: s {pk['s']:.1f} mm、溝の中心の高さ {pk['z']:.2f} mm(上の端 {pk['top']:.2f} mm)、"
                 f"上から見た位置 x {pk['xy'][0]:.1f}・y {pk['xy'][1]:.1f} mm")
  return lines


def write_magnet_figure(path, maps_pieces, frames, pockets, height):
  """合わせ面ごとに、面の上の色と溝の位置を描いた図(確認用)"""
  import matplotlib
  matplotlib.use("Agg")
  import matplotlib.pyplot as plt
  from matplotlib.patches import Rectangle, Circle
  plt.rcParams["font.family"] = ["Hiragino Sans", "Yu Gothic", "Meiryo", "sans-serif"]
  rh, _ = magnet_dims()
  fig, axes = plt.subplots(len(frames), 1, figsize=(12, 3.2 * len(frames)))
  px = 0.5
  for ax, (fi, frame) in zip(np.atleast_1d(axes), enumerate(frames)):
    name, a, _b, o, u, n, (s0, s1) = frame
    s_grid = np.arange(s0 + px / 2, s1, px)
    z_grid = np.arange(px / 2, height, px)
    img = np.ones((len(z_grid), len(s_grid), 3))
    loc_m = _to_local(o, u, n)
    for mat, sol in maps_pieces[a].items():
      if sol.is_empty():
        continue
      r = rasterize(sol.transform(loc_m).slice(0.1), s_grid, z_grid)
      img[r] = matplotlib.colors.to_rgb(MATERIALS[mat][1])
    ax.imshow(img, origin="lower", extent=(s0, s1, 0, height), interpolation="nearest")
    for pk in pockets:
      if pk["face"] != fi:
        continue
      ax.add_patch(Circle((pk["s"], pk["z"]), rh, fill=False, ec="#e000e0", lw=1.5))
      ax.add_patch(Rectangle((pk["s"] - rh, pk["z"]), 2 * rh, rh, fill=False, ec="#e000e0", lw=1.5))
    for top in sorted({pk["top"] for pk in pockets}):
      ax.axhline(top, color="#e000e0", lw=0.6, ls=":")
    ax.set_title(f"{name}(合わせ面を {a} の側から見る。紫: 磁石の溝、点線: 溝の上の端 = 一時停止)", fontsize=10)
    ax.set_xlabel("合わせ面に沿った位置 s [mm]")
    ax.set_ylabel("高さ [mm]")
    ax.set_aspect("equal")
  fig.tight_layout()
  fig.savefig(path, dpi=130)
  plt.close(fig)


CAKE_OFFSETS = {"north": (0, 18, 0), "southwest": (-14, -10, 0), "southeast": (8, -14, 0)}   # 確認用に部品を離す [mm]

def write_cake(out_dir, pieces, notes, size, scale, exaggeration, above, magnet_lines=()):
  out_dir.mkdir(parents=True, exist_ok=True)
  for old in out_dir.glob("*.stl"):
    old.unlink()
  files, preview = [], []
  for name, mats in pieces.items():
    for k, mat in enumerate(MATERIALS, 1):
      m = mats.get(mat)
      if m is None or m.is_empty():
        continue
      tris = mesh_triangles(m)
      fn = f"{name}_{k}_{mat}.stl"
      write_stl(out_dir / fn, tris, f"{name} {mat}")
      files.append((fn, len(tris), m.volume() / 1000.0))
      preview.append((name, mat, tris))
  lines = ["3Dプリントの模型(試作・ケーキのように3つに切る形)", "",
           f"大きさ: 東西 {size[0]:.0f} mm × 南北 {size[1]:.0f} mm × 高さ {size[2]:.0f} mm"
           f"(1 km = {scale:g} mm、縦の倍率 海面より下 {exaggeration:g}・上 {above if above is not None else exaggeration:g})",
           f"範囲: 東西 {X_RANGE_KM[1] - X_RANGE_KM[0]:g} km、東西の切り口から北 {HALF_DEPTH_KM:g} km・南 {size[1] / scale - HALF_DEPTH_KM:g} km",
           "部品: north(東西の切り口の北)、southwest(南西のくさび。山頂から発破への切り口の西)、southeast(南〜南東)", "",
           "色(ファイル名の番号 = フィラメントの番号の案):"]
  lines += [f"  {k}. {mat}: {desc}" for k, (mat, (desc, _c)) in enumerate(MATERIALS.items(), 1)]
  lines += ["", "地震:"] + [f"  {n}" for n in notes]
  lines += ["", "Bambu Studio: 部品ごとに STL をまとめて読み込み、「1つの物体の複数のパーツとして読み込む」を選ぶ。",
            "  パーツごとにフィラメントを割り当てる。切り口の面を下にして置くと、切り口がきれいに仕上がる"
            + ("(磁石の溝があるときは、底を下にして置く)" if magnet_lines else "") + "。", ""]
  if magnet_lines:
    lines += list(magnet_lines) + [""]
  lines += [f"{gsi_tiles.DEM_ATTRIBUTION}。地下の層と P波の通り道は、震源を求めるのに使う深さで速度が変わる構造(模式的なもの)", "",
            "ファイル(三角形の数、体積):"] + [f"  {fn} ({n}、{v:.1f} cm3)" for fn, n, v in files]
  (out_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
  return files, preview


def main():
  global MAGNET_DIAMETER_MM, MAGNET_HEIGHT_MM
  ap = argparse.ArgumentParser(description="3Dプリント用の模式的な模型を、色ごとの STL に書き出す(試作)")
  ap.add_argument("--layout", choices=("cake", "core"), default="cake",
                  help="cake: 東西と山頂から発破への面で3つに切る(manifold3d が要る)。core: 南北に割り、発破はコアにする")
  ap.add_argument("--scale", type=float, default=SCALE_MM_PER_KM, help="1 km を何 mm にするか")
  ap.add_argument("--exaggeration", type=float, default=1.0, help="縦の倍率(全体。--above を付けたときは海面より下)")
  ap.add_argument("--above", type=float, default=None, help="海面より上の縦の倍率(省略すると --exaggeration と同じ)")
  ap.add_argument("--south", type=float, default=HALF_DEPTH_KM,
                  help="東西の切り口から南の奥行き [km](cake のとき。海岸まで入れるなら 6〜9.5)")
  ap.add_argument("--rays", choices=("manten", "near"), default="manten",
                  help="P波の通り道(cake のとき)。manten: 満点の拠点すべて、near: 切り口の近くの拠点(外部の観測点も含む)")
  ap.add_argument("--magnets", action="store_true",
                  help="(cake のとき)合わせ面に、磁石を閉じ込める U字の溝を開ける(合わせ面ごとに 2段×2個。README に一時停止の高さ)")
  ap.add_argument("--magnet-size", default=f"{MAGNET_DIAMETER_MM:g}x{MAGNET_HEIGHT_MM:g}",
                  help="磁石の 直径x高さ [mm](例 5x2)")
  ap.add_argument("--magnet-z", default=None,
                  help="溝の中心の高さ [mm] をカンマで(例 5,25。省略すると、底から海面までの 1/8 と 5/8 の高さに近い、"
                       "3つの合わせ面で共通に置ける高さを探す)")
  ap.add_argument("--name", default=None, help="出力のフォルダの名前(省略すると usu_<layout>)")
  args = ap.parse_args()
  out = app_paths.BATCH_PLOTS_DIR / "print_model" / (args.name or f"usu_{args.layout}")
  if args.layout == "cake":
    MAGNET_DIAMETER_MM, MAGNET_HEIGHT_MM = (float(v) for v in args.magnet_size.lower().split("x"))
    pieces, notes, size, cut = build_cake(DEFAULT_SECTION_QUAKES, DEFAULT_BLAST_QUAKES, args.scale, args.exaggeration, args.above, args.south, args.rays)
    pockets, frames, magnet_lines = [], None, []
    if args.magnets:
      zs = [float(v) for v in args.magnet_z.split(",")] if args.magnet_z else None
      pockets, z_levels, frames = place_magnets(pieces, cut, size[2], z_levels=zs)
      if not pockets:
        raise SystemExit("磁石の溝を置ける所が見つからない(--magnet-z で高さを指定してみる)")
      magnet_lines = magnet_notes(pockets, z_levels)
      out.mkdir(parents=True, exist_ok=True)
      write_magnet_figure(out / "magnets.png", pieces, frames, pockets, size[2])
      cut_magnet_holes(pieces, pockets, frames)
    files, _preview = write_cake(out, pieces, notes, size, args.scale, args.exaggeration, args.above, magnet_lines)
    # 確認用の HTML(地形の格子を粗くして作り直す)
    global GRID_MM
    GRID_MM = PREVIEW_CELL_MM
    pieces, _n, _s, _c = build_cake(DEFAULT_SECTION_QUAKES, DEFAULT_BLAST_QUAKES, args.scale, args.exaggeration, args.above, args.south, args.rays)
    holes = cut_magnet_holes(pieces, pockets, frames) if pockets else {}
    preview = [(name, mat, mesh_triangles(m)) for name, mats in pieces.items() for mat, m in mats.items()
               if not m.is_empty()]
    preview += [(name, "magnet", mesh_triangles(h)) for name, h in holes.items()]
    write_preview(out / "preview.html", preview, 0, offsets=CAKE_OFFSETS)
    print("\n".join(notes + magnet_lines))
    print(f"大きさ {size[0]:.0f} × {size[1]:.0f} × {size[2]:.0f} mm、ファイル {len(files)} 個: {out}")
    return
  global CELL_MM
  pieces, cores, notes, size, ny = build(DEFAULT_SECTION_QUAKES, DEFAULT_CORE_QUAKES, args.scale, args.exaggeration, args.above)
  files = write_model(out, pieces, cores, notes, size, ny, args.scale, args.exaggeration, args.above)
  # 確認用の HTML(柱を大きくして作り直す)
  CELL_MM = PREVIEW_CELL_MM
  pieces, cores, _notes, _size, ny = build(DEFAULT_SECTION_QUAKES, DEFAULT_CORE_QUAKES, args.scale, args.exaggeration, args.above)
  preview = []
  parts = [(name, p["cols"], 0.0, (0 if name == "south" else ny) * CELL_MM) for name, p in pieces.items()]
  parts += [(f"core{k}", c["cols"], c["i0"] * CELL_MM, (c["j0"] + (0 if c["piece"] == "south" else ny)) * CELL_MM)
            for k, c in enumerate(cores, 1)]
  for name, cols, x0, y0 in parts:
    for mat in MATERIALS:
      tris = mesh(cols, mat, x0, y0, CELL_MM)
      if len(tris):
        preview.append((name, mat, tris))
  write_preview(out / "preview.html", preview, ny)
  print("\n".join(notes))
  print(f"大きさ {size[0]:.0f} × {size[1]:.0f} × {size[2]:.0f} mm、ファイル {len(files)} 個: {out}")


if __name__ == "__main__":
  main()
