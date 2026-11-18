import Foundation
import SwiftUI

/// App state: the paired connection and the latest data from the desktop. Polls while a screen is visible.
@MainActor
final class RemoteStore: ObservableObject {
    @Published var connection: Connection?
    @Published var status: DesktopStatus?
    @Published var streamers: [StreamerItem] = []
    @Published var clips: [ClipItem] = []
    @Published var accounts: [AccountItem] = []
    @Published var jobs: [JobItem] = []
    @Published var error: String?
    @Published var online = false

    private(set) var api: APIClient?
    private var pollTask: Task<Void, Never>?

    init() {
        if let c = Keychain.load() { use(c) }
    }

    var isPaired: Bool { connection != nil }

    func use(_ c: Connection) {
        connection = c
        api = APIClient(connection: c)
    }

    func pair(_ info: PairingInfo) async throws {
        let c = try await APIClient.pair(info)
        Keychain.save(c)
        use(c)
        await refreshAll()
    }

    func unpair() {
        stopPolling()
        Keychain.delete()
        connection = nil
        api = nil
        status = nil
        streamers = []
        clips = []
        accounts = []
        jobs = []
        error = nil
    }

    // MARK: loading

    enum Section { case live, clips, queue, connection }

    func refresh(_ section: Section) async {
        guard let api else { return }
        do {
            async let st = api.status()
            switch section {
            case .live: streamers = try await api.streamers()
            case .clips:
                clips = try await api.clips()
                accounts = try await api.accounts()
            case .queue: jobs = try await api.jobs()
            case .connection: break
            }
            status = try await st
            online = true
            error = nil
        } catch {
            handle(error)
        }
    }

    func refreshAll() async {
        for s in [Section.live, .clips, .queue] { await refresh(s) }
    }

    private func handle(_ e: Error) {
        online = false
        if case APIError.unauthorized = e {
            unpair()
            error = APIError.unauthorized.errorDescription
        } else {
            error = e.localizedDescription
        }
    }

    /// Refresh the visible screen every 2 seconds.
    func startPolling(_ section: Section) {
        stopPolling()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh(section)
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: actions

    func run(_ action: @escaping (APIClient) async throws -> Void, then section: Section) {
        guard let api else { return }
        Task {
            do {
                try await action(api)
                await refresh(section)
            } catch {
                handle(error)
            }
        }
    }
}
