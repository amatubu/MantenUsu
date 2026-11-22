import os
import sys
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backend_bases import MouseEvent, PickEvent
from obspy import Stream, Trace, UTCDateTime

import station_names
import waveform_window


def make_stream(stations):
  st = Stream()
  rng = np.random.default_rng(0)
  for nm in stations:
    for c in "UNE":
      st += Trace(rng.normal(size=500), header={"station": nm, "channel": c, "sampling_rate": 100.0,
                                                "starttime": UTCDateTime(2026, 2, 2, 2, 45)})
  return st


def walk(w):
  for c in w.winfo_children():
    yield c
    yield from walk(c)


class TestClickSelectsStation(unittest.TestCase):
  """波形・ラベル・拠点名をクリックすると、下の拠点名(と音で聴く成分)が切り替わる"""

  def setUp(self):
    try:
      self.root = tk.Tk()
    except tk.TclError:
      self.skipTest("画面が使えない")
    self.root.withdraw()
    self.addCleanup(self.root.destroy)   # 波形の画面を閉じる後片付け(あとで足す)より後に動く

  def open(self, stations):
    figs = []
    real = waveform_window.create_waveform_figure
    with mock.patch.object(waveform_window, "create_waveform_figure",
                           side_effect=lambda st: figs.append(real(st)) or figs[-1]):
      win = waveform_window.open_waveform_window(self.root, make_stream(stations), "test")
    self.addCleanup(lambda: win.winfo_exists() and win.event_generate("<<CloseWaveform>>"))
    combos = [w for w in walk(win) if isinstance(w, ttk.Combobox)]
    return figs[0], combos[0], combos[2]   # 拠点名、フィルタ、成分、速さの順

  def click_axes(self, fig, ax):
    x, y = ax.transData.transform((1.0, 0.0))
    fig.canvas.callbacks.process("button_press_event",
                                 MouseEvent("button_press_event", fig.canvas, x, y, button=1))

  def pick(self, fig, artist):
    mouse = MouseEvent("button_press_event", fig.canvas, 0, 0, button=1)
    fig.canvas.callbacks.process("pick_event", PickEvent("pick_event", fig.canvas, mouse, artist))

  def test_vertical_trace_and_label(self):
    fig, station, comp = self.open(["Hokudai", "Mimatsu"])
    self.assertEqual(station.get(), station_names.full("Hokudai"))
    ax = next(a for a in fig.axes if a.get_gid() == "Mimatsu" and a.wave_channel == "E")
    self.click_axes(fig, ax)
    self.assertEqual((station.get(), comp.current()), (station_names.full("Mimatsu"), 2))
    ax = next(a for a in fig.axes if a.get_gid() == "Hokudai" and a.wave_channel == "N")
    self.pick(fig, ax.yaxis.label)
    self.assertEqual((station.get(), comp.current()), (station_names.full("Hokudai"), 1))

  def test_grid_title(self):
    fig, station, comp = self.open(["Bamba", "Hokudai", "Mimatsu", "Nizaemn"])
    title = next(a for a in fig.axes if a.get_gid() == "Nizaemn" and a.get_title()).title
    self.pick(fig, title)
    self.assertEqual(station.get(), station_names.full("Nizaemn"))
    self.assertEqual(comp.current(), 0)   # 拠点名のクリックでは成分は変えない


if __name__ == "__main__":
  unittest.main()
