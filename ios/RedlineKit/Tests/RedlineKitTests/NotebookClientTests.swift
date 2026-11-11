import Foundation
import Testing
@testable import RedlineKit

/// Answers every request with one canned response and keeps what it was asked.
actor StubTransport: HTTPTransport {
    private let status: Int
    private let body: Data
    private let fails: Bool
    private(set) var requests: [URLRequest] = []

    init(status: Int = 200, body: Data = Data("{}".utf8), fails: Bool = false) {
        self.status = status
        self.body = body
        self.fails = fails
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        requests.append(request)
        if fails { throw URLError(.notConnectedToInternet) }
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        return (body, response)
    }
}

struct NotebookClientTests {
    let server = try! Server(host: "coach.example.org")
    let link = NotebookLink(token: String(repeating: "a", count: 43), entry: nil)

    @Test func connectingTradesTheLinkForADeviceToken() async throws {
        let transport = StubTransport(body: Data(#"{"device": "d3v1ce"}"#.utf8))
        let device = try await NotebookClient.connect(link, server: server, transport: transport)
        #expect(device == "d3v1ce")
        let request = try #require(await transport.requests.first)
        #expect(request.url?.absoluteString == "https://coach.example.org/notebook-api/devices")
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
        let body = try JSONDecoder().decode([String: String].self, from: try #require(request.httpBody))
        #expect(body == ["token": link.token])
    }

    @Test func aReadSendsTheDeviceTokenAndDecodesItsView() async throws {
        let fixture = try #require(Bundle.module.url(forResource: "weeks", withExtension: "json", subdirectory: "Fixtures"))
        let transport = StubTransport(body: try Data(contentsOf: fixture))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        let weeks = try await client.get(.weeks)
        #expect(!weeks.weeks.isEmpty)
        let request = try #require(await transport.requests.first)
        #expect(request.url?.absoluteString == "https://coach.example.org/notebook-api/weeks")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer d3v1ce")
    }

    @Test func aPathSegmentIsEscaped() async throws {
        let transport = StubTransport(status: 404, body: Data())
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        _ = try? await client.get(.week("2026-W41/../x"))
        let request = try #require(await transport.requests.first)
        #expect(request.url?.path(percentEncoded: true) == "/notebook-api/week/2026-W41%2F..%2Fx")
    }

    @Test func aForecastCarriesItsQueryAndIsNeverKept() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = FileResponseCache(directory: directory)
        let transport = StubTransport(body: Data("null".utf8))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport, cache: cache)
        let endpoint = Endpoint.weather(date: "2026-10-09", time: "17:00", place: "Le Parc")
        #expect(try await client.get(endpoint) == nil)
        let request = try #require(await transport.requests.first)
        #expect(request.url?.absoluteString == "https://coach.example.org/notebook-api/weather?date=2026-10-09&time=17:00&place=Le%20Parc")
        #expect(cache.read(endpoint.cacheKey) == nil)
    }

    @Test func aRevokedDeviceIsSignedOutWithTheServersWords() async {
        let transport = StubTransport(status: 401, body: Data(#"{"error": "Ouvre un nouveau lien."}"#.utf8))
        let client = NotebookClient(server: server, device: "old", transport: transport)
        await #expect(throws: NotebookError.signedOut("Ouvre un nouveau lien.")) { try await client.get(.today) }
    }

    @Test func aRefusalKeepsItsStatusAndMessage() async {
        let transport = StubTransport(status: 503, body: Data(#"{"error": "Indisponible."}"#.utf8))
        let client = NotebookClient(server: server, device: "d3v1ce", transport: transport)
        await #expect(throws: NotebookError.refused(status: 503, message: "Indisponible.")) {
            try await client.get(.injury)
        }
    }

    @Test func noNetworkIsUnreachable() async {
        let client = NotebookClient(server: server, device: "d3v1ce", transport: StubTransport(fails: true))
        await #expect(throws: NotebookError.unreachable) { try await client.get(.race) }
    }

    @Test func anAnswerOutsideTheContractIsUnreadable() async {
        let client = NotebookClient(server: server, device: "d3v1ce", transport: StubTransport(body: Data("[]".utf8)))
        await #expect(throws: NotebookError.unreadable) { try await client.get(.habits) }
    }

    @Test func signingOutRevokesThisDevice() async throws {
        let transport = StubTransport(body: Data(#"{"status": "ok"}"#.utf8))
        try await NotebookClient(server: server, device: "d3v1ce", transport: transport).signOut()
        let request = try #require(await transport.requests.first)
        #expect(request.url?.path() == "/notebook-api/logout")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer d3v1ce")
    }
}
