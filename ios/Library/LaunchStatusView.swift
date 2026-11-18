import SwiftUI

/// Covers the web view until the page has drawn, so the first seconds (the address probe, then the shell)
/// show what the app is doing instead of a blank screen.
struct LaunchStatusView: View {
    enum Stage: Equatable {
        case connecting, loadingShelf, openingOffline

        var title: String {
            switch self {
            case .connecting: "正在连接书库"
            case .loadingShelf: "已连接，正在打开书架"
            case .openingOffline: "连不上服务器"
            }
        }

        var detail: String {
            switch self {
            case .connecting: "依次尝试局域网和 Tailscale 地址"
            case .loadingShelf: "马上就好"
            case .openingOffline: "正在打开本机缓存的书"
            }
        }

        var symbol: String {
            switch self {
            case .connecting: "antenna.radiowaves.left.and.right"
            case .loadingShelf: "books.vertical.fill"
            case .openingOffline: "wifi.slash"
            }
        }
    }

    // Same as --accent in frontend/assets/css/main.css
    private static let accent = Color(red: 0x0e / 255, green: 0x7c / 255, blue: 0x70 / 255)
    private static let pulseDuration = 1.6
    private static let ringCount = 2

    let stage: Stage
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    var body: some View {
        VStack(spacing: 28) {
            ZStack {
                if stage != .openingOffline && !reduceMotion {
                    ForEach(0..<Self.ringCount, id: \.self) { ring in
                        Circle()
                            .stroke(Self.accent.opacity(0.6), lineWidth: 2)
                            .scaleEffect(isPulsing ? 1.9 : 1)
                            .opacity(isPulsing ? 0 : 1)
                            .animation(
                                .easeOut(duration: Self.pulseDuration).repeatForever(autoreverses: false)
                                    .delay(Double(ring) * Self.pulseDuration / Double(Self.ringCount)),
                                value: isPulsing)
                    }
                }
                Circle().fill(stage == .openingOffline ? Color.gray.opacity(0.35) : Self.accent)
                Image(systemName: stage.symbol)
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.white)
                    .contentTransition(.symbolEffect(.replace))
            }
            .frame(width: 84, height: 84)

            VStack(spacing: 8) {
                Text(stage.title).font(.headline).foregroundStyle(.white)
                Text(stage.detail).font(.subheadline).foregroundStyle(.white.opacity(0.6))
            }
            .multilineTextAlignment(.center)
            .animation(.easeInOut(duration: 0.2), value: stage)
        }
        .padding(.horizontal, 32)
        .accessibilityElement(children: .combine)
        .onAppear { isPulsing = true }
    }
}
