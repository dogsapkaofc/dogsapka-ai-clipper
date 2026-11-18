import SwiftUI

@main
struct DogsapkaRemoteApp: App {
    @StateObject private var store = RemoteStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .tint(Theme.purple)
                .preferredColorScheme(.dark)
        }
    }
}

enum Theme {
    static let purple = Color(red: 0.545, green: 0.361, blue: 0.965)      // #8B5CF6
    static let purple2 = Color(red: 0.655, green: 0.545, blue: 0.980)     // #A78BFA
    static let cyan = Color(red: 0.133, green: 0.827, blue: 0.933)        // #22D3EE
    static let green = Color(red: 0.204, green: 0.827, blue: 0.600)       // #34D399
    static let red = Color(red: 0.957, green: 0.247, blue: 0.369)         // #F43F5E
    static let amber = Color(red: 0.984, green: 0.749, blue: 0.141)       // #FBBF24
    static let background = Color(red: 0.027, green: 0.027, blue: 0.051)  // #07070D
    static let card = Color.white.opacity(0.05)
}

struct RootView: View {
    @EnvironmentObject var store: RemoteStore

    var body: some View {
        Group {
            if store.isPaired {
                MainTabs()
            } else {
                PairView()
            }
        }
        .animation(.easeInOut, value: store.isPaired)
    }
}

struct MainTabs: View {
    var body: some View {
        TabView {
            LiveView()
                .tabItem { Label("Élő", systemImage: "dot.radiowaves.left.and.right") }
            ClipsView()
                .tabItem { Label("Klipek", systemImage: "film.stack") }
            QueueView()
                .tabItem { Label("Feldolgozás", systemImage: "square.stack.3d.up") }
            ConnectionView()
                .tabItem { Label("Kapcsolat", systemImage: "desktopcomputer") }
        }
    }
}

/// Small coloured capsule label (LIVE, platform, status...).
struct Pill: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.bold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.15), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.45), lineWidth: 1))
    }
}

/// Banner shown on top of a screen when the desktop is unreachable or reported an error.
struct ErrorBanner: View {
    @EnvironmentObject var store: RemoteStore

    var body: some View {
        if let e = store.error {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.amber)
                Text(e).font(.footnote)
                Spacer(minLength: 0)
            }
            .padding(12)
            .background(Theme.amber.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        }
    }
}
