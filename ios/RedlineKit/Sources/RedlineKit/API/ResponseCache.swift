import Foundation

/// The last answer of each route, kept so that a screen opens at once and still shows
/// something without a network.
public protocol ResponseCache: Sendable {
    func read(_ key: String) -> CachedResponse?
    func write(_ data: Data, for key: String)
    /// Forgets every answer: a device that signs out keeps nothing of the notebook.
    func clear()
}

public struct CachedResponse: Sendable, Equatable {
    public let data: Data
    public let savedAt: Date
}

/// One file per route in a private folder, protected like the rest of the app's data.
public struct FileResponseCache: ResponseCache {
    private let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    /// The app's own cache folder, one per server.
    public init(server: Server) {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        self.init(directory: caches.appending(path: "notebook").appending(path: Self.fileName(server.baseURL.absoluteString)))
    }

    public func read(_ key: String) -> CachedResponse? {
        let url = file(key)
        guard let data = try? Data(contentsOf: url),
              let saved = try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
        else { return nil }
        return CachedResponse(data: data, savedAt: saved)
    }

    public func write(_ data: Data, for key: String) {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? data.write(to: file(key), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    public func clear() {
        try? FileManager.default.removeItem(at: directory)
    }

    private func file(_ key: String) -> URL {
        directory.appending(path: Self.fileName(key) + ".json")
    }

    /// A key as a file name: anything but letters, digits and hyphens becomes `_`.
    static func fileName(_ key: String) -> String {
        String(key.unicodeScalars.map { scalar in
            CharacterSet.alphanumerics.contains(scalar) && scalar.isASCII || scalar == "-" ? Character(scalar) : "_"
        })
    }
}
