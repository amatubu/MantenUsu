# raw_to_win.py(ロガーの raw を WIN にする)のテスト
#   本物の raw は Git に入れないので、形式どおりのレコードを作って確かめる
import zipfile
from datetime import datetime

import numpy as np
import pytest

import raw_to_win as rw

CH = [0x8d04, 0x8d05, 0x8d06]


# 値 V (k, 3) を、'W' レコード(1024 バイト)にする。値が 1 バイトに収まるフレームは 1 バイトで書く
def make_w(idx, v):
  body = bytearray()
  for row in v:
    hi = [int(x) >> 2 for x in row]
    lsb = sum((int(x) & 3) << (2 * j) for j, x in enumerate(row))
    if all(-128 <= h <= 127 for h in hi):
      body += bytes([0x80 | lsb]) + b"".join(h.to_bytes(1, "big", signed=True) for h in hi)
    else:
      body += bytes([0xC0 | lsb]) + b"".join(h.to_bytes(2, "big", signed=True) for h in hi)
  rec = b"W\0\0\x44" + len(v).to_bytes(2, "big") + idx.to_bytes(4, "big") + b"\xff" * 6 + bytes(body)
  assert len(rec) <= 1024
  return rec + b"\0" * (1024 - len(rec))


def make_header(station="0970355225", card=1):
  h = bytearray(1024)
  h[:10] = station.encode()
  h[0x16] = card
  h[0x18:0x38] = b"4232.4437,N,14051.4218,E000190.0"
  for j, c in enumerate(CH):
    h[0x3a + 2 * j:0x3c + 2 * j] = c.to_bytes(2, "big")
  h[0x40:0x46] = bytes.fromhex("0200030200 01".replace(" ", ""))
  return bytes(h)


def make_g(start="20060422025241"):
  return (b"G\0" + start.encode()).ljust(1024, b"\0")


# 通し番号 first から、frames 個のフレームを、1 レコード per フレーム数で並べた raw を書く
def write_raw(path, v, first=0, per=140, card=1, gap_after=None, gap=0, station="0970355225",
              start="20060422025241"):
  recs = [make_header(station, card), make_g(start)]
  idx = first
  for a in range(0, len(v), per):
    if gap_after is not None and a == gap_after:
      idx += gap
    recs.append(make_w(idx, v[a:a + per]))
    idx += len(v[a:a + per])
  recs.append(b"\xff" * 1024 * 3)
  path.write_bytes(b"".join(recs))


