// PageView.swift
// 教材の HTML を開く画面(WKWebView)。ページの「▶ 振動」から届くメッセージ(usuHaptics)で、iPhone を振動させる
//   {cmd: "play", interval: 秒, intensity: [0〜1, ...], sharpness: 0〜1} … 鳴らす / {cmd: "stop"} … 止める
//   振動できない端末(シミュレーターなど)では usuHaptics を置かない。ページは画面の揺れで代わりにする
import SwiftUI
import WebKit

struct PageView: UIViewRepresentable {
  static let handlerName = "usuHaptics"
  let url: URL

  func makeCoordinator() -> Coordinator { Coordinator() }

  func makeUIView(context: Context) -> WKWebView {
    let config = WKWebViewConfiguration()
    config.allowsInlineMediaPlayback = true
    config.mediaTypesRequiringUserActionForPlayback = []
    if HapticPlayer.supported {
      config.userContentController.add(WeakHandler(context.coordinator), name: Self.handlerName)
    }
    let web = WKWebView(frame: .zero, configuration: config)
    web.uiDelegate = context.coordinator
    web.allowsBackForwardNavigationGestures = true
    web.isInspectable = true    // Mac の Safari の「開発」メニューから中身を調べられるように
    web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    return web
  }

  func updateUIView(_ web: WKWebView, context: Context) {}

  static func dismantleUIView(_ web: WKWebView, coordinator: Coordinator) {
    coordinator.haptics.stop()
    web.configuration.userContentController.removeScriptMessageHandler(forName: handlerName)
  }

  final class Coordinator: NSObject, WKScriptMessageHandler, WKUIDelegate {
    let haptics = HapticPlayer()

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
      guard let body = message.body as? [String: Any], let cmd = body["cmd"] as? String else { return }
      switch cmd {
      case "play":
        let interval = (body["interval"] as? NSNumber)?.doubleValue ?? 0
        let intensity = (body["intensity"] as? [NSNumber])?.map(\.doubleValue) ?? []
        let sharpness = (body["sharpness"] as? NSNumber)?.doubleValue ?? 0.3
        haptics.play(interval: interval, intensity: intensity, sharpness: sharpness)
      case "stop":
        haptics.stop()
      default:
        break
      }
    }

    // 別のタブで開くリンク(3D の図から波形のページを開くときなど)は、同じ画面で開く
    func webView(_ web: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
      if let url = action.request.url {
        if url.isFileURL {
          web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        } else {
          web.load(action.request)
        }
      }
      return nil
    }

    func webView(_ web: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
      print("ページの alert: \(message)")
      completionHandler()
    }
  }
}

// WKUserContentController がメッセージの受け手を強く持ち続けないようにする
final class WeakHandler: NSObject, WKScriptMessageHandler {
  weak var target: WKScriptMessageHandler?
  init(_ target: WKScriptMessageHandler) { self.target = target }
  func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
    target?.userContentController(controller, didReceive: message)
  }
}
