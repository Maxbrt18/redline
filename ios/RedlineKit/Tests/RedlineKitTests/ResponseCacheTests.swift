import Foundation
import Testing
@testable import RedlineKit

struct ResponseCacheTests {
    let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    let server = try! Server(host: "coach.example.org")

    @Test func aReadKeepsItsAnswerForTheNextLaunch() async throws {
        let fixture = try #require(Bundle.module.url(forResource: "weeks", withExtension: "json", subdirectory: "Fixtures"))
        let cache = FileResponseCache(directory: directory)
        let client = NotebookClient(server: server, device: "d3v1ce",
                                    transport: StubTransport(body: try Data(contentsOf: fixture)), cache: cache)
        #expect(client.cached(.weeks) == nil)
        let fresh = try await client.get(.weeks)
        let offline = NotebookClient(server: server, device: "d3v1ce", transport: StubTransport(fails: true), cache: cache)
        let kept = try #require(offline.cached(.weeks))
        #expect(kept.value == fresh)
        await #expect(throws: NotebookError.unreachable) { try await offline.get(.weeks) }
        #expect(offline.cached(.weeks)?.value == fresh)
    }

    @Test func aRefusedAnswerReplacesNothing() async throws {
        let cache = FileResponseCache(directory: directory)
        cache.write(Data(#"{"habits": []}"#.utf8), for: "habits")
        let client = NotebookClient(server: server, device: "d3v1ce",
                                    transport: StubTransport(status: 503, body: Data()), cache: cache)
        _ = try? await client.get(.habits)
        #expect(client.cached(.habits)?.value.habits == [])
    }

    @Test func clearingForgetsEveryAnswer() {
        let cache = FileResponseCache(directory: directory)
        cache.write(Data("{}".utf8), for: "today")
        cache.clear()
        #expect(cache.read("today") == nil)
    }

    @Test func aKeyBecomesASafeFileName() {
        #expect(FileResponseCache.fileName("week/2026-W46") == "week_2026-W46")
        #expect(FileResponseCache.fileName("session/../é") == "session_____")
    }
}
