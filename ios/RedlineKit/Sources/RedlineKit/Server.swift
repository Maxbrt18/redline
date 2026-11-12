import Foundation

/// The coach server of one instance.
///
/// The host is an instance value: it comes from the app's build settings
/// (`REDLINE_SERVER_HOST` in `Config/Local.xcconfig`), never from the code.
public struct Server: Sendable, Equatable {
    /// The Info.plist key that the build fills from `REDLINE_SERVER_HOST`.
    public static let hostKey = "RedlineServerHost"

    public let baseURL: URL

    /// A server reached over HTTPS at `host`, which may carry a port and nothing else.
    public init(host: String) throws(ServerError) {
        let trimmed = host.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { throw .missingHost }
        guard let components = URLComponents(string: "https://\(trimmed)"),
              let name = components.host, !name.isEmpty,
              components.path.isEmpty, components.query == nil, components.fragment == nil,
              components.user == nil, components.password == nil,
              let url = components.url
        else { throw .invalidHost(trimmed) }
        baseURL = url
    }

    /// The server the app was built for.
    public init(bundle: Bundle) throws(ServerError) {
        let host = bundle.object(forInfoDictionaryKey: Self.hostKey) as? String
        try self.init(host: host ?? "")
    }
}

public enum ServerError: Error, Equatable {
    /// The build left `REDLINE_SERVER_HOST` empty.
    case missingHost
    /// The value is a URL, a path or anything else than a host.
    case invalidHost(String)
}
