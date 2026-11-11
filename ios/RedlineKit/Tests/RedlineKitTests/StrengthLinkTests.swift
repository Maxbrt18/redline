import Foundation
import Testing
@testable import RedlineKit

struct StrengthLinkTests {
    let server = try! Server(host: "coach.example.org")
    let token = StrengthToken(String(repeating: "A", count: 32))!

    func fixture(_ name: String) throws -> Data {
        try Data(contentsOf: try #require(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")))
    }

    @Test func aSessionLinkCarriesItsToken() throws {
        let url = try #require(URL(string: "https://coach.example.org/s#t=" + String(repeating: "b_-9", count: 8)))
        #expect(StrengthToken(link: url)?.value == String(repeating: "b_-9", count: 8))
    }

    @Test func thePageHandsTheSameTokenToTheApp() throws {
        let url = try #require(URL(string: "redline://s#t=" + String(repeating: "A", count: 32)))
        #expect(StrengthToken(link: url) == token)
    }

    @Test(arguments: ["https://coach.example.org/carnet#t=" + String(repeating: "A", count: 32),
                      "redline://connect#t=" + String(repeating: "A", count: 32),
                      "http://coach.example.org/s#t=" + String(repeating: "A", count: 32),
                      "https://coach.example.org/s#t=short",
                      "https://coach.example.org/s#t=" + String(repeating: "A", count: 31) + "=",
                      "https://coach.example.org/s"])
    func anythingElseIsNotASessionLink(text: String) throws {
        #expect(StrengthToken(link: try #require(URL(string: text))) == nil)
    }

    @Test func launchingAsksWithTheSignatureAndReadsTheToken() async throws {
        let transport = StubTransport(body: try fixture("launch"))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        #expect(try await client.launchStrength(signature: "abc") == token)
        let request = try #require(await transport.requests.first)
        #expect(request.url?.absoluteString == "https://coach.example.org/notebook-api/launch")
        #expect(try JSONDecoder().decode([String: String].self, from: try #require(request.httpBody)) == ["signature": "abc"])
    }

    @Test func aChangedSessionIsRefused() async {
        let transport = StubTransport(status: 409, body: Data(#"{"error": "La séance a changé."}"#.utf8))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        await #expect(throws: NotebookError.refused(status: 409, message: "La séance a changé.")) {
            try await client.launchStrength(signature: "abc")
        }
    }

    @Test func theSessionOpensWithTheTokenAloneInItsBody() async throws {
        let transport = StubTransport(body: try fixture("strength-session"))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        let view = try await client.strengthSession(token)
        #expect(view.session.sid == "w41-renfo-a")
        let request = try #require(await transport.requests.first)
        #expect(request.url?.absoluteString == "https://coach.example.org/strength-session")
        #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
        #expect(try JSONDecoder().decode([String: String].self, from: try #require(request.httpBody)) == ["token": token.value])
    }

    @Test(arguments: [(410, StrengthLinkError.expired), (403, .forbidden), (503, .unreachable), (429, .unreachable)])
    func aLinkThatDoesNotOpenSaysWhy(status: Int, error: StrengthLinkError) async {
        let client = NotebookClient(server: server, device: "d", transport: StubTransport(status: status))
        await #expect(throws: error) { try await client.strengthSession(token) }
    }

    @Test func noNetworkLeavesTheLinkUnopened() async {
        let client = NotebookClient(server: server, device: "d", transport: StubTransport(fails: true))
        await #expect(throws: StrengthLinkError.unreachable) { try await client.strengthSession(token) }
    }

    static let report = Notebook.StrengthReportPosted(performedOn: "2026-10-09", pains: [:], minutes: 20, note: "",
                                                      exercises: [])

    @Test(arguments: [(201, #"{"status": "accepted"}"#, StrengthReportOutcome.accepted),
                      (200, #"{"status": "duplicate"}"#, .duplicate),
                      (410, #"{"status": "expired"}"#, .expired),
                      (429, #"{"status": "quota"}"#, .quota),
                      (403, "forbidden", .forbidden),
                      (400, #"{"status": "invalid", "error": "performed_on missing"}"#, .invalid("performed_on missing")),
                      (503, "temporary failure", .unreachable)])
    func theServerSaysWhatBecameOfTheReport(status: Int, body: String, outcome: StrengthReportOutcome) async throws {
        let transport = StubTransport(status: status, body: Data(body.utf8))
        let client = NotebookClient(server: server, device: "d", transport: transport)
        #expect(await client.sendStrengthReport(Self.report, token: token) == outcome)
        let request = try #require(await transport.requests.first)
        #expect(request.url?.path() == "/strength-report")
        let sent = try JSONDecoder().decode([String: JSONValue].self, from: try #require(request.httpBody))
        #expect(Set(sent.keys) == ["token", "report"])
    }

    @Test func aReportWithoutNetworkWaits() async {
        let client = NotebookClient(server: server, device: "d", transport: StubTransport(fails: true))
        let outcome = await client.sendStrengthReport(Self.report, token: token)
        #expect(outcome == .unreachable && outcome.retries)
    }

    @Test func aPendingReportLeavesFromTheOutboxOnceTheNetworkIsBack() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = FileStrengthProgressStore(directory: directory)
        let view = try JSONDecoder().decode(Notebook.StrengthSessionView.self, from: fixture("strength-session"))
        try store.saveOpened(OpenedStrength(token: token, view: view))
        var run = StrengthRun(session: view.session, zones: view.zones)
        run.skipOpening()
        run.validate(12, at: .now)
        _ = run.reportToSend(at: .now)
        try store.save(run.progress, sid: view.session.sid)
        #expect(store.pendingReports().count == 1)

        let offline = NotebookClient(server: server, device: "d", transport: StubTransport(fails: true))
        await StrengthOutbox.flush(client: offline, store: store)
        #expect(store.pendingReports().count == 1)

        let transport = StubTransport(status: 201, body: Data(#"{"status": "accepted"}"#.utf8))
        await StrengthOutbox.flush(client: NotebookClient(server: server, device: "d", transport: transport), store: store)
        #expect(store.pendingReports().isEmpty)
        #expect(store.load(sid: view.session.sid)?.delivery == .sent(duplicate: false))
        #expect(await transport.requests.count == 1)
    }

    @Test func aRefusedReportGoesBackToTheReview() throws {
        let view = try JSONDecoder().decode(Notebook.StrengthSessionView.self, from: fixture("strength-session"))
        var run = StrengthRun(session: view.session, zones: view.zones)
        _ = run.reportToSend(at: .now)
        run.apply(.unreachable)
        #expect(run.isLocked)
        run.apply(.quota)
        #expect(!run.isLocked)
        _ = run.reportToSend(at: .now)
        run.apply(.duplicate)
        #expect(run.progress.delivery == .sent(duplicate: true))
    }
}
