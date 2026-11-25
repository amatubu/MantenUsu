// ContentView.swift
// ページの一覧。アプリに入れたページ(ビルドのときに batch_plots からコピーしたもの)と、
// 読み込んだページ(「読み込む」・Finder・ファイル App からこのアプリの書類に入れた HTML)を出す
import SwiftUI
import UniformTypeIdentifiers

struct Page: Identifiable, Hashable {
  let url: URL
  var id: URL { url }
  var title: String { url.deletingPathExtension().lastPathComponent }
}

enum PageStore {
  static var bundledDir: URL? { Bundle.main.resourceURL?.appendingPathComponent("Pages") }
  static var importedDir: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }

  static func list(_ dir: URL?) -> [Page] {
    guard let dir, let names = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { return [] }
    return names.filter { ["html", "htm"].contains($0.pathExtension.lowercased()) }
      .sorted { $0.lastPathComponent < $1.lastPathComponent }
      .map(Page.init)
  }

  static func importFiles(_ urls: [URL]) throws {
    for url in urls {
      let scoped = url.startAccessingSecurityScopedResource()
      defer { if scoped { url.stopAccessingSecurityScopedResource() } }
      let dest = importedDir.appendingPathComponent(url.lastPathComponent)
      try? FileManager.default.removeItem(at: dest)
      try FileManager.default.copyItem(at: url, to: dest)
    }
  }
}

struct ContentView: View {
  @Environment(\.scenePhase) private var scenePhase
  @State private var bundled: [Page] = []
  @State private var imported: [Page] = []
  @State private var importing = false
  @State private var errorText: String?

  var body: some View {
    NavigationStack {
      List {
        Section {
          if bundled.isEmpty { Text("ありません").foregroundStyle(.secondary) }
          ForEach(bundled) { page in NavigationLink(page.title, value: page) }
        } header: {
          Text("アプリに入れたページ")
        } footer: {
          Text("Mac の Xcode でビルドしたときの batch_plots の波形・3D の図・揺れを比べる・世界の地震のページです。")
        }
        Section {
          if imported.isEmpty { Text("ありません").foregroundStyle(.secondary) }
          ForEach(imported) { page in NavigationLink(page.title, value: page) }
            .onDelete { offsets in
              for i in offsets { try? FileManager.default.removeItem(at: imported[i].url) }
              reload()
            }
        } header: {
          Text("読み込んだページ")
        } footer: {
          Text("右上の「読み込む」か、Mac の Finder・ファイル App で、このアプリの書類に入れた HTML です。")
        }
        Section {
          Label(HapticPlayer.supported ? "この端末は振動(Core Haptics)に対応しています"
                                       : "この端末では振動できません。ページは画面の揺れで代わりにします",
                systemImage: HapticPlayer.supported ? "iphone.radiowaves.left.and.right" : "iphone.slash")
            .font(.footnote)
        }
      }
      .navigationTitle("満点の揺れを感じる")
      .navigationDestination(for: Page.self) { page in
        PageView(url: page.url)
          .navigationTitle(page.title)
          .navigationBarTitleDisplayMode(.inline)
          .ignoresSafeArea(.container, edges: .bottom)
      }
      .toolbar {
        Button("読み込む") { importing = true }
      }
      .fileImporter(isPresented: $importing, allowedContentTypes: [.html], allowsMultipleSelection: true) { result in
        do {
          try PageStore.importFiles(try result.get())
        } catch {
          errorText = error.localizedDescription
        }
        reload()
      }
      .alert("読み込めませんでした", isPresented: Binding(get: { errorText != nil }, set: { if !$0 { errorText = nil } })) {
        Button("OK", role: .cancel) {}
      } message: {
        Text(errorText ?? "")
      }
      .refreshable { reload() }
      .onAppear(perform: reload)
      .onChange(of: scenePhase) { _, phase in if phase == .active { reload() } }
    }
  }

  private func reload() {
    bundled = PageStore.list(PageStore.bundledDir)
    imported = PageStore.list(PageStore.importedDir)
  }
}
