#if DEBUG
import Foundation
import RedlineKit

/// The notebook of the tests' fictional athlete, served from the bundled contract fixtures:
/// `-RedlineDemo YES` at launch opens every screen without a server. Debug builds only.
enum DemoNotebook {
    static func session() -> NotebookSession {
        let server = try! Server(host: "demo.invalid")
        return NotebookSession(server: server, store: DemoTokenStore(), transport: DemoTransport())
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
struct DemoTransport: HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let parts = Array(request.url!.pathComponents.dropFirst(2))
        let path = parts.joined(separator: "/")
        let name = if request.httpMethod == "POST" {
            // A write answers as the service does: a revision, the variants, or done.
            switch path {
            case "reorganize/take", "proposals/take": "revised"
            case "reorganize", "answer": path
            default: "done"
            }
        } else if parts.first == "entries" && parts.count > 1 {
            parts.count > 2 ? "card" : "entry"
        } else {
            parts.first ?? ""
        }
        let fixture = Bundle.main.url(forResource: name, withExtension: name == "card" ? "png" : "json")
        try await Task.sleep(for: .milliseconds(300))
        let body = fixture.flatMap { try? Data(contentsOf: $0) } ?? Data(#"{"error": "Pas de démo pour cette page."}"#.utf8)
        let response = HTTPURLResponse(url: request.url!, statusCode: fixture == nil ? 404 : 200,
                                       httpVersion: nil, headerFields: nil)!
        return (body, response)
    }
}

private struct DemoTokenStore: DeviceTokenStore {
    func read() -> String? { "demo" }
    func save(_ token: String) throws {}
    func delete() {}
}
#endif
