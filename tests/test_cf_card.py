# cf_card.py(CF カードの読み込み・初期化)のテスト
#   本物のカード(authopen)は使わず、ふつうのファイルをカードの代わりに開く
import hashlib
import os
import plistlib
import sys
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import cf_card
import make_cf_image

SIZE = 64 * 1024 + 4096     # カードの代わりの大きさ(CHUNK の倍数でない終わりも試す)


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
  monkeypatch.setattr(cf_card, "CHUNK", 8192)
  monkeypatch.setattr(cf_card, "IDENTIFY_SIZE", 4096)


def setting():
  return make_cf_image.make_setting("1070432003", ("42", "32.1199"), ("140", "46.9595"), 17.1,
                                    ["8D10", "8D11", "8D12"], cards=2, sensor=1000)


def recorded_card(path, index=1):
  h = bytearray(setting())
  h[0x16] = index
  body = bytes([ord("G")]) + bytes(1023) + bytes([ord("W")]) * 1024
  data = bytes(h) + body + bytes([0xFF]) * (SIZE - len(h) - len(body))
  path.write_bytes(data)
  return data


def device(path, write=False):
  fd = os.open(path, os.O_RDWR if write else os.O_RDONLY)
  return cf_card.Device(fd, path.stat().st_size)


def test_read_card(tmp_path):
  card = tmp_path / "card"
  data = recorded_card(card)
  out = tmp_path / "bamba_20261115out_1.img"
  seen = []
  with device(card) as dev:
    stats = {}
    digest = cf_card.read_card(dev, out, verify=True, progress=lambda d, t, s: seen.append((d, t, s)), stats=stats)
  assert set(stats) == {"読み込み", "確認"} and all(v >= 0 for v in stats.values())
  assert out.read_bytes() == data
  assert digest == hashlib.sha256(data).hexdigest()
  assert (tmp_path / (out.name + ".sha256")).read_text() == f"{digest}  {out.name}\n"
  assert seen[-1][0] == seen[-1][1] == 2 * SIZE
  assert {s for _, _, s in seen} == {"読み込み", "確認(もう一度読む)"}


def test_read_card_cancel_leaves_nothing(tmp_path):
  card = tmp_path / "card"
  recorded_card(card)
  out = tmp_path / "x.img"
  with device(card) as dev, pytest.raises(cf_card.Cancelled):
    cf_card.read_card(dev, out, cancelled=lambda: True)
  assert list(tmp_path.iterdir()) == [card]


def test_read_card_detects_unstable_read(tmp_path, monkeypatch):
  card = tmp_path / "card"
  recorded_card(card)
  calls = {"n": 0}
  real = cf_card.Device.pread

  def flaky(self, n, offset):
    b = real(self, n, offset)
    calls["n"] += 1
    return b if calls["n"] != len(range(0, SIZE, cf_card.CHUNK)) + 1 else bytes(len(b))
  monkeypatch.setattr(cf_card.Device, "pread", flaky)
  out = tmp_path / "x.img"
  with device(card) as dev, pytest.raises(cf_card.CardError, match="2 回読んだ"):
    cf_card.read_card(dev, out)
  assert not out.exists() and not (tmp_path / "x.img.part").exists()


def test_write_card(tmp_path):
  card = tmp_path / "card"
  recorded_card(card)
  s = setting()
  with device(card, write=True) as dev:
    stats = {}
    cf_card.write_card(dev, s, verify=True, stats=stats)
  assert set(stats) == {"書き込み", "確認"}
  data = card.read_bytes()
  assert data[:1024] == s and set(data[1024:]) == {0xFF} and len(data) == SIZE
  # make_cf_image のイメージと同じ
  img = tmp_path / "init.img"
  make_cf_image.write_image(img, s, SIZE)
  assert img.read_bytes() == data


def test_write_card_verify_fails(tmp_path, monkeypatch):
  card = tmp_path / "card"
  recorded_card(card)
  monkeypatch.setattr(cf_card.Device, "pread", lambda self, n, off: bytes(n))
  with device(card, write=True) as dev, pytest.raises(cf_card.CardError, match="読み直した"):
    cf_card.write_card(dev, setting())


