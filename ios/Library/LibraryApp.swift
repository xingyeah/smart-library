import SwiftUI

@main
struct LibraryApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    // Same as --bg in frontend/index.html, so the safe areas match the page
    static let pageBackground = Color(red: 0x0f / 255, green: 0x11 / 255, blue: 0x17 / 255)

    var body: some View {
        ZStack {
            Self.pageBackground.ignoresSafeArea()
            if let connection = model.connection {
                BrowserView(connection: connection)
                    .id(connection.config.baseURL)
            } else {
                ConnectView()
            }
        }
        .sheet(isPresented: $model.isShowingSettings) { SettingsView() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await model.refreshConnection() } }
        }
    }
}

struct BrowserView: View {
    /// The connection notice shows this long; while offline the page itself keeps a small marker.
    private static let noticeDuration: UInt64 = 3_000_000_000

    let connection: Connection
    @EnvironmentObject private var model: AppModel
    @ObservedObject private var downloader: BookDownloader
    @ObservedObject private var schemeHandler: LibrarySchemeHandler
    @State private var notice: ConnectionNotice?
    @State private var noticeTask: Task<Void, Never>?
    @State private var isPageReady = false

    init(connection: Connection) {
        self.connection = connection
        downloader = connection.downloader
        schemeHandler = connection.schemeHandler
    }

    var body: some View {
        LibraryWebView(connection: connection, store: model.store,
                       onOpenSettings: { model.isShowingSettings = true },
                       onPageReady: { isPageReady = true })
            .overlay {
                if !isPageReady {
                    ZStack {
                        RootView.pageBackground.ignoresSafeArea()
                        LaunchStatusView(stage: launchStage)
                    }
                    .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.25), value: isPageReady)
            .overlay(alignment: .bottom) {
                VStack(spacing: 8) {
                    if let status = downloader.status { DownloadBanner(status: status, onDismiss: downloader.dismissError) }
                    if let notice { ConnectionNoticeBadge(notice: notice) }
                }
                .padding(.bottom, 72)
                .animation(.easeOut(duration: 0.2), value: notice)
            }
            .onChange(of: schemeHandler.isOffline) { _, isOffline in show(isOffline ? .offline : .reconnected) }
    }

    private var launchStage: LaunchStatusView.Stage {
        guard schemeHandler.hasCheckedServer else { return .connecting }
        return schemeHandler.isOffline ? .openingOffline : .loadingShelf
    }

    private func show(_ newNotice: ConnectionNotice) {
        noticeTask?.cancel()
        notice = newNotice
        noticeTask = Task {
            try? await Task.sleep(nanoseconds: Self.noticeDuration)
            if !Task.isCancelled { notice = nil }
        }
    }
}

private struct DownloadBanner: View {
    let status: BookDownloader.Status
    let onDismiss: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            if let error = status.error {
                Text(error).lineLimit(2)
                Button("关闭", action: onDismiss)
            } else {
                ProgressView(value: Double(status.done), total: Double(max(status.total, status.done, 1)))
                    .frame(width: 80)
                Text("下载离线：\(status.title)").lineLimit(1)
            }
        }
        .font(.footnote)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: Capsule())
        .padding(.horizontal, 16)
    }
}

private enum ConnectionNotice {
    case offline, reconnected
}

private struct ConnectionNoticeBadge: View {
    let notice: ConnectionNotice

    var body: some View {
        Label(notice == .offline ? "离线模式：只显示本机缓存的书" : "已重新连接",
              systemImage: notice == .offline ? "wifi.slash" : "wifi")
            .font(.caption)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial, in: Capsule())
    }
}
