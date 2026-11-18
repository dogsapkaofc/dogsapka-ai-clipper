import SwiftUI

struct QueueView: View {
    @EnvironmentObject var store: RemoteStore

    var body: some View {
        NavigationStack {
            List {
                ErrorBanner().listRowBackground(Color.clear)
                if store.jobs.isEmpty {
                    Text("Nincs feladat a sorban.").foregroundStyle(.secondary)
                }
                ForEach(store.jobs) { j in JobRow(job: j) }
            }
            .navigationTitle("Feldolgozás")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    let paused = store.status?.queuePaused ?? false
                    Button {
                        store.run({ try await $0.pauseQueue(!paused) }, then: .queue)
                    } label: {
                        Label(paused ? "Folytatás" : "Mind szünet", systemImage: paused ? "play.fill" : "pause.fill")
                    }
                }
            }
            .refreshable { await store.refresh(.queue) }
            .onAppear { store.startPolling(.queue) }
            .onDisappear { store.stopPolling() }
        }
    }
}

struct JobRow: View {
    @EnvironmentObject var store: RemoteStore
    let job: JobItem

    private var color: Color {
        switch job.status {
        case "running": return Theme.cyan
        case "done": return Theme.green
        case "failed": return Theme.red
        case "paused": return Theme.amber
        default: return .gray
        }
    }

    private var statusText: String {
        ["queued": "sorban", "running": "fut", "paused": "szünet", "done": "kész", "failed": "hiba",
         "cancelled": "megszakítva"][job.status] ?? job.status
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("#\(job.id) · \(job.name)").font(.subheadline.bold())
                Spacer()
                Pill(text: statusText, color: color)
            }
            if !job.clip.isEmpty {
                Text(job.clip).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            ProgressView(value: job.progress).tint(color)
            Text(job.status == "failed" ? job.error : job.stage)
                .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
        }
        .padding(.vertical, 4)
        .swipeActions(edge: .trailing) {
            if ["running", "queued", "paused"].contains(job.status) {
                Button(role: .destructive) { act("cancel") } label: { Label("Mégse", systemImage: "xmark") }
            }
            if ["failed", "cancelled"].contains(job.status) {
                Button { act("retry") } label: { Label("Újra", systemImage: "arrow.clockwise") }.tint(Theme.purple)
            }
        }
        .swipeActions(edge: .leading) {
            if ["running", "queued"].contains(job.status) {
                Button { act("pause") } label: { Label("Szünet", systemImage: "pause.fill") }.tint(Theme.amber)
            }
            if job.status == "paused" {
                Button { act("resume") } label: { Label("Folytatás", systemImage: "play.fill") }.tint(Theme.green)
            }
        }
    }

    private func act(_ action: String) {
        store.run({ try await $0.job(job.id, action) }, then: .queue)
    }
}
