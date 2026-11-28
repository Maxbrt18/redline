#if DEBUG
import Foundation
import RedlineKit

/// The notebook of the tests' fictional athlete, served from the bundled contract fixtures:
/// `-RedlineDemo YES` at launch opens every screen without a server. Debug builds only.
enum DemoNotebook {
    static func session() -> NotebookSession {
        let server = try! Server(host: "demo.invalid")
        let strength = FileManager.default.temporaryDirectory.appending(path: "demo-strength")
        return NotebookSession(server: server, store: DemoTokenStore(), transport: DemoTransport(),
                               strengthStore: FileStrengthProgressStore(directory: strength))
    }

    /// The week the reorganisation fixture is about.
    static var reorganizedWeek: String {
        let url = Bundle.main.url(forResource: "reorganize", withExtension: "json")
        let view = url.flatMap { try? JSONDecoder().decode(Notebook.ReorganizeView.self, from: Data(contentsOf: $0)) }
        return view?.week ?? Day(.now).isoWeek
    }
}

/// Answers each route with the fixture of the same name; `week/<label>` and
/// `session/<id>` share one fixture each, every card is `entry`, and every card's image
/// is `card.png`, a sample card of the engine. A write is accepted and changes nothing.
/// Every strength link opens the `strength-session` fixture, and its report is accepted.
struct DemoTransport: HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        switch request.url!.path() {
        case "/strength-session": return try await answer(request, name: "strength-session")
        case "/strength-report":
            return (Data(#"{"status": "accepted"}"#.utf8),
                    HTTPURLResponse(url: request.url!, statusCode: 201, httpVersion: nil, headerFields: nil)!)
        default: break
        }
        let parts = Array(request.url!.pathComponents.dropFirst(2))
        let path = parts.joined(separator: "/")
        let name = if request.httpMethod == "POST" {
            // A write answers as the service does: a revision, the variants, or done.
            switch path {
            case "reorganize/take", "proposals/take": "revised"
            case "reorganize", "answer", "launch": path
            default: "done"
            }
        } else if path == "plan/proposal" {
            "plan-proposal"
        } else if parts.first == "entries" && parts.count > 1 {
            parts.count > 2 ? "card" : "entry"
        } else {
            parts.first ?? ""
        }
        return try await answer(request, name: name)
    }

    private func answer(_ request: URLRequest, name: String) async throws -> (Data, URLResponse) {
        let fixture = Bundle.main.url(forResource: name, withExtension: name == "card" ? "png" : "json")
        try await Task.sleep(for: .milliseconds(300))
        var body = fixture.flatMap { try? Data(contentsOf: $0) } ?? Data(#"{"error": "Pas de démo pour cette page."}"#.utf8)
        if name == "today" { body = DemoDay.variant(of: body) }
        if name == "plan" { body = DemoPlan.variant(of: body) }
        let response = HTTPURLResponse(url: request.url!, statusCode: fixture == nil ? 404 : 200,
                                       httpVersion: nil, headerFields: nil)!
        return (body, response)
    }
}

/// `-RedlineDay done` or `-RedlineDay rest` with the demo turns today's fixture into that
/// moment of the day: every session done, the run's debrief left to answer; or nothing
/// planned and nothing asked.
nonisolated private enum DemoDay {
    static let run = (observation: "demo-run", km: 8.35)

    static func variant(of data: Data) -> Data {
        guard let moment = UserDefaults.standard.string(forKey: "RedlineDay"),
              var today = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let sessions = today["sessions"] as? [[String: Any]] else { return data }
        let date = today["date"] as? String ?? ""
        switch moment {
        case "done":
            today["sessions"] = sessions.map { session in
                var session = session
                session["status"] = "done"
                session["fact"] = session["kind"] as? String == "strength" ? strengthFact : runFact(date: date)
                return session
            }
            today["asks"] = [debrief(date: date)]
            today["strip"] = (today["strip"] as? [[String: Any]])?.map { day in
                var day = day
                if day["date"] as? String == date { day["done_km"] = run.km }
                return day
            }
            if var totals = today["totals"] as? [String: Any] {
                totals["km_done"] = (totals["km_done"] as? Double ?? 0) + run.km
                today["totals"] = totals
            }
        case "rest":
            today["sessions"] = [[String: Any]]()
            today["asks"] = [[String: Any]]()
        default:
            return data
        }
        return (try? JSONSerialization.data(withJSONObject: today)) ?? data
    }

    private static var strengthFact: [String: Any] { ["duration_min": 26, "pains": [Any](), "over": [Any]()] }

    private static func runFact(date: String) -> [String: Any] {
        ["observation_id": run.observation, "kind": "easy", "date": date, "km": run.km, "duration_min": 48,
         "hr_avg": 162, "rpe": 3, "pains": [Any](), "enjoyment": NSNull(), "dplus": 20,
         "started_at": "\(date)T17:34:00", "over": ["hr_avg"]]
    }

    private static func debrief(date: String) -> [String: Any] {
        ["id": "cell:\(run.observation)", "type": "cells", "date": date, "kind": "easy", "km": run.km,
         "fields": ["enjoyment"], "labels": [String: String](), "total": 3, "duration_min": 48,
         "pain_values": [0, 0.5, 1, 1.5, 2, 2.5, 3]]
    }
}

private struct DemoTokenStore: DeviceTokenStore {
    func read() -> String? { "demo" }
    func save(_ token: String) throws {}
    func delete() {}
}
#endif

/// `-RedlinePlan proposal` with the demo opens the Plan screen with the proposal fixture
/// waiting to be taken.
nonisolated enum DemoPlan {
    static func variant(of data: Data) -> Data {
        guard UserDefaults.standard.string(forKey: "RedlinePlan") == "proposal",
              var plan = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let url = Bundle.main.url(forResource: "plan-proposal", withExtension: "json"),
              let proposal = (try? JSONSerialization.jsonObject(with: Data(contentsOf: url))) as? [String: Any],
              let weeks = proposal["weeks"] as? [[String: Any]] else { return data }
        plan["proposal"] = ["id": proposal["id"] ?? "", "race": proposal["race"] ?? NSNull(),
                            "weeks": weeks.count, "starts_on": weeks.first?["monday"] ?? "",
                            "peak_km": proposal["peak_km"] ?? 0, "expires_on": proposal["expires_on"] ?? ""]
        return (try? JSONSerialization.data(withJSONObject: plan)) ?? data
    }
}
