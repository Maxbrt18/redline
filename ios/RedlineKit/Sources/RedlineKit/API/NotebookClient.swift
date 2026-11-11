import Foundation

/// What carries a request to the server; tests replace it.
public protocol HTTPTransport: Sendable {
    func data(for request: URLRequest) async throws -> (Data, URLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try await session.data(for: request)
    }
}

/// Why a request to the notebook API failed.
public enum NotebookError: Error, Equatable {
    /// The device token is missing, revoked or expired: the device must connect again.
    /// The message is the server's, in the athlete's language.
    case signedOut(String)
    /// The server refused the request and said why, in the athlete's language.
    case refused(status: Int, message: String)
    /// No answer: no network, a timeout, the server down.
    case unreachable
    /// An answer that is not what the contract declares.
    case unreadable
}

/// A read route of the notebook API and the view it serves.
public struct Endpoint<Value: Decodable & Sendable>: Sendable {
    let path: [String]
    var query: [URLQueryItem] = []
    /// Whether the device keeps the answer for the next opening; an old forecast would
    /// mislead, so a forecast is never kept.
    var kept = true

    /// Where the cache keeps this route's last answer; it names the request, query included.
    public var cacheKey: String {
        ([path.joined(separator: "/")] + query.map { "\($0.name)=\($0.value ?? "")" }).joined(separator: "?")
    }
}

public extension Endpoint where Value == Notebook.TodayView {
    static var today: Self { Self(path: ["today"]) }
}

public extension Endpoint where Value == Notebook.WeekView {
    static func week(_ label: String) -> Self { Self(path: ["week", label]) }
}

public extension Endpoint where Value == Notebook.WeeksView {
    static var weeks: Self { Self(path: ["weeks"]) }
}

public extension Endpoint where Value == Notebook.InjuryView {
    /// A watched zone's pain over the last weeks; without a zone, the first one.
    static func injury(zone: String? = nil) -> Self {
        Self(path: ["injury"], query: zone.map { [URLQueryItem(name: "zone", value: $0)] } ?? [])
    }
}

public extension Endpoint where Value == Notebook.SessionView {
    static func session(_ observation: String) -> Self { Self(path: ["session", observation]) }
}

public extension Endpoint where Value == Notebook.RaceView {
    static var race: Self { Self(path: ["race"]) }
}

public extension Endpoint where Value == Notebook.RacePlanView {
    static var racePlan: Self { Self(path: ["race-plan"]) }
}

public extension Endpoint where Value == Notebook.HabitsView {
    static var habits: Self { Self(path: ["habits"]) }
}

public extension Endpoint where Value == Notebook.EntriesView {
    /// A page of the archived cards, latest first; `offset` comes from the previous page.
    static func entries(offset: Int = 0) -> Self {
        Self(path: ["entries"], query: offset > 0 ? [URLQueryItem(name: "offset", value: "\(offset)")] : [])
    }
}

public extension Endpoint where Value == Notebook.EntryView {
    static func entry(_ id: String) -> Self { Self(path: ["entries", id]) }
}

public extension Endpoint where Value == Notebook.Forecast? {
    /// The forecast of a session to come: at its place, else the notebook's; around its
    /// start, else the moments of the day. Nil when there is none to give.
    static func weather(date: String, time: String? = nil, place: String? = nil) -> Self {
        let query = [URLQueryItem(name: "date", value: date)]
            + (time.map { [URLQueryItem(name: "time", value: $0)] } ?? [])
            + (place.map { [URLQueryItem(name: "place", value: $0)] } ?? [])
        return Self(path: ["weather"], query: query, kept: false)
    }
}

/// The notebook API, as one connected device.
public struct NotebookClient: Sendable {
    public let server: Server
    private let device: String
    private let transport: any HTTPTransport
    private let cache: (any ResponseCache)?

    public init(server: Server, device: String, transport: any HTTPTransport = URLSessionTransport(),
                cache: (any ResponseCache)? = nil) {
        self.server = server
        self.device = device
        self.transport = transport
        self.cache = cache
    }

    /// Trades a link for a device token, which the caller keeps in the keychain.
    public static func connect(
        _ link: NotebookLink, server: Server, transport: any HTTPTransport = URLSessionTransport()
    ) async throws(NotebookError) -> String {
        let request = post(["devices"], body: ["token": link.token], server: server, device: nil)
        let grant = try await send(request, as: Notebook.DeviceGrant.self, transport: transport)
        return grant.device
    }

