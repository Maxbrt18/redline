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
    static var injury: Self { Self(path: ["injury"]) }
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

/// The notebook API, as one connected device.
public struct NotebookClient: Sendable {
    public let server: Server
    private let device: String
    private let transport: any HTTPTransport

    public init(server: Server, device: String, transport: any HTTPTransport = URLSessionTransport()) {
        self.server = server
        self.device = device
        self.transport = transport
    }

    /// Trades a link for a device token, which the caller keeps in the keychain.
    public static func connect(
        _ link: NotebookLink, server: Server, transport: any HTTPTransport = URLSessionTransport()
    ) async throws(NotebookError) -> String {
        let request = post(["devices"], body: ["token": link.token], server: server, device: nil)
        let grant = try await send(request, as: Notebook.DeviceGrant.self, transport: transport)
        return grant.device
    }

    public func get<Value>(_ endpoint: Endpoint<Value>) async throws(NotebookError) -> Value {
        var request = URLRequest(url: Self.url(endpoint.path, server: server))
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(device)", forHTTPHeaderField: "Authorization")
        return try await Self.send(request, as: Value.self, transport: transport)
    }

    /// Revokes this device's token on the server.
    public func signOut() async throws(NotebookError) {
        let request = Self.post(["logout"], body: [:], server: server, device: device)
        _ = try await Self.send(request, as: JSONValue.self, transport: transport)
    }

    /// Each segment is escaped on its own: a `/` inside one never adds a level.
    private static func url(_ path: [String], server: Server) -> URL {
        var segment = CharacterSet.urlPathAllowed
        segment.remove("/")
        var components = URLComponents(url: server.baseURL, resolvingAgainstBaseURL: false)!
        components.percentEncodedPath = (["notebook-api"] + path)
            .map { "/" + ($0.addingPercentEncoding(withAllowedCharacters: segment) ?? "") }
            .joined()
        return components.url!
    }

    private static func post(_ path: [String], body: [String: String], server: Server, device: String?) -> URLRequest {
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
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw .unreadable
        }
    }
}

/// The body of every refusal: `{"error": "..."}`.
private struct ServerMessage: Decodable {
    let error: String
}