def test_decode_w_both_widths():
  v = np.array([[1527, -408, -188], [3, -1, 0], [40000 // 4 * 4 + 2, -5, 7]])
  idx, n, out, coarse = rw.decode_w(np.frombuffer(make_w(123, v), np.uint8).reshape(1, 1024))
  assert not coarse.any()
  assert idx.tolist() == [123] and n.tolist() == [3]
  assert out.tolist() == v.tolist()


# 粗い記録(制御の bit 7 が 0。大きな揺れのとき): 値は 64 倍。記念館 2025-12-08 23:15:42 の地震のレコードの形
def test_decode_w_coarse_frames():
  body = (bytes([0x40 | 0b110101]) + bytes.fromhex("01e40051ff26")     # 2 バイト・粗い
          + bytes([0x01]) + bytes.fromhex("49092c")                     # 1 バイト・粗い
          + bytes([0xC0]) + bytes.fromhex("000100020003"))              # 2 バイト・細かい
  rec = (b"W\0\0\x44" + (3).to_bytes(2, "big") + (0).to_bytes(4, "big") + b"\xff" * 6 + body).ljust(1024, b"\0")
  _, _, out, coarse = rw.decode_w(np.frombuffer(rec, np.uint8).reshape(1, 1024))
  assert coarse.tolist() == [True, True, False]
  assert out.tolist() == [[(4 * 484 + 1) * 64, (4 * 81 + 1) * 64, (4 * -218 + 3) * 64],
                          [(4 * 73 + 1) * 64, 4 * 9 * 64, 4 * 44 * 64],
                          [4, 8, 12]]


def test_decode_w_rejects_overflow():
  rec = bytearray(make_w(0, np.array([[1, 2, 3]])))
  rec[4:6] = (300).to_bytes(2, "big")
  with pytest.raises(rw.RawError):
    rw.decode_w(np.frombuffer(bytes(rec), np.uint8).reshape(1, 1024))


def test_gps_start_time_rollover():
  assert rw.gps_start_time(make_g()) == datetime(2025, 12, 6, 11, 52, 41)
  assert rw.gps_start_time(make_g("20260101000000")) == datetime(2026, 1, 1, 9, 0, 0)


@pytest.mark.parametrize("amp", [3, 100, 20000, 3_000_000, 100_000_000])
def test_encode_read_win_roundtrip(amp):
  rng = np.random.default_rng(amp)
  data = np.cumsum(rng.integers(-amp, amp + 1, (250, 3)), 0)
  blob = rw.encode_second(datetime(2025, 12, 6, 11, 52, 41), CH, data)
  [(t, chans)] = rw.read_win(blob)
  assert t == datetime(2025, 12, 6, 11, 52, 41)
  for j, c in enumerate(CH):
    assert chans[c].tolist() == data[:, j].tolist()


def test_fit_channel_recovers_gain():
  rng = np.random.default_rng(0)
  v = rng.integers(-60000, 60000, 20000)
  w = np.floor(v * 1.0003266 - 3.00098).astype(int)
  g, c, h = rw.fit_channel(v, w)
  assert abs(g - 1.0003266) < 1e-7 and abs(c + 3.00098) < 0.01 and h > 0
  assert (np.floor(v * g + c) == w).all()
  # 合わないもの(1 つだけ値を変える)は None
  w[100] += 5
  assert rw.fit_channel(v, w) is None


def test_convert_minutes_and_gap(tmp_path):
  rng = np.random.default_rng(1)
  # 11:52:41 から 100 秒。40.32 秒のあとに 30.5 秒の抜け
  v = rng.integers(-2000, 2000, (100 * 250, 3))
  write_raw(tmp_path / "a.raw", v, gap_after=72 * 140, gap=7625)
  cal = tmp_path / "cal.txt"
  f = rw.RawFile(tmp_path / "a.raw")
  rw.save_cal("0970355225", CH, [[-3.00098, -1.0013, -3.0035], [-13.0093, -5.0028, -7.005]],
              [[1.0003266, 1.0013245, 1.0011865], [1.0007122, 1.0005471, 1.0007209]], f.header["cal_key"], cal)
  offs, gains = rw.calibration_for(f.header, rw.load_cal(cal))
  assert offs == [[-3.00098, -1.0013, -3.0035], [-13.0093, -5.0028, -7.005]] and gains[1][2] == 1.0007209
  [(a_end, b_start, _)] = f.gaps()
  assert b_start - a_end == 7625
  got = {}

  def sink(name, blob, t, w, coarse, append):
    got[name] = got.get(name, b"") + blob if append else blob
  files, start = rw.open_inputs([tmp_path / "a.raw"])
  rw.write_minutes(rw.iter_frames(files, report=lambda s: None), start, f.header, offs, gains, sink)
  assert sorted(got) == ["251206/0970355225_2512061152.win", "251206/0970355225_2512061153.win",
                         "251206/0970355225_2512061154.win"]
  secs = {name: [t.second for t, _ in rw.read_win(b)] for name, b in got.items()}
  # 52:41〜52:59(19 秒)、53:00〜53:20(抜けの前の 41 秒目は端数なので捨てる)、53:52〜53:59
  assert secs["251206/0970355225_2512061152.win"] == list(range(41, 60))
  assert secs["251206/0970355225_2512061153.win"] == list(range(0, 21)) + list(range(52, 60))
  # 値は floor(g × V + c)
  t, chans = rw.read_win(got["251206/0970355225_2512061152.win"])[0]
  assert chans[0x8d04].tolist() == np.floor(v[:250, 0] * 1.0003266 - 3.00098).astype(int).tolist()


def test_two_cards_and_zip_output(tmp_path, monkeypatch):
  rng = np.random.default_rng(2)
  v = rng.integers(-500, 500, (130 * 250, 3))
  write_raw(tmp_path / "x_2.raw", v[60 * 250:], first=60 * 250, card=2)
  write_raw(tmp_path / "x_1.raw", v[:60 * 250], card=1)
  files, start = rw.open_inputs([tmp_path / "x_2.raw", tmp_path / "x_1.raw"])
  assert [f.header["card"] for f in files] == [1, 2]
  monkeypatch.setattr(rw, "load_cal", lambda path=None: {"0970355225": {
    "channels": {(c, m): (0, 1.0) for c in CH for m in rw.MODES}, "keys": {files[0].header["cal_key"]}}})
  out = tmp_path / "out.zip"
  assert rw.main([str(tmp_path / "x_1.raw"), str(tmp_path / "x_2.raw"), "-o", str(out)]) == 0
  names = sorted(zipfile.ZipFile(out).namelist())
  assert names[0] == "0970355225/251206/0970355225_2512061152.win" and len(names) == 3
  # 同じものを --verify で比べると、全部一致する
  assert rw.main([str(tmp_path / "x_1.raw"), str(tmp_path / "x_2.raw"), "--verify", str(out)]) == 0


def test_mixed_stations_rejected(tmp_path):
  v = np.zeros((500, 3), int)
  write_raw(tmp_path / "a.raw", v)
  (tmp_path / "b.raw").write_bytes(make_header("1070432003") + make_g() + make_w(0, v[:10]))
  with pytest.raises(rw.RawError):
    rw.open_inputs([tmp_path / "a.raw", tmp_path / "b.raw"])


def test_calibrate_uses_mode_and_needs_coarse_gain():
  v = np.array([[1000, 2000, 3000], [64000, 128000, -64000]])
  coarse = np.array([False, True])
  got = rw.calibrate(v, coarse, [[-3, -1, -3], [-13, -5, -7]], [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
  assert got.tolist() == [[997, 1999, 2997], [127987, 255995, -128007]]
  with pytest.raises(rw.RawError):
    rw.calibrate(v, coarse, [[0] * 3, [0] * 3], [[1.0] * 3, [float("nan")] * 3])


# 一度に読むレコードの区切りが秒の途中に来ても、秒を捨てない(区切りの前後は続いている)
def test_chunk_boundary_keeps_seconds(tmp_path, monkeypatch):
  rng = np.random.default_rng(3)
  v = rng.integers(-500, 500, (90 * 250, 3))
  write_raw(tmp_path / "a.raw", v)
  monkeypatch.setattr(rw, "CHUNK", 7)   # 7 レコード = 980 フレーム(秒の途中で区切れる)
  files, start = rw.open_inputs([tmp_path / "a.raw"])
  got = {}

  def sink(name, blob, t, w, coarse, append):
    got[name] = got.get(name, b"") + blob if append else blob
  ident = [[0] * 3, [0] * 3], [[1.0] * 3, [1.0] * 3]
  rw.write_minutes(rw.iter_frames(files, report=lambda s: None), start, files[0].header, *ident, sink)
  blocks = [b for name in sorted(got) for b in rw.read_win(got[name])]
  assert len(blocks) == 90
  assert np.concatenate([b[1][0x8d05] for b in blocks]).tolist() == v[:, 1].tolist()


# GPS 補正の記録から、時刻のずれ(ミリ秒)を読む
def test_gps_drift_ms(tmp_path):
  def g_rec(freq, drift):
    r = bytearray(make_g())
    r[0x10:0x1c] = (100).to_bytes(4, "big") + freq.to_bytes(4, "big") + drift.to_bytes(4, "big")
    return bytes(r)
  data = (make_header() + g_rec(122880000, 0) + g_rec(0xFFFFFFFF, 0xFFFFFFFF) + g_rec(122879999, 4096 * 2)
          + g_rec(122880000, 4096000 - 4096 * 5) + make_w(0, np.zeros((10, 3), int)) + b"\xff" * 1024)
  (tmp_path / "a.raw").write_bytes(data)
  assert rw.RawFile(tmp_path / "a.raw").gps_drift_ms().tolist() == [0.0, 2.0, -5.0]


def g_drift(drift, freq=122880000):
  r = bytearray(make_g())
  r[0x10:0x1c] = (100).to_bytes(4, "big") + freq.to_bytes(4, "big") + (drift % 4096000).to_bytes(4, "big")
  return bytes(r)


# GPS による時刻の補正(gps3)の時刻と向き(馬場 2026/8〜11 の変換ツールの gps3 と同じ決め方)
def test_gps_corrections_rule(tmp_path):
  ms = 4096
  # 1 レコード = 1 秒(250 フレーム)。(この秒の 'W' のあとに置く 'G' のずれ)
  marks = {10: int(4.47 * ms), 20: 16379, 30: 16379, 100: int(8.43 * ms), 110: -int(4.2 * ms),
           120: int(3.0 * ms), 130: 0}
  recs = [make_header(), g_drift(0)]
  for sec in range(140):
    recs.append(make_w(sec * 250, np.zeros((250, 3), int)))
    if sec in marks:
      recs.append(g_drift(marks[sec]))
    elif sec % 7 == 0:
      recs.append(g_drift(0xFFFFFFFF, 0xFFFFFFFF))   # 測っていない記録は使わない
  recs.append(b"\xff" * 1024)
  (tmp_path / "a.raw").write_bytes(b"".join(recs))
  files, start = rw.open_inputs([tmp_path / "a.raw"])
  corr = rw.gps_corrections(files)
  # 4.47 ms → +1(秒 10)、3.999 ms → 0 に戻す(秒 20。補正後の時刻で数える)、8.43 ms → +2(前の測定 30 秒と
  # 100 秒の間を等分: 65 秒と 100 秒)、-4.2 ms → -1(0 の側へ切り捨て。2 から -1 へ 3 つ: 103・106・110)、
  # 3.0 ms → 0(+1。1 サンプル減らしたあとの時刻なので 119 秒)、0 → 変わらない
  assert corr == [(10, 1), (20, -1), (65, 1), (100, 1), (103, -1), (106, -1), (110, -1), (119, 1)]
  assert rw.gps3_lines(corr[:1], start) == ["251206115251,1"]


# 補正をサンプルの並びに入れる: 足すときはその秒の 249 番目に 248 番目を写し、減らすときは次の秒の頭を捨てる
@pytest.mark.parametrize("pieces", [[3000], [749, 2251], [748, 1, 751, 1500], [1499, 1, 1500]])
def test_correct_frames(pieces):
  rng = np.random.default_rng(5)
  raw = rng.integers(-500, 500, (3000, 3))
  segs, a = [], 0
  for n in pieces:
    segs.append((a, raw[a:a + n], np.zeros(n, bool)))
    a += n
  got = {}

  def sink(name, blob, t, w, coarse, append):
    got[name] = got.get(name, b"") + blob if append else blob
  ident = [[0] * 3, [0] * 3], [[1.0] * 3, [1.0] * 3]
  start = datetime(2026, 10, 3, 9, 52, 0)
  rw.write_minutes(rw.correct_frames(iter(segs), [(2, 1), (5, -1)]), start, {"station": "1070432003", "channels": CH},
                   *ident, lambda *a: sink(*a))
  blocks = [b for name in sorted(got) for b in rw.read_win(got[name])]
  out = np.concatenate([b[1][CH[0]] for b in blocks])
  # 秒 2 の 249 番目に 248 番目(raw 748)を写す。秒 6 の頭になるはずの raw 1499(補正後の 1500)を捨てる
  want = np.concatenate([raw[:749], raw[748:749], raw[749:1499], raw[1500:]])[:, 0]
  assert len(blocks) == 12 and out.tolist() == want[:3000].tolist()
  assert [t.second for t, _ in blocks] == list(range(12))


def test_no_gps_correction_option(tmp_path, capsys):
  recs = [make_header(), g_drift(0)]
  for sec in range(20):
    recs.append(make_w(sec * 250, np.zeros((250, 3), int)))
    if sec == 10:
      recs.append(g_drift(int(4.5 * 4096)))
  (tmp_path / "a.raw").write_bytes(b"".join(recs) + b"\xff" * 1024)
  assert rw.main([str(tmp_path / "a.raw"), "--info"]) == 0
  assert "251206115251,1" in capsys.readouterr().out
  assert rw.main([str(tmp_path / "a.raw"), "--info", "--no-gps-correction"]) == 0
  assert "補正しません" in capsys.readouterr().out


def converted_inputs(tmp_path, monkeypatch, seconds=130):
  rng = np.random.default_rng(5)
  write_raw(tmp_path / "x_1.raw", rng.integers(-500, 500, (seconds * 250, 3)))
  files, start = rw.open_inputs([tmp_path / "x_1.raw"])
  header = files[0].header
  offs, gains = [[0] * 3] * 2, [[1.0] * 3] * 2
  return files, start, header, offs, gains


def test_source_zip_name(tmp_path, monkeypatch):
  files, start, *_ = converted_inputs(tmp_path, monkeypatch)
  assert rw.source_zip_name("0970355225", "Mimatsu", files, start) == "0970355225_Mimatsu(251206-251206).zip"


def test_daily_zip_writer_appends_only_missing(tmp_path, monkeypatch):
  files, start, header, offs, gains = converted_inputs(tmp_path, monkeypatch)
  daily = tmp_path / "daily"
  daily.mkdir()
  # 前からある日の ZIP(ほかの拠点のファイルと、同じ分のファイルが 1 つ入っている)
  with zipfile.ZipFile(daily / "251206.zip", "w") as z:
    z.writestr("1070432003/251206/1070432003_2512061152.win", b"other")
    z.writestr("0970355225/251206/0970355225_2512061152.win", b"old")
  w = rw.convert(files, start, header, offs, gains, rw.DailyZipWriter(daily))
  names = sorted(zipfile.ZipFile(daily / "251206.zip").namelist())
  assert names == ["0970355225/251206/0970355225_2512061152.win", "0970355225/251206/0970355225_2512061153.win",
                   "0970355225/251206/0970355225_2512061154.win", "1070432003/251206/1070432003_2512061152.win"]
  assert zipfile.ZipFile(daily / "251206.zip").read("0970355225/251206/0970355225_2512061152.win") == b"old"
  assert (w.count, w.skipped, w.added) == (2, 1, {"251206": 2})
  assert w.appended == {"251206": {datetime(2025, 12, 6, 11)}}
  assert not list(daily.glob("*.part"))
  # 同じものを ZIP にした中身と同じ
  out = tmp_path / "src.zip"
  rw.convert(files, start, header, offs, gains, rw.ZipWriter(out, "0970355225"))
  src = zipfile.ZipFile(out)
  assert src.read("0970355225/251206/0970355225_2512061153.win") == \
      zipfile.ZipFile(daily / "251206.zip").read("0970355225/251206/0970355225_2512061153.win")


def test_convert_stopped_leaves_no_partial_zip(tmp_path, monkeypatch):
  files, start, header, offs, gains = converted_inputs(tmp_path, monkeypatch)

  def stop(done, total):
    raise KeyboardInterrupt
  out = tmp_path / "src.zip"
  with pytest.raises(KeyboardInterrupt):
    rw.convert(files, start, header, offs, gains, rw.ZipWriter(out, "0970355225"), progress=stop)
  daily = tmp_path / "daily"
  with pytest.raises(KeyboardInterrupt):
    rw.convert(files, start, header, offs, gains, rw.DailyZipWriter(daily), progress=stop)
  assert sorted(p.name for p in tmp_path.iterdir()) == ["x_1.raw"]


# いくつかの拠点・回収の分は、回収ごとに分ける(カードの順に並べる)
def test_open_groups_by_station_and_start(tmp_path):
  v = np.zeros((500, 3), np.int64)
  write_raw(tmp_path / "a_2.raw", v, first=500, card=2)
  write_raw(tmp_path / "a_1.raw", v, card=1)
  write_raw(tmp_path / "b_1.raw", v, station="1070432003")
  write_raw(tmp_path / "c_1.raw", v, start="20060501000000")
  groups = rw.open_groups([tmp_path / n for n in ("b_1.raw", "a_2.raw", "c_1.raw", "a_1.raw")])
  assert [[f.path.name for f in fs] for fs, _ in groups] == [["a_1.raw", "a_2.raw"], ["c_1.raw"], ["b_1.raw"]]
  assert groups[0][1] < groups[1][1]
  assert [fs[0].header["station"] for fs, _ in groups] == ["0970355225", "0970355225", "1070432003"]


def test_open_groups_rejects_same_card_twice(tmp_path):
  v = np.zeros((500, 3), np.int64)
  write_raw(tmp_path / "a.raw", v)
  write_raw(tmp_path / "b.raw", v)
  with pytest.raises(rw.RawError, match="1 枚目が 2 つ"):
    rw.open_groups([tmp_path / "a.raw", tmp_path / "b.raw"])


# 設定値が書かれないまま記録したカード(拠点ID 0000000000)を、ほかの拠点として変換する(--as-station)
def test_as_station(tmp_path):
  v = np.zeros((500, 3), np.int64)
  write_raw(tmp_path / "a.raw", v, station="0000000000")
  files, _ = rw.open_inputs([tmp_path / "a.raw"])
  cal = {"1070432003": {"channels": {(ch, m): (0.0, 1.0) for ch in (0x8D12, 0x8D10, 0x8D11) for m in rw.MODES},
                        "keys": {"x"}}}
  rw.as_station(files, "1070432003", cal, report=lambda s: None)
  assert files[0].header["station"] == "1070432003"
  assert files[0].header["channels"] == [0x8D10, 0x8D11, 0x8D12]
  with pytest.raises(rw.RawError, match="校正値"):
    rw.as_station(files, "9999999999", cal)


# GPS の文の位置($GPGGA。度分を度に)
def test_gga_position():
  rec = (b"G\0" + b"20051208060322" + b"\0" * 32
         + b"$GPGGA,060322,4232.6000,N,14047.1000,E,0,05,00.45,000020.0,M,0033.6,M,,\r").ljust(1024, b"\0")
  lat, lon = rw.gga_position(rec)
  assert abs(lat - (42 + 32.6 / 60)) < 1e-9 and abs(lon - (140 + 47.1 / 60)) < 1e-9
  assert rw.gga_position(b"G\0" + b"\0" * 1022) is None


# 設定値が書かれないまま記録したか(拠点ID 0000000000・チャンネル f000〜)。チャンネル表に無いだけの機体は違う
def test_is_unset():
  assert rw.is_unset({"station": "0000000000", "channels": [0xF000, 0xF001, 0xF002]})
  assert rw.is_unset({"station": "1234567890", "channels": [0xF000, 0xF001, 0xF002]})
  assert not rw.is_unset({"station": "1472580096", "channels": [0x8D04, 0x8D05, 0x8D06]})
