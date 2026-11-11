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
}

/// Answers each route with the fixture of the same name; `week/<label>` and
/// `session/<id>` share one fixture each, every card is `entry`, and every card's image
/// is `card.png`, a sample card of the engine.
struct DemoTransport: HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        let parts = Array(request.url!.pathComponents.dropFirst(2))
        let name = parts.first == "entries" && parts.count > 1 ? (parts.count > 2 ? "card" : "entry") : parts.first ?? ""
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
