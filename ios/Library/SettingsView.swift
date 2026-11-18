import SwiftUI

/// Server address and downloaded books, opened from the ⚙️ button the page shows inside the app.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var books: [DownloadedBook] = []
    @State private var readThroughBytes: Int64 = 0
    @State private var addresses: [URL] = []
    @State private var activeAddress: URL?
    @State private var isSyncing = false
    @State private var syncResult: String?

    struct DownloadedBook: Identifiable {
        let id: Int
        let title: String
        let bytes: Int64
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(addresses, id: \.self) { address in
                        HStack {
                            Text(address.absoluteString).lineLimit(1)
                            Spacer()
                            if address == activeAddress { Text("使用中").foregroundStyle(.secondary) }
                        }
                    }
                    .onDelete(perform: removeAddresses)
                    .deleteDisabled(addresses.count <= 1)
                    Button(isSyncing ? "正在同步…" : "同步地址") { Task { await syncAddresses() } }
                        .disabled(isSyncing || model.connection == nil)
                    Button("更换服务器", role: .destructive) { model.startServerChange() }
                } header: {
                    Text("服务器")
                } footer: {
                    Text(syncResult ?? "地址只在点「同步地址」时从服务器更新；不再用的地址左滑删除。")
                }
                Section {
                    Button("清除阅读缓存（\(Self.format(readThroughBytes))）") {
                        model.store.clearReadThrough()
                        reload()
                    }
                    .disabled(readThroughBytes == 0)
                } footer: {
                    Text("在线读过的内容会自动缓存，断网时可接着读；超过 \(Self.format(OfflineStore.readThroughLimit)) 先删最久没读的书。")
                }
                Section("已下载（\(books.count) 本，\(totalSize)）") {
                    if books.isEmpty { Text("在书籍详情的 ⋯ 菜单里选「下载离线」").foregroundStyle(.secondary) }
                    ForEach(books) { book in
                        HStack {
                            Text(book.title).lineLimit(1)
                            Spacer()
                            Text(Self.format(book.bytes)).foregroundStyle(.secondary)
                        }
                    }
                    .onDelete(perform: remove)
                }
            }
            .navigationTitle("设置")
            .toolbar { Button("完成") { dismiss() } }
            .onAppear(perform: reload)
        }
    }

    private var totalSize: String { Self.format(books.reduce(0) { $0 + $1.bytes }) }

    private func syncAddresses() async {
        guard let client = model.connection?.client else { return }
        isSyncing = true
        defer { isSyncing = false }
        guard await client.probe() else {
            syncResult = "同步失败：连不上服务器"
            return
        }
        guard let reported = await client.fetchReportedAddresses() else {
            syncResult = "同步失败：服务器没有返回地址"
            return
        }
        let added = client.addAddresses(reported)
        syncResult = added > 0 ? "已同步，新增 \(added) 个地址" : "已同步，地址没有变化"
        reload()
    }

    private func removeAddresses(at offsets: IndexSet) {
        guard let client = model.connection?.client else { return }
        offsets.map { addresses[$0] }.forEach(client.removeAddress)
        reload()
    }

    private func reload() {
        let store = model.store
        addresses = model.connection?.client.config.addresses ?? []
        activeAddress = model.connection?.client.activeBaseURL
        readThroughBytes = store.readThroughSize()
        books = store.details().compactMap { detail in
            guard let id = (detail["id"] as? NSNumber)?.intValue else { return nil }
            return DownloadedBook(id: id, title: detail["title"] as? String ?? "第 \(id) 本书", bytes: store.sizeOfBook(id))
        }
    }

    private func remove(at offsets: IndexSet) {
        for index in offsets {
            let id = books[index].id
            if let downloader = model.connection?.downloader {
                downloader.remove(bookID: id)
            } else {
                model.store.removeBook(id)
            }
        }
        reload()
    }

    private static func format(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .memory)
    }
}
