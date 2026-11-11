import Foundation
import Observation
import RedlineKit

/// One screen's view: the last answer kept on the device at once, then the server's.
@Observable
final class ScreenModel<Value: Decodable & Sendable> {
    private(set) var value: Value?
    /// When the shown value came from the server.
    private(set) var savedAt: Date?
    /// The last refresh failed and the value shown is the kept one.
    private(set) var stale = false
    /// Why there is nothing to show, in the athlete's words.
    private(set) var failure: String?
    private(set) var loading = false
    let endpoint: Endpoint<Value>

    init(_ endpoint: Endpoint<Value>) {
        self.endpoint = endpoint
    }

    func load(with session: NotebookSession) async {
        guard case .signedIn(let client) = session.state else { return }
        if value == nil, let kept = client.cached(endpoint) {
            value = kept.value
            savedAt = kept.savedAt
            stale = true
        }
        loading = true
        defer { loading = false }
        do {
            value = try await client.get(endpoint)
            savedAt = .now
            stale = false
            failure = nil
        } catch .signedOut(let message) {
            session.forget(saying: message.isEmpty ? nil : message)
        } catch {
            stale = value != nil
            failure = value == nil ? NotebookSession.words(for: error) : nil
        }
    }
}
