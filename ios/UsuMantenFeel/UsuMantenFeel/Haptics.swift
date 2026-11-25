// Haptics.swift
// ページから届いた揺れの強さの並び(0〜1、interval 秒ごと)を、Core Haptics の連続した振動にして鳴らす(TODO 58)
//   1 つの連続した振動(continuous)は長さに上限があるので、CHUNK_SECONDS ごとに分ける
//   強さは、振動の強さを 1 にして、強さの曲線(hapticIntensityControl)で掛ける。
//   1 本の曲線の点は 16 個までなので、15 コマずつ(となりの曲線と端の点を共有して)曲線を並べる
import CoreHaptics
import Foundation

final class HapticPlayer {
  static let chunkSeconds = 25.0
  static let pointsPerCurve = 16

  static var supported: Bool { CHHapticEngine.capabilitiesForHardware().supportsHaptics }

  private var engine: CHHapticEngine?
  private var player: CHHapticAdvancedPatternPlayer?

  func play(interval: Double, intensity: [Double], sharpness: Double) {
    stop()
    guard Self.supported, interval > 0, intensity.count >= 2 else { return }
    do {
      let engine = try readyEngine()
      let pattern = try Self.pattern(interval: interval, intensity: intensity, sharpness: sharpness)
      let player = try engine.makeAdvancedPlayer(with: pattern)
      try player.start(atTime: CHHapticTimeImmediate)
      self.player = player
    } catch {
      print("振動を鳴らせませんでした: \(error)")
    }
  }

  func stop() {
    try? player?.stop(atTime: CHHapticTimeImmediate)
    player = nil
  }

  private func readyEngine() throws -> CHHapticEngine {
    if let engine {
      try engine.start()
      return engine
    }
    let engine = try CHHapticEngine()
    engine.playsHapticsOnly = true
    engine.isAutoShutdownEnabled = true
    // 音声のセッションが切れたときなどは作り直す(次に鳴らすときに新しく作る)
    engine.resetHandler = { [weak self] in
      self?.engine = nil
      self?.player = nil
    }
    try engine.start()
    self.engine = engine
    return engine
  }

  // 強さの並びから、振動の並び(連続した振動 + 強さの曲線)を作る
  static func pattern(interval: Double, intensity: [Double], sharpness: Double) throws -> CHHapticPattern {
    let total = Double(intensity.count - 1) * interval
    var events: [CHHapticEvent] = []
    var start = 0.0
    while start < total {
      let length = min(chunkSeconds, total - start)
      events.append(CHHapticEvent(eventType: .hapticContinuous,
                                  parameters: [CHHapticEventParameter(parameterID: .hapticIntensity, value: 1),
                                               CHHapticEventParameter(parameterID: .hapticSharpness, value: Float(sharpness))],
                                  relativeTime: start, duration: length))
      start += length
    }
    var curves: [CHHapticParameterCurve] = []
    var first = 0
    while first < intensity.count - 1 {
      let last = min(first + pointsPerCurve - 1, intensity.count - 1)
      let points = (first...last).map { i in
        CHHapticParameterCurve.ControlPoint(relativeTime: Double(i - first) * interval,
                                            value: Float(min(1, max(0, intensity[i]))))
      }
      curves.append(CHHapticParameterCurve(parameterID: .hapticIntensityControl, controlPoints: points,
                                           relativeTime: Double(first) * interval))
      first = last
    }
    return try CHHapticPattern(events: events, parameterCurves: curves)
  }
}
