import Foundation
import UIKit

enum APIError: LocalizedError {
    case unauthorized
    case server(String)
    case unreachable(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "A számítógép letiltotta ezt a telefont – párosítsd újra."
        case .server(let msg): return msg
        case .unreachable(let msg): return "Nem érem el a számítógépet (\(msg)). Ugyanazon a Wi-Fi-n vagy, és fut a DOGSAPKA?"
        }
    }
}

/// Talks to the DOGSAPKA desktop app's remote-control API.
final class APIClient {
    let connection: Connection
    private let session: URLSession
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    init(connection: Connection) {
        self.connection = connection
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 8
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg)
    }

    var authHeaders: [String: String] { ["Authorization": "Bearer \(connection.token)"] }

    func url(_ path: String) -> URL { connection.baseURL.appendingPathComponent(path) }

    // MARK: pairing

    /// Tries every address from the QR code and returns the first that accepts the code.
    static func pair(_ info: PairingInfo) async throws -> Connection {
        var lastError: Error = APIError.unreachable("nincs cím")
        let device = await UIDevice.current.name
        for host in info.hosts {
            guard let url = URL(string: "http://\(host):\(info.port)/api/pair") else { continue }
            var req = URLRequest(url: url, timeoutInterval: 6)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: ["code": info.code, "device": device])
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                if code == 403 { throw APIError.server("Hibás vagy lejárt párosító kód. Kérj újat a számítógépen.") }
                guard code == 200,
                      let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let token = obj["token"] as? String else { throw APIError.server("Váratlan válasz (\(code)).") }
                return Connection(host: host, port: info.port, token: token,
                                  desktopVersion: obj["version"] as? String ?? "")
            } catch let e as APIError {
                if case .server = e { throw e }          // reached the desktop, but it said no
                lastError = e
            } catch {
                lastError = APIError.unreachable(error.localizedDescription)
            }
        }
        throw lastError
    }

    // MARK: requests

    private func send(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Data {
        var req = URLRequest(url: url(path))
        req.httpMethod = method
        authHeaders.forEach { req.setValue($1, forHTTPHeaderField: $0) }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: req)
        } catch {
            throw APIError.unreachable(error.localizedDescription)
        }
        let (data, resp) = result
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 { throw APIError.unauthorized }
        guard (200..<300).contains(code) else {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw APIError.server(msg ?? "Hiba (\(code))")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String, as: T.Type) async throws -> T {
        try decoder.decode(T.self, from: try await send(path))
    }

    func status() async throws -> DesktopStatus { try await get("api/status", as: DesktopStatus.self) }
    func streamers() async throws -> [StreamerItem] { try await get("api/streamers", as: [StreamerItem].self) }
    func clips() async throws -> [ClipItem] { try await get("api/clips", as: [ClipItem].self) }
    func accounts() async throws -> [AccountItem] { try await get("api/accounts", as: [AccountItem].self) }
    func jobs() async throws -> [JobItem] { try await get("api/jobs", as: [JobItem].self) }

    func setMonitoring(_ id: Int, _ enabled: Bool) async throws {
        _ = try await send("api/streamers/\(id)/monitoring", method: "POST", body: ["enabled": enabled])
    }

    func setMonitoringAll(_ enabled: Bool) async throws {
        _ = try await send("api/monitoring/all", method: "POST", body: ["enabled": enabled])
    }

    func upload(export: Int, accounts: [Int]) async throws {
        _ = try await send("api/exports/\(export)/upload", method: "POST",
                           body: ["account_ids": accounts, "confirm_rights": true])
    }

    func job(_ id: Int, _ action: String) async throws {
        _ = try await send("api/jobs/\(id)/\(action)", method: "POST", body: [:])
    }

    func pauseQueue(_ paused: Bool) async throws {
        _ = try await send("api/queue/pause_all", method: "POST", body: ["paused": paused])
    }

    func thumbnail(clip: Int) async throws -> Data { try await send("api/clips/\(clip)/thumbnail") }

    func videoURL(export: Int) -> URL { url("api/exports/\(export)/video") }
}
