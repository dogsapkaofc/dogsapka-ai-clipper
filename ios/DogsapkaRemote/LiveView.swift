import SwiftUI

struct LiveView: View {
    @EnvironmentObject var store: RemoteStore

    private var real: [StreamerItem] { store.streamers.filter { !$0.simulation } }
    private var anyPaused: Bool { real.contains { !$0.monitoring } || real.isEmpty }

    var body: some View {
        NavigationStack {
            List {
                ErrorBanner().listRowBackground(Color.clear)
                if let st = store.status {
                    HStack {
                        StatBox(value: "\(st.liveSessions)", title: "élő")
                        StatBox(value: "\(st.monitored)/\(st.streamers)", title: "figyelve")
                        StatBox(value: "\(st.queuePending)", title: "sorban")
                    }
                    .listRowBackground(Color.clear)
                }
                Section {
                    ForEach(store.streamers) { s in StreamerRow(s: s) }
                } footer: {
                    if store.streamers.isEmpty { Text("Még nincs streamer – a számítógépen tudod hozzáadni.") }
                }
            }
            .navigationTitle("Élő")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        let enable = anyPaused
                        store.run({ try await $0.setMonitoringAll(enable) }, then: .live)
                    } label: {
                        Label(anyPaused ? "Összes indítása" : "Összes leállítása",
                              systemImage: anyPaused ? "play.fill" : "pause.fill")
                    }
                    .disabled(real.isEmpty)
                }
            }
            .refreshable { await store.refresh(.live) }
            .onAppear { store.startPolling(.live) }
            .onDisappear { store.stopPolling() }
        }
    }
}

struct StatBox: View {
    let value: String
    let title: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(.title2.bold().monospacedDigit())
            Text(title).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 14))
    }
}

struct StreamerRow: View {
    @EnvironmentObject var store: RemoteStore
    let s: StreamerItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(s.name).font(.headline)
                Pill(text: s.platform == "youtube" ? "YouTube" : "Twitch",
                     color: s.platform == "youtube" ? Theme.red : Theme.purple2)
                Spacer()
                if let st = s.stats, st.recording {
                    Pill(text: "● FELVÉTEL", color: Theme.red)
                } else if s.live {
                    Pill(text: "ÉLŐ", color: Theme.red)
                } else {
                    Pill(text: "OFFLINE", color: .gray)
                }
            }
            if !s.title.isEmpty {
                Text(s.title).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            if let st = s.stats {
                HStack(alignment: .bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Pontszám").font(.caption2).foregroundStyle(.secondary)
                        Text("\(Int(st.score))")
                            .font(.title.bold().monospacedDigit())
                            .foregroundStyle(st.score >= st.threshold ? Theme.amber : .primary)
                    }
                    Sparkline(values: st.scores, threshold: st.threshold)
                        .frame(height: 44)
                }
                Text(String(format: "Chat %.1f/s · puffer %.0f/%.0f s · ★ %ld",
                            st.chatRate, st.bufferS, st.bufferTarget, st.highlights))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !s.simulation {
                Toggle(isOn: Binding(get: { s.monitoring }, set: { on in
                    store.run({ try await $0.setMonitoring(s.id, on) }, then: .live)
                })) {
                    Text("Figyelés").font(.subheadline)
                }
                .tint(Theme.purple)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Small score graph with the threshold as a dashed line.
struct Sparkline: View {
    let values: [Double]
    let threshold: Double

    var body: some View {
        GeometryReader { g in
            let w = g.size.width, h = g.size.height
            let pts = values.enumerated().map { i, v in
                CGPoint(x: values.count > 1 ? w * CGFloat(i) / CGFloat(values.count - 1) : 0,
                        y: h - h * CGFloat(min(max(v, 0), 100) / 100))
            }
            ZStack {
                Path { p in
                    let y = h - h * CGFloat(threshold / 100)
                    p.move(to: CGPoint(x: 0, y: y))
                    p.addLine(to: CGPoint(x: w, y: y))
                }
                .stroke(Theme.amber.opacity(0.7), style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
                if pts.count > 1 {
                    Path { p in
                        p.move(to: pts[0])
                        pts.dropFirst().forEach { p.addLine(to: $0) }
                    }
                    .stroke(Theme.purple2, style: StrokeStyle(lineWidth: 2, lineJoin: .round))
                }
            }
        }
    }
}