def test_card_info(tmp_path):
  card = tmp_path / "card"
  recorded_card(card, index=2)
  info = cf_card.card_info(card.read_bytes()[:4096])
  assert (info.station, info.cards, info.index, info.has_data) == ("1070432003", 2, 2, True)
  assert info.location == "4232.1199,N,14046.9595,E000017.1"
  assert "2 枚目(全 2 枚)・記録あり" in cf_card.card_summary(info)
  assert cf_card.card_summary(info).endswith("北緯 42°32.1199′・東経 140°46.9595′・標高 17.1 m")
  assert cf_card.location_text("4233.6000,N,14050.6000,E000140.0") == "北緯 42°33.6000′・東経 140°50.6000′・標高 140.0 m"
  assert cf_card.location_text("broken") == "位置 broken"
  blank = setting() + bytes([0xFF]) * 3072
  info = cf_card.card_info(blank)
  assert (info.index, info.has_data) == (0, False)
  assert "番号なし" in cf_card.card_summary(info) and "記録なし" in cf_card.card_summary(info)
  assert "ロガーのカードではない" in cf_card.card_summary(cf_card.card_info(bytes(4096)))


def test_find_image(tmp_path):
  card = tmp_path / "card"
  data = recorded_card(card)
  (tmp_path / "a.img").write_bytes(data)
  (tmp_path / "b.img").write_bytes(bytes(SIZE))
  (tmp_path / "short.img").write_bytes(data[:100])
  assert cf_card.find_image(data[:4096], tmp_path) == ["a.img"]
  assert cf_card.find_image(data[:4096], tmp_path / "none") == []


def fake_diskutil(disks):
  def run(cmd, capture_output=True):
    assert cmd[2] == "-plist"
    args = [cmd[1]] + cmd[3:]
    if args == ["list", "external", "physical"]:
      out = {"WholeDisks": list(disks)}
    elif args[0] == "info":
      out = disks[args[1]]["info"]
    elif args[0] == "list":
      out = {"AllDisksAndPartitions": disks[args[1]]["parts"]}
    else:
      raise AssertionError(cmd)
    return SimpleNamespace(returncode=0, stdout=plistlib.dumps(out), stderr=b"")
  return run


def test_list_disks():
  run = fake_diskutil({
    "disk7": {"info": {"DeviceNode": "/dev/disk7", "TotalSize": make_cf_image.CARD_SIZE, "MediaName": "CF Reader",
                       "Internal": False},
              "parts": [{"DeviceIdentifier": "disk7", "Size": make_cf_image.CARD_SIZE}]},
    "disk4": {"info": {"DeviceNode": "/dev/disk4", "TotalSize": 8001563221504, "MediaName": "Samsung",
                       "Internal": False},
              "parts": [{"DeviceIdentifier": "disk4", "Partitions": [{"MountPoint": "/Volumes/Samsung8TB"}]}]},
  })
  disks = cf_card.list_disks(run)
  assert [(d.node, d.is_card, d.mounted) for d in disks] == [("/dev/disk7", True, False), ("/dev/disk4", False, True)]
  assert disks[0].raw_node == "/dev/rdisk7"


def test_device_open_refuses_other_disks():
  disk = cf_card.Disk("/dev/disk4", 8001563221504, "Samsung", False, True)
  with pytest.raises(cf_card.CardError, match="CF カードではありません"):
    cf_card.Device.open(disk, write=True)
  internal = cf_card.Disk("/dev/disk0", make_cf_image.CARD_SIZE, "SSD", True, False)
  with pytest.raises(cf_card.CardError):
    cf_card.Device.open(internal)


def test_authopen_failure(monkeypatch, tmp_path):
  # パスワードの画面を取り消したとき(authopen が fd を渡さずに終わる)
  fake = tmp_path / "authopen"
  fake.write_text("#!/bin/sh\necho 'authopen: user cancelled' >&2\nexit 1\n")
  fake.chmod(0o755)
  monkeypatch.setattr(cf_card, "AUTHOPEN", str(fake))
  with pytest.raises(cf_card.CardError, match="開けませんでした.*cancelled"):
    cf_card.authopen("/dev/rdisk99")


def test_authopen_passes_fd(monkeypatch, tmp_path):
  # authopen の代わりに、標準出力のソケットへ fd を送るスクリプト(SCM_RIGHTS)
  target = tmp_path / "target"
  target.write_bytes(b"hello card")
  fake = tmp_path / "authopen.py"
  fake.write_text("import os, socket, sys\n"
                  "s = socket.socket(fileno=1)\n"
                  "fd = os.open(sys.argv[-1], os.O_RDWR if '-o' in sys.argv else os.O_RDONLY)\n"
                  "socket.send_fds(s, [b'x'], [fd])\n")
  wrapper = tmp_path / "authopen"
  wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
  wrapper.chmod(0o755)
  monkeypatch.setattr(cf_card, "AUTHOPEN", str(wrapper))
  fd = cf_card.authopen(str(target), write=True)
  try:
    assert os.pread(fd, 5, 0) == b"hello"
    os.pwrite(fd, b"HELLO", 0)
  finally:
    os.close(fd)
  assert target.read_bytes() == b"HELLO card"


