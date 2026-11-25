// UsuMantenFeelApp.swift
// 満点の揺れを、iPhone の振動で感じるためのテスト用のアプリ(TODO 58)。自分の iPhone に入れるだけで、配らない
//   教材の HTML(quake_wave_view.py の波形のページなど)を中で開き、ページの「▶ 振動」を Core Haptics の振動にする
import AVFoundation
import SwiftUI

@main
struct UsuMantenFeelApp: App {
  init() {
    // ページの「音」を、消音のスイッチが入っていても鳴らす(ほかのアプリの音は止めない)
    try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [.mixWithOthers])
  }

  var body: some Scene {
    WindowGroup { ContentView() }
  }
}
