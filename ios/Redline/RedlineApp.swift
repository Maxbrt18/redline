import RedlineKit
import SwiftUI

@main
struct RedlineApp: App {
    /// Read once: the host is fixed when the app is built.
    private static let server = Result<Server, ServerError> { () throws(ServerError) in
        try Server(bundle: .main)
    }

    var body: some Scene {
        WindowGroup {
            RootView(server: Self.server)
        }
    }
}
