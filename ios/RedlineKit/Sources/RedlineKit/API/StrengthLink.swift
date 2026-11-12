import Foundation

/// The capability of one strength session: the token of a `/s#t=<token>` link. It opens
/// that session and posts its report, and nothing else; it never goes in a URL sent to
/// the server.
public struct StrengthToken: Hashable, Sendable, Codable {
    public let value: String

    /// Nil for anything but 32 characters of the URL-safe base64 alphabet.
    public init?(_ value: String) {
        guard value.count == 32, value.unicodeScalars.allSatisfy(Self.alphabet.contains) else { return nil }
        self.value = value
    }

    /// The token of a session link: `https://<host>/s#t=<token>` as the coach sends it, or
    /// `redline://s#t=<token>` as the page hands it to the app; nil for any other link.
    public init?(link: URL) {
        let fromServer = link.scheme == "https" && link.path() == "/s"
        let fromPage = link.scheme == NotebookLink.scheme && link.host() == "s" && link.path().isEmpty
        guard fromServer || fromPage, let fragment = link.fragment(percentEncoded: false), fragment.hasPrefix("t=")
        else { return nil }
        self.init(String(fragment.dropFirst(2)))
    }

    private static let alphabet = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
}

/// Why a strength link did not open.
public enum StrengthLinkError: Error, Equatable {
    /// Past its seven days, or revoked: the coach sends a new one.
    case expired
    /// Unknown to the server.
    case forbidden
    /// No answer, or the server busy: the session opens from what the device kept, if anything.
    case unreachable
    case unreadable
}

/// What the server did with a report.
public enum StrengthReportOutcome: Equatable, Sendable {
    case accepted
    /// It already had a report for this link; nothing new is recorded.
    case duplicate
    case expired
    /// The daily limit of reports is reached.
    case quota
    case forbidden
    /// The report breaks the contract; the server's reason, for the logs.
    case invalid(String)
    /// No answer, or a temporary failure: the report goes out again later.
    case unreachable

    /// Whether the report waits to be sent again.
    public var retries: Bool { self == .unreachable }
}

public extension NotebookClient {
    /// Today's strength session as a link, if the session shown (`signature`) is still the
    /// one of the day; the server refuses with 409 once it has changed.
    func launchStrength(signature: String) async throws(NotebookError) -> StrengthToken {
        let launched: Notebook.Launched = try await write(["launch"], ["signature": signature])
        guard let url = URL(string: launched.url), let token = StrengthToken(link: url) else { throw .unreadable }
        return token
    }

    /// The session a link opens, with the zones to ask about and the movement cues.
    func strengthSession(_ token: StrengthToken) async throws(StrengthLinkError) -> Notebook.StrengthSessionView {
        let (data, status) = try await Self.capability(["strength-session"], body: ["token": token.value],
                                                       server: server, transport: transport)
        switch status {
        case 200:
            guard let view = try? JSONDecoder().decode(Notebook.StrengthSessionView.self, from: data) else {
                throw .unreadable
            }
            return view
        case 410: throw .expired
        case 429, 500...: throw .unreachable
        default: throw .forbidden
        }
    }

    /// Posts a session's report with its link.
    func sendStrengthReport(_ report: Notebook.StrengthReportPosted, token: StrengthToken) async -> StrengthReportOutcome {
        let body = ReportBody(token: token.value, report: report)
        guard let (data, status) = try? await Self.capability(["strength-report"], body: body, server: server,
                                                               transport: transport)
        else { return .unreachable }
        let answer = try? JSONDecoder().decode(ReportAnswer.self, from: data)
        switch status {
        case 200..<300: return answer?.status == "duplicate" ? .duplicate : .accepted
        case 410: return .expired
        case 429: return .quota
        case 403: return .forbidden
        case 400: return .invalid(answer?.error ?? "")
        default: return .unreachable
        }
    }

    /// A route of the strength session, outside `/notebook-api/`: the token in the body
    /// is its only credential.
    private static func capability(_ path: [String], body: some Encodable, server: Server,
                                   transport: any HTTPTransport) async throws(StrengthLinkError) -> (Data, Int) {
        var components = URLComponents(url: server.baseURL, resolvingAgainstBaseURL: false)!
        components.path = "/" + path.joined(separator: "/")
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONEncoder().encode(body)
        do {
            let (data, response) = try await transport.data(for: request)
            guard let status = (response as? HTTPURLResponse)?.statusCode else { throw StrengthLinkError.unreadable }
            return (data, status)
        } catch {
            throw .unreachable
        }
    }
}

private struct ReportBody: Encodable {
    let token: String
    let report: Notebook.StrengthReportPosted
}

private struct ReportAnswer: Decodable {
    let status: String?
    let error: String?
}
