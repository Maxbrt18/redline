import Testing
@testable import RedlineKit

struct NotebookLinkTests {
    let server = try! Server(host: "coach.example.org")
    let token = String(repeating: "aB3_-", count: 8) + "xyz"
    let card = "0b5a1c2e-3d4f-4a6b-8c9d-0e1f2a3b4c5d"

    @Test func theServersLinkGivesItsToken() throws {
        let link = try NotebookLink("https://coach.example.org/carnet#t=\(token)", server: server)
        #expect(link == NotebookLink(token: token, entry: nil))
    }

    @Test func aLinkToACardKeepsTheCard() throws {
        let link = try NotebookLink(" https://coach.example.org/carnet#t=\(token)&entry=\(card)\n", server: server)
        #expect(link.entry == card)
    }

    @Test func theAppsSchemeOpensTheSameLink() throws {
        let link = try NotebookLink("redline://connect#t=\(token)", server: server)
        #expect(link.token == token)
    }

    @Test func anotherServersLinkIsNamedAsSuch() {
        #expect(throws: NotebookLinkError.otherServer) {
            try NotebookLink("https://coach.other.org/carnet#t=\(token)", server: server)
        }
    }

    @Test(arguments: ["http://coach.example.org/carnet#t=x", "https://coach.example.org/s#t=x",
                      "bonjour", "redline://strength#t=x", ""])
    func whatIsNotANotebookLinkIsRefused(text: String) {
        #expect(throws: NotebookLinkError.notALink) { try NotebookLink(text, server: server) }
    }

    @Test func aLinkWithoutAWellFormedTokenIsRefused() {
        for fragment in ["", "#t=short", "#entry=\(card)", "#t=\(token)&entry=not-a-card"] {
            #expect(throws: NotebookLinkError.noToken) {
                try NotebookLink("https://coach.example.org/carnet\(fragment)", server: server)
            }
        }
    }
}

extension NotebookLink {
    init(token: String, entry: String?) {
        self = try! NotebookLink(
            "redline://connect#t=\(token)" + (entry.map { "&entry=\($0)" } ?? ""),
            server: try! Server(host: "coach.example.org"))
    }
}
