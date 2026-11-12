import Foundation

/// A notebook link that `/carnet` sends in Messenger, pasted in the app or opened by it:
/// `https://<host>/carnet#t=<token>` or `redline://connect#t=<token>`, either followed by
/// `&entry=<card id>` when the link points at a card. The token stays in the fragment, which
/// no request ever carries to a server.
public struct NotebookLink: Sendable, Equatable {
    /// The one-use link token that buys a device token.
    public let token: String
    /// The card the link points at, if any.
    public let entry: String?

    public static let scheme = "redline"

    public init(_ text: String, server: Server) throws(NotebookLinkError) {
        guard let url = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw .notALink
        }
        let fromServer = url.scheme == "https" && url.host == server.baseURL.host()
            && url.port == server.baseURL.port && url.path == "/carnet"
        let fromApp = url.scheme == Self.scheme && url.host == "connect" && url.path.isEmpty
        guard fromServer || fromApp else {
            throw url.scheme == "https" && url.path == "/carnet" ? .otherServer : .notALink
        }
        var fragment = URLComponents()
        fragment.percentEncodedQuery = url.percentEncodedFragment
        let items = fragment.queryItems ?? []
        guard let token = items.first(where: { $0.name == "t" })?.value else { throw .noToken }
        guard token.wholeMatch(of: /[A-Za-z0-9_-]{43}/) != nil else { throw .noToken }
        let entry = items.first(where: { $0.name == "entry" })?.value
        if let entry, entry.wholeMatch(of: /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/) == nil {
            throw .noToken
        }
        self.token = token
        self.entry = entry
    }
}

public enum NotebookLinkError: Error, Equatable {
    /// Not a notebook link at all.
    case notALink
    /// A notebook link of another instance's server.
    case otherServer
    /// A notebook link whose token or card id is missing or malformed.
    case noToken
}
