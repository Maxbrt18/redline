import Observation
import RedlineKit

/// Whether this device is connected to the athlete's notebook, and how it connects.
@Observable
final class NotebookSession {
    enum State {
        case signedOut
        case signedIn(NotebookClient)
    }

    let server: Server
    private(set) var state: State
    private(set) var connecting = false
    /// Why the last connection failed, in the athlete's words; nil once it succeeds.
    private(set) var failure: String?
    private let store: any DeviceTokenStore

    init(server: Server, store: any DeviceTokenStore) {
        self.server = server
        self.store = store
        state = store.read().map { .signedIn(NotebookClient(server: server, device: $0)) } ?? .signedOut
    }

    /// Connects with a link pasted or opened by the system; a device already connected
    /// keeps its token, as an old notification stays openable on the page.
    func connect(with text: String) async {
        if case .signedIn = state { return }
        let link: NotebookLink
        do {
            link = try NotebookLink(text, server: server)
        } catch {
            failure = switch error {
            case .notALink: "Ce n’est pas un lien de carnet. Envoie /carnet dans Messenger et copie le lien du bouton."
            case .otherServer: "Ce lien vient d’un autre coach que celui de cette app."
            case .noToken: "Ce lien est incomplet. Copie-le en entier depuis Messenger."
            }
            return
        }
        connecting = true
        defer { connecting = false }
        do {
            let device = try await NotebookClient.connect(link, server: server)
            try store.save(device)
            failure = nil
            state = .signedIn(NotebookClient(server: server, device: device))
        } catch let error as NotebookError {
            failure = Self.words(for: error)
        } catch {
            failure = "Impossible de garder la connexion sur cet appareil. Réessaie."
        }
    }

    /// Revokes the token on the server when it can; the device forgets it either way.
    func signOut() async {
        if case .signedIn(let client) = state {
            try? await client.signOut()
        }
        forget()
    }

    /// The server no longer knows this device: back to the connection screen.
    func forget(saying message: String? = nil) {
        store.delete()
        failure = message
        state = .signedOut
    }

    static func words(for error: NotebookError) -> String {
        switch error {
        case .signedOut(let message) where !message.isEmpty:
            message
        case .refused(_, let message) where !message.isEmpty:
            message
        case .signedOut:
            "Ce lien a expiré ou a déjà servi. Envoie /carnet dans Messenger."
        case .unreachable:
            "Pas de réponse du serveur. Vérifie ta connexion, puis réessaie."
        default:
            "Le serveur a répondu de travers. Réessaie dans un instant."
        }
    }
}
