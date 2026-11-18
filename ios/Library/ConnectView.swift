import SwiftUI

/// First launch: paste the LAN address the server prints; it is checked against `/api/stats` before saving.
struct ConnectView: View {
    @EnvironmentObject private var model: AppModel
    @State private var address = ""
    @State private var isChecking = false
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("连接图书馆服务器").font(.title2.bold())
                Spacer()
                if model.replacedConfig != nil {
                    Button("取消") { model.cancelServerChange() }
                }
            }
            Text("在电脑上启动服务器后，把终端打印的局域网或 Tailscale 地址（含 token）粘贴到这里，App 会自动记住另一个地址，并在两者间切换。模拟器可直接用 http://127.0.0.1:8765")
                .font(.footnote)
                .foregroundStyle(.secondary)
            TextField("http://192.168.1.5:8765/?token=…", text: $address)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if let errorText {
                Text(errorText).font(.footnote).foregroundStyle(.red)
            }
            Button {
                Task { await connect() }
            } label: {
                HStack {
                    if isChecking { ProgressView() }
                    Text(isChecking ? "正在连接…" : "连接")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(address.isEmpty || isChecking)
            Spacer()
        }
        .padding(24)
    }

    private func connect() async {
        errorText = nil
        isChecking = true
        defer { isChecking = false }
        do {
            let config = try ServerConfig.parse(address)
            let client = ServerClient(config: config)
            let response = try await client.get("/api/stats")
            guard response.status == 200 else {
                errorText = response.status == 403 ? "token 不对：请粘贴服务器终端打印的完整地址" : "服务器返回 \(response.status)"
                return
            }
            // The other address is fetched this once; after that the list changes only through 同步地址 in settings
            let reported = await client.fetchReportedAddresses() ?? []
            model.connect(ServerConfig(baseURL: config.baseURL, token: config.token, addresses: reported)
                .adding([config.baseURL]))
        } catch {
            errorText = "连不上：\(error.localizedDescription)"
        }
    }
}
