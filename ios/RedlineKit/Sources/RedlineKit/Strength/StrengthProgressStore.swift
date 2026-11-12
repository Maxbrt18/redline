import Foundation

/// Where a session's progress stays between two openings of the app, by session id, with
/// the link that opened it: a session opened once reopens without a network, and its
/// report leaves when the network comes back.
public protocol StrengthProgressStore: Sendable {
    /// Nil when nothing is kept, or when this build no longer reads what is.
    func load(sid: String) -> StrengthProgress?
    func save(_ progress: StrengthProgress, sid: String) throws
    func remove(sid: String)
    func loadOpened(sid: String) -> OpenedStrength?
    func saveOpened(_ opened: OpenedStrength) throws
    /// Every kept session whose report waits for the network.
    func pendingReports() -> [(opened: OpenedStrength, progress: StrengthProgress)]
}

/// A strength link as it opened: its token and what the server answered.
public struct OpenedStrength: Codable, Hashable, Sendable {
    public let token: StrengthToken
    public let view: Notebook.StrengthSessionView

    public init(token: StrengthToken, view: Notebook.StrengthSessionView) {
        self.token = token
        self.view = view
    }

    public var sid: String { view.session.sid }
}

/// One file per session in the app's private support folder.
public struct FileStrengthProgressStore: StrengthProgressStore {
    private let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    /// The app's own folder, one per server.
    public init(server: Server) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        self.init(directory: support.appending(path: "strength")
            .appending(path: FileResponseCache.fileName(server.baseURL.absoluteString)))
    }

    public func load(sid: String) -> StrengthProgress? {
        guard let data = try? Data(contentsOf: file(sid)) else { return nil }
        return try? JSONDecoder().decode(StrengthProgress.self, from: data)
    }

    public func save(_ progress: StrengthProgress, sid: String) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try JSONEncoder().encode(progress)
            .write(to: file(sid), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    public func remove(sid: String) {
        try? FileManager.default.removeItem(at: file(sid))
        try? FileManager.default.removeItem(at: link(sid))
    }

    public func loadOpened(sid: String) -> OpenedStrength? {
        guard let data = try? Data(contentsOf: link(sid)) else { return nil }
        return try? JSONDecoder().decode(OpenedStrength.self, from: data)
    }

    public func saveOpened(_ opened: OpenedStrength) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try JSONEncoder().encode(opened)
            .write(to: link(opened.sid), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    public func pendingReports() -> [(opened: OpenedStrength, progress: StrengthProgress)] {
        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return files.filter { $0.lastPathComponent.hasSuffix(Self.linkSuffix) }.compactMap { url in
            guard let data = try? Data(contentsOf: url),
                  let opened = try? JSONDecoder().decode(OpenedStrength.self, from: data),
                  let progress = load(sid: opened.sid), case .pending = progress.delivery
            else { return nil }
            return (opened, progress)
        }
    }

    private static let linkSuffix = ".link.json"

    private func file(_ sid: String) -> URL {
        directory.appending(path: FileResponseCache.fileName(sid) + ".json")
    }

    private func link(_ sid: String) -> URL {
        directory.appending(path: FileResponseCache.fileName(sid) + Self.linkSuffix)
    }
}

/// A started session not yet sent, as the notebook offers to resume it ("3 exercices sur 7").
public struct StrengthResume: Hashable, Sendable {
    public let startedAt: Date
    public let done: Int
    public let total: Int

    public init?(session: Notebook.StrengthSession, progress: StrengthProgress?) {
        guard let progress, let startedAt = progress.startedAt else { return nil }
        if case .sent = progress.delivery { return nil }
        let run = StrengthRun(session: session, zones: [], saved: progress)
        self.startedAt = startedAt
        done = run.exercises.indices.filter(run.isDone).count
        total = run.exercises.count
    }
}

/// How long to wait before sending a pending report again: 5 s, doubled on each failure,
/// at most 5 min.
public struct StrengthRetry: Sendable {
    public static let first: Duration = .seconds(5)
    public static let longest: Duration = .seconds(300)

    public private(set) var delay = first

    public init() {}

    /// The wait before the next attempt; the one after is longer.
    public mutating func next() -> Duration {
        let wait = delay
        delay = min(delay * 2, Self.longest)
        return wait
    }

    public mutating func reset() {
        delay = Self.first
    }
}

/// Sends the reports left waiting for the network, from wherever the app is.
public enum StrengthOutbox {
    /// What each kept report became; a report the server still does not answer stays pending.
    public static func flush(client: NotebookClient, store: any StrengthProgressStore,
                             now: Date = .now) async {
        for (opened, progress) in store.pendingReports() {
            var run = StrengthRun(session: opened.view.session, zones: opened.view.zones, saved: progress)
            guard let report = run.reportToSend(at: now) else { continue }
            run.apply(await client.sendStrengthReport(report, token: opened.token))
            try? store.save(run.progress, sid: opened.sid)
        }
    }
}