def test_load_stations_from_project():
  from app_paths import PROJECT_DIR
  stations = {s.id: s for s in cf_card.load_stations(PROJECT_DIR / "etc")}
  assert stations["1070432003"].name == "Bamba"
  assert stations["1070432003"].channels == ["8D10", "8D11", "8D12"]
  assert stations["0970355225"].name == "Mimatsu"
  assert stations["1070432002"].alt == pytest.approx(61.7)


def test_latest_setting_file(tmp_path):
  for name in ["1070432003_2512012109_a", "1070432003_2601232154_馬場邸(1枚)", "1070432003_2601232154_馬場邸(2枚)",
               "1070432002_2611142309_b"]:
    (tmp_path / name).write_bytes(bytes(1024))
  (tmp_path / "1070432003_2612312359_broken").write_bytes(bytes(10))
  assert cf_card.latest_setting_file("1070432003", tmp_path).name == "1070432003_2601232154_馬場邸(2枚)"
  assert cf_card.latest_setting_file("0970355225", tmp_path) is None
  assert cf_card.latest_setting_file("1070432003", tmp_path / "none") is None


def test_deg_min_and_name():
  assert cf_card.deg_min(42.53533) == (42, 32.1198)
  assert cf_card.deg_min(140.99999999) == (141, 0.0)
  assert cf_card.image_name("Bamba", 2, datetime(2026, 8, 26, 8, 17)) == "bamba_20260826in_2.img"
  assert cf_card.image_name("Bamba", 1) == f"bamba_{date.today():%Y%m%d}out_1.img"


def test_record_start(tmp_path):
  g = (b"G\0" + b"20060422025241").ljust(1024, b"\0")
  w = bytes([ord("W")]) * 1024
  for name, body, want in [("card1", g + w, datetime(2025, 12, 6, 11, 52, 41)),
                           ("card2", w * 40 + g, datetime(2025, 12, 6, 11, 52, 41)),  # 2 枚目: 'W' のあとに 'G'
                           ("blank", b"", None),
                           ("broken", bytes([ord("G")]) + bytes(1023), None)]:
    card = tmp_path / name
    card.write_bytes((setting() + body).ljust(SIZE, b"\xff"))
    with device(card) as dev:
      assert cf_card.record_start(dev) == want, name


# 記録の範囲(二分探索で 0xFF の所を探す)
def test_card_range(tmp_path):
  def w(idx, n):
    return (b"W\0\0\x44" + n.to_bytes(2, "big") + idx.to_bytes(4, "big")).ljust(1024, b"\0")
  g = (b"G\0" + b"20060422025241").ljust(1024, b"\0")
  start = datetime(2025, 12, 6, 11, 52, 41)
  n_rec = SIZE // 1024
  for name, body, want in [
      ("card1", g + b"".join(w(i * 250, 250) for i in range(10)), (start, start.replace(second=51), False)),
      ("card2", w(2500, 250) + g + w(2750, 250), (start.replace(second=51), start.replace(second=53), False)),
      ("full", g + b"".join(w(i * 250, 250) for i in range(n_rec - 2)), None),
      ("blank", b"", None)]:
    card = tmp_path / name
    card.write_bytes((setting() + body).ljust(SIZE, b"\xff")[:SIZE])
    with device(card) as dev:
      got = cf_card.card_range(dev, start if body else None)
    if name == "full":
      assert got[2] is True and got[0] == start
    else:
      assert got == want, name


# 位置からいちばん近い拠点
def test_nearest_station():
  st = [cf_card.Station("1070432003", "Bamba", [], 42.53533, 140.78266),
        cf_card.Station("0970355243", "Hokudai", [], 42.55947, 140.84252),
        cf_card.Station("9999999999", "NoPos", [])]
  s, km = cf_card.nearest_station(st, (42 + 32.6 / 60, 140 + 47.1 / 60))
  assert s.name == "Bamba" and 0.5 < km < 1.5
  assert cf_card.nearest_station(st, None) == (None, None)


# /dev のディスクの名前(パーティションは除く)
def test_disk_nodes(tmp_path):
  for n in ("disk4", "disk4s1", "disk12", "rdisk4", "null"):
    (tmp_path / n).write_bytes(b"")
  assert {n for n, _ in cf_card.disk_nodes(tmp_path)} == {"disk4", "disk12"}
  assert cf_card.disk_nodes(tmp_path / "none") is None
