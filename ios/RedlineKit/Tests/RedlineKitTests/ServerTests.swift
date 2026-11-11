import Foundation
import Testing
@testable import RedlineKit

struct ServerTests {
    @Test func aHostBecomesAnHTTPSBaseURL() throws {
        #expect(try Server(host: "coach.example.org").baseURL == URL(string: "https://coach.example.org"))
    }

    @Test func aHostMayCarryAPort() throws {
        #expect(try Server(host: "coach.example.org:8443").baseURL == URL(string: "https://coach.example.org:8443"))
    }

    @Test func surroundingSpacesAreIgnored() throws {
        #expect(try Server(host: " coach.example.org ").baseURL == URL(string: "https://coach.example.org"))
    }

    @Test(arguments: ["", "   "])
    func anEmptyHostIsMissing(host: String) {
        #expect(throws: ServerError.missingHost) { try Server(host: host) }
    }

    @Test(arguments: [
        "https://coach.example.org",
        "coach.example.org/carnet",
        "coach example.org",
        "athlete@coach.example.org",
        "coach.example.org?next=1",
        "coach.example.org#t=abc",
    ])
    func anythingButAHostIsRefused(host: String) {
        #expect(throws: ServerError.invalidHost(host)) { try Server(host: host) }
    }

    @Test func aBundleWithoutTheKeyHasNoHost() {
        #expect(throws: ServerError.missingHost) { try Server(bundle: Bundle(for: Marker.self)) }
    }
}

private final class Marker {}
