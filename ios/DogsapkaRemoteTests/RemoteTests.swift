import XCTest
@testable import DogsapkaRemote

final class PairingTests: XCTestCase {
    func testParsesDesktopQRCode() {
        // exactly what dogsapka_clipper/ui/remote_panel.py::pairing_url produces
        let info = PairingInfo(url: "dogsapka://pair?hosts=192.168.1.12%2C10.0.0.5&port=47820&code=494111")
        XCTAssertEqual(info, PairingInfo(hosts: ["192.168.1.12", "10.0.0.5"], port: 47820, code: "494111"))
    }

    func testRejectsForeignOrBrokenCodes() {
        XCTAssertNil(PairingInfo(url: "https://example.com/pair?hosts=1.2.3.4&port=1&code=123456"))
        XCTAssertNil(PairingInfo(url: "dogsapka://pair?hosts=1.2.3.4&port=47820&code=12345"))
        XCTAssertNil(PairingInfo(url: "dogsapka://pair?port=47820&code=123456"))
        XCTAssertNil(PairingInfo(url: "dogsapka://pair?hosts=1.2.3.4&port=99999&code=123456"))
    }

    func testManualEntry() {
        XCTAssertEqual(PairingInfo(address: "192.168.1.12:47821", code: "494 111"),
                       PairingInfo(hosts: ["192.168.1.12"], port: 47821, code: "494111"))
        XCTAssertEqual(PairingInfo(address: "192.168.1.12", code: "494111")?.port, 47820)
        XCTAssertNil(PairingInfo(address: "192.168.1.12", code: "12"))
    }
}

final class DecodingTests: XCTestCase {
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    // JSON shapes copied from the desktop API (services/remote.py)
    func testStatus() throws {
        let json = #"{"version": "0.3.0", "live_sessions": 2, "recording": 1, "monitored": 3, "streamers": 4,"#
            + #" "queue_pending": 5, "queue_paused": false, "disk_free_gb": 209.4}"#
        let s = try decoder.decode(DesktopStatus.self, from: Data(json.utf8))
        XCTAssertEqual(s.liveSessions, 2)
        XCTAssertEqual(s.diskFreeGb, 209.4, accuracy: 0.01)
    }

    func testStreamersWithAndWithoutStats() throws {
        let json = """
        [{"id": 1, "name": "Anna", "login": "anna", "platform": "twitch", "live": true, "monitoring": true,
          "title": "GTA", "category": "Grand Theft Auto V", "viewers": 120, "simulation": false,
          "stats": {"score": 72.5, "threshold": 60.0, "chat_rate": 4.2, "audio": 0.31, "buffer_s": 118,
                    "buffer_target": 120, "recording": true, "highlights": 2, "scores": [10.0, 55.5, 72.5]}},
         {"id": 2, "name": "Bela", "login": "yt-bela", "platform": "youtube", "live": false, "monitoring": false,
          "title": "", "category": "", "viewers": 0, "simulation": false, "stats": null}]
        """
        let list = try decoder.decode([StreamerItem].self, from: Data(json.utf8))
        XCTAssertEqual(list.count, 2)
        XCTAssertEqual(list[0].stats?.highlights, 2)
        XCTAssertEqual(list[0].stats?.scores.last, 72.5)
        XCTAssertNil(list[1].stats)
    }

    func testClipsAndPlayableExport() throws {
        let json = """
        [{"id": 7, "title": "Nice play", "streamer": "Anna", "score": 91.0, "quality": 80.0, "duration": 21.0,
          "created": "2026-11-18T10:00:00", "status": "done", "has_thumbnail": true,
          "exports": [{"id": 12, "profile": "TikTok Gaming", "qc_passed": true, "uploaded_to": ["Main"],
                       "available": false},
                      {"id": 11, "profile": "TikTok Gaming", "qc_passed": true, "uploaded_to": [],
                       "available": true}]}]
        """
        let clips = try decoder.decode([ClipItem].self, from: Data(json.utf8))
        XCTAssertEqual(clips[0].playable?.id, 11)       // the newest export whose file still exists
    }

    func testJobsAndAccounts() throws {
        let jobs = try decoder.decode([JobItem].self, from: Data("""
        [{"id": 3, "kind": "upload", "name": "Upload", "status": "running", "progress": 0.42,
          "stage": "Uploading to Main", "error": "", "clip": "Nice play"}]
        """.utf8))
        XCTAssertEqual(jobs[0].progress, 0.42, accuracy: 0.001)
        let accounts = try decoder.decode([AccountItem].self, from: Data("""
        [{"id": 1, "platform": "youtube", "name": "Main", "privacy": "public", "auto_upload": true}]
        """.utf8))
        XCTAssertTrue(accounts[0].autoUpload)
    }

    func testConnectionURL() {
        let c = Connection(host: "192.168.1.12", port: 47820, token: "t", desktopVersion: "0.3.0")
        XCTAssertEqual(c.baseURL.absoluteString, "http://192.168.1.12:47820")
        let api = APIClient(connection: c)
        XCTAssertEqual(api.videoURL(export: 5).absoluteString, "http://192.168.1.12:47820/api/exports/5/video")
        XCTAssertEqual(api.authHeaders["Authorization"], "Bearer t")
    }
}
