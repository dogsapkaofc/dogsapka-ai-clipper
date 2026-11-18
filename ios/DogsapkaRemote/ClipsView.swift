import AVKit
import SwiftUI

struct ClipsView: View {
    @EnvironmentObject var store: RemoteStore

    var body: some View {
        NavigationStack {
            List {
                ErrorBanner().listRowBackground(Color.clear)
                if store.clips.isEmpty {
                    Text("Még nincs klip.").foregroundStyle(.secondary)
                }
                ForEach(store.clips) { c in
                    NavigationLink(value: c.id) { ClipRow(clip: c) }
                }
            }
            .navigationTitle("Klipek")
            .navigationDestination(for: Int.self) { id in
                if let clip = store.clips.first(where: { $0.id == id }) {
                    ClipDetailView(clip: clip)
                }
            }
            .refreshable { await store.refresh(.clips) }
            .onAppear { store.startPolling(.clips) }
            .onDisappear { store.stopPolling() }
        }
    }
}

struct ClipRow: View {
    let clip: ClipItem

    var body: some View {
        HStack(spacing: 12) {
            Thumbnail(clipID: clip.hasThumbnail ? clip.id : nil)
                .frame(width: 96, height: 54)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(clip.title).font(.subheadline.bold()).lineLimit(2)
                HStack(spacing: 6) {
                    Text(clip.streamer)
                    Text("· \(Int(clip.duration)) s")
                    Text("· AI \(Int(clip.score))")
                }
                .font(.caption).foregroundStyle(.secondary)
                if let e = clip.playable, !e.uploadedTo.isEmpty {
                    Text("Feltöltve: " + e.uploadedTo.joined(separator: ", "))
                        .font(.caption2).foregroundStyle(Theme.green).lineLimit(1)
                }
            }
        }
    }
}

/// Clip thumbnail loaded with the pairing token (AsyncImage cannot send auth headers).
struct Thumbnail: View {
    @EnvironmentObject var store: RemoteStore
    let clipID: Int?
    @State private var image: UIImage?

    private static let cache = NSCache<NSNumber, UIImage>()

    var body: some View {
        ZStack {
            Rectangle().fill(Theme.card)
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: "film").foregroundStyle(.secondary)
            }
        }
        .task(id: clipID) {
            guard let id = clipID else { return }
            if let cached = Self.cache.object(forKey: NSNumber(value: id)) {
                image = cached
                return
            }
            if let data = try? await store.api?.thumbnail(clip: id), let img = UIImage(data: data) {
                Self.cache.setObject(img, forKey: NSNumber(value: id))
                image = img
            }
        }
    }
}

struct ClipDetailView: View {
    @EnvironmentObject var store: RemoteStore
    let clip: ClipItem
    @State private var player: AVPlayer?
    @State private var showUpload = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let player {
                    VideoPlayer(player: player)
                        .aspectRatio(9 / 16, contentMode: .fit)
                        .frame(maxHeight: 520)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                } else {
                    ZStack {
                        RoundedRectangle(cornerRadius: 16).fill(Theme.card)
                        Text(clip.playable == nil ? "A videó még készül vagy nem elérhető." : "Betöltés…")
                            .foregroundStyle(.secondary)
                    }
                    .frame(height: 220)
                }
                Text(clip.title).font(.title3.bold())
                HStack {
                    Pill(text: clip.streamer, color: Theme.purple2)
                    Pill(text: "\(Int(clip.duration)) s", color: Theme.cyan)
                    Pill(text: "AI \(Int(clip.score))", color: Theme.green)
                }
                if let e = clip.playable {
                    if !e.uploadedTo.isEmpty {
                        Label("Feltöltve: " + e.uploadedTo.joined(separator: ", "), systemImage: "checkmark.seal.fill")
                            .font(.footnote).foregroundStyle(Theme.green)
                    }
                    Button {
                        showUpload = true
                    } label: {
                        Label("Feltöltés", systemImage: "arrow.up.circle.fill")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 4)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!e.qcPassed)
                    if !e.qcPassed {
                        Text("Ez az export nem ment át a minőségellenőrzésen.").font(.caption).foregroundStyle(Theme.amber)
                    }
                }
            }
            .padding()
        }
        .navigationTitle("Klip")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear(perform: load)
        .onDisappear { player?.pause() }
        .sheet(isPresented: $showUpload) {
            if let e = clip.playable { UploadSheet(exportID: e.id, alreadyUploaded: e.uploadedTo) }
        }
    }

    private func load() {
        guard player == nil, let api = store.api, let e = clip.playable else { return }
        // stream straight from the desktop; the token goes in the request headers
        let asset = AVURLAsset(url: api.videoURL(export: e.id),
                               options: ["AVURLAssetHTTPHeaderFieldsKey": api.authHeaders])
        player = AVPlayer(playerItem: AVPlayerItem(asset: asset))
    }
}

struct UploadSheet: View {
    @EnvironmentObject var store: RemoteStore
    @Environment(\.dismiss) private var dismiss
    let exportID: Int
    let alreadyUploaded: [String]
    @State private var selected: Set<Int> = []
    @State private var confirmed = false
    @State private var sending = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Fiókok") {
                    if store.accounts.isEmpty {
                        Text("Nincs összekötött fiók. A számítógépen: Beállítások → Közzététel → fiók hozzáadása.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(store.accounts) { a in
                        Toggle(isOn: Binding(get: { selected.contains(a.id) },
                                             set: { on in if on { selected.insert(a.id) } else { selected.remove(a.id) } })) {
                            VStack(alignment: .leading) {
                                Text(a.name)
                                Text("\(a.platform == "youtube" ? "YouTube" : "TikTok") · \(a.privacy)"
                                     + (alreadyUploaded.contains(a.name) ? " · már feltöltve" : ""))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Section {
                    Toggle("Megerősítem, hogy jogom van közzétenni ezt a tartalmat, és a streamer engedélyezte",
                           isOn: $confirmed)
                }
            }
            .navigationTitle("Feltöltés")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Mégse") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Feltöltés") {
                        sending = true
                        let ids = Array(selected)
                        store.run({ try await $0.upload(export: exportID, accounts: ids) }, then: .queue)
                        dismiss()
                    }
                    .disabled(selected.isEmpty || !confirmed || sending)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
