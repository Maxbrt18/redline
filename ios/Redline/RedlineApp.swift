import RedlineKit
import SwiftUI

@main
struct RedlineApp: App {
    /// Nil when the build carries no valid server host: the app then says so.
    @State private var session: NotebookSession? = {
        #if DEBUG
        if UserDefaults.standard.bool(forKey: "RedlineDemo") { return DemoNotebook.session() }
        #endif
        guard let server = try? Server(bundle: .main) else { return nil }
        return NotebookSession(server: server, store: KeychainDeviceTokenStore(server: server),
                               cache: FileResponseCache(server: server))
    }()

    init() {
        Fonts.register()
        StrengthLive.install()
    }

    var body: some Scene {
        WindowGroup {
            RootView(session: session)
        }
    }
}
