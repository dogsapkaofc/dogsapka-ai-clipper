import Foundation

// JSON models of the desktop app's remote-control API (dogsapka_clipper/services/remote.py).
// Keys arrive in snake_case and are decoded with .convertFromSnakeCase.

struct DesktopStatus: Codable, Equatable {
    var version: String
    var liveSessions: Int
    var recording: Int
    var monitored: Int
    var streamers: Int
    var queuePending: Int
    var queuePaused: Bool
    var diskFreeGb: Double
}

struct LiveStats: Codable, Equatable {
    var score: Double
    var threshold: Double
    var chatRate: Double
    var audio: Double
    var bufferS: Double
    var bufferTarget: Double
    var recording: Bool
    var highlights: Int
    var scores: [Double]
}

struct StreamerItem: Codable, Identifiable, Equatable {
    var id: Int
    var name: String
    var login: String
    var platform: String
    var live: Bool
    var monitoring: Bool
    var title: String
    var category: String
    var viewers: Int
    var simulation: Bool
    var stats: LiveStats?
}

struct ExportItem: Codable, Identifiable, Equatable {
    var id: Int
    var profile: String
    var qcPassed: Bool
    var uploadedTo: [String]
    var available: Bool
}

struct ClipItem: Codable, Identifiable, Equatable {
    var id: Int
    var title: String
    var streamer: String
    var score: Double
    var quality: Double
    var duration: Double
    var created: String
    var status: String
    var hasThumbnail: Bool
    var exports: [ExportItem]

    /// Newest playable export, if any.
    var playable: ExportItem? { exports.first(where: { $0.available }) }
}

struct AccountItem: Codable, Identifiable, Equatable {
    var id: Int
    var platform: String
    var name: String
    var privacy: String
    var autoUpload: Bool
}

struct JobItem: Codable, Identifiable, Equatable {
    var id: Int
    var kind: String
    var name: String
    var status: String
    var progress: Double
    var stage: String
    var error: String
    var clip: String
}

struct DesktopInfo: Codable {
    var app: String
    var version: String
    var pairing: Bool
}

/// Content of the pairing QR code shown by the desktop app:
/// `dogsapka://pair?hosts=192.168.1.12,10.0.0.5&port=47820&code=123456`
struct PairingInfo: Equatable {
    var hosts: [String]
    var port: Int
    var code: String

    init(hosts: [String], port: Int, code: String) {
        self.hosts = hosts
        self.port = port
        self.code = code
    }

    init?(url string: String) {
        guard let comps = URLComponents(string: string.trimmingCharacters(in: .whitespacesAndNewlines)),
              comps.scheme == "dogsapka", comps.host == "pair" else { return nil }
        var q: [String: String] = [:]
        for item in comps.queryItems ?? [] { q[item.name] = item.value ?? "" }
        let hosts = (q["hosts"] ?? "").split(separator: ",").map { String($0) }.filter { !$0.isEmpty }
        guard !hosts.isEmpty, let port = Int(q["port"] ?? ""), (1...65535).contains(port),
              let code = q["code"], code.count == 6, code.allSatisfy({ $0.isNumber }) else { return nil }
        self.init(hosts: hosts, port: port, code: code)
    }

    /// Manual entry: "192.168.1.12:47820" (port optional) + code.
    init?(address: String, code: String) {
        let parts = address.trimmingCharacters(in: .whitespaces).split(separator: ":")
        guard let host = parts.first, !host.isEmpty else { return nil }
        let port = parts.count > 1 ? Int(parts[1]) ?? 47820 : 47820
        let c = code.filter { $0.isNumber }
        guard c.count == 6 else { return nil }
        self.init(hosts: [String(host)], port: port, code: c)
    }
}

/// What the phone remembers after pairing (stored in the Keychain).
struct Connection: Codable, Equatable {
    var host: String
    var port: Int
    var token: String
    var desktopVersion: String

    var baseURL: URL { URL(string: "http://\(host):\(port)")! }
}