    /// The route's view from the server; the answer replaces the cached one.
    public func get<Value>(_ endpoint: Endpoint<Value>) async throws(NotebookError) -> Value {
        var request = URLRequest(url: Self.url(endpoint.path, query: endpoint.query, server: server))
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(device)", forHTTPHeaderField: "Authorization")
        let data = try await Self.exchange(request, transport: transport)
        let value = try Self.decode(Value.self, from: data)
        if endpoint.kept {
            cache?.write(data, for: endpoint.cacheKey)
        }
        return value
    }

    /// The route's last answer kept on the device, and when it came; nil if there is none
    /// or if this build no longer reads it.
    public func cached<Value>(_ endpoint: Endpoint<Value>) -> (value: Value, savedAt: Date)? {
        guard endpoint.kept, let kept = cache?.read(endpoint.cacheKey),
              let value = try? JSONDecoder().decode(Value.self, from: kept.data)
        else { return nil }
        return (value, kept.savedAt)
    }

    /// A card's image. An image never changes under its hash, so a kept one is shown
    /// without a request, offline included.
    public func cardImage(_ entry: Notebook.EntryView) async throws(NotebookError) -> Data {
        let key = "entries/\(entry.id)/image@\(entry.imageHash)"
        if let kept = cache?.read(key) { return kept.data }
        var request = URLRequest(url: Self.url(["entries", entry.id, "image"], server: server))
        request.setValue("image/*", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(device)", forHTTPHeaderField: "Authorization")
        let data = try await Self.exchange(request, transport: transport)
        cache?.write(data, for: key)
        return data
    }

    /// Forgets every cached answer.
    public func clearCache() {
        cache?.clear()
    }

    /// Answers one question of the notebook: `ask` names it (and its field), `value` is
    /// what the page would send. True when the answer went on to the coach.
    public func answer(_ ask: String, value: JSONValue) async throws(NotebookError) -> Bool {
        let request = Self.post(["answer"], body: AnswerBody(ask: ask, value: value), server: server, device: device)
        return try await Self.send(request, as: Notebook.Answered.self, transport: transport).alert
    }

    /// Revokes this device's token on the server.
    public func signOut() async throws(NotebookError) {
        let request = Self.post(["logout"], body: [String: String](), server: server, device: device)
        _ = try await Self.send(request, as: JSONValue.self, transport: transport)
    }

    /// Each segment is escaped on its own: a `/` inside one never adds a level.
    private static func url(_ path: [String], query: [URLQueryItem] = [], server: Server) -> URL {
        var segment = CharacterSet.urlPathAllowed
        segment.remove("/")
        var components = URLComponents(url: server.baseURL, resolvingAgainstBaseURL: false)!
        components.percentEncodedPath = (["notebook-api"] + path)
            .map { "/" + ($0.addingPercentEncoding(withAllowedCharacters: segment) ?? "") }
            .joined()
        components.queryItems = query.isEmpty ? nil : query
        return components.url!
    }

    private static func post(_ path: [String], body: some Encodable, server: Server, device: String?) -> URLRequest {
        var request = URLRequest(url: url(path, server: server))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let device {
            request.setValue("Bearer \(device)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try? JSONEncoder().encode(body)
        return request
    }

    private static func send<Value: Decodable>(
        _ request: URLRequest, as type: Value.Type, transport: any HTTPTransport
    ) async throws(NotebookError) -> Value {
        try decode(type, from: try await exchange(request, transport: transport))
    }

    private static func exchange(_ request: URLRequest, transport: any HTTPTransport) async throws(NotebookError) -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await transport.data(for: request)
        } catch {
            throw .unreachable
        }
        guard let status = (response as? HTTPURLResponse)?.statusCode else { throw .unreadable }
        guard (200..<300).contains(status) else {
            let message = (try? JSONDecoder().decode(ServerMessage.self, from: data))?.error ?? ""
            throw status == 401 ? .signedOut(message) : .refused(status: status, message: message)
        }
        return data
    }

    private static func decode<Value: Decodable>(_ type: Value.Type, from data: Data) throws(NotebookError) -> Value {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw .unreadable
        }
    }
}

private struct AnswerBody: Encodable {
    let ask: String
    let value: JSONValue
}

/// The body of every refusal: `{"error": "..."}`.
private struct ServerMessage: Decodable {
    let error: String
}
