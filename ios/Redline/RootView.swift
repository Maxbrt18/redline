import RedlineKit
import SwiftUI

/// The notebook's three sections, one question each: Today follows the time of day, Week
/// judges the past and prescribes the rest, Race looks ahead.
enum NotebookTab: Hashable, CaseIterable {
    case today, week, race

    var title: LocalizedStringKey {
        switch self {
        case .today: "Aujourd’hui"
        case .week: "Semaine"
        case .race: "Course"
        }
    }

    var symbol: String {
        switch self {
        case .today: "calendar"
        case .week: "chart.bar"
        case .race: "flag"
        }
    }
}

struct RootView: View {
    let session: NotebookSession?
    @State private var selection: NotebookTab = .today
    @State private var showingSettings = false
    /// Each tab's stack, so that a card received in Messenger can open on top of Today.
    @State private var paths: [NotebookTab: NavigationPath] = [:]
    @AppStorage(Theme.storageKey) private var theme = Theme.auto

    var body: some View {
        content.onChange(of: theme, initial: true) { _, theme in theme.apply() }
    }

    @ViewBuilder private var content: some View {
        #if DEBUG
        // `-RedlineCatalogue all` (or `track`) at launch opens the components, connected or not.
        switch UserDefaults.standard.string(forKey: "RedlineCatalogue") {
        case "all": NavigationStack { DesignCatalogue() }
        case "track": NavigationStack { TrackPreview() }
        default: notebook
        }
        #else
        notebook
        #endif
    }

    @ViewBuilder private var notebook: some View {
        if let session {
            Group {
                switch session.state {
                case .signedOut:
                    ConnectView(session: session)
                case .signedIn:
                    tabs(session)
                        .sheet(isPresented: $showingSettings) { SettingsView(session: session) }
                }
            }
            .onOpenURL { url in
                Task { await session.connect(with: url.absoluteString) }
            }
        } else {
            ContentUnavailableView {
                Label("Serveur non configuré", systemImage: "network.slash")
            } description: {
                Text("Renseigne `REDLINE_SERVER_HOST` dans `Config/Local.xcconfig`, puis reconstruis l’app.")
            }
        }
    }

    private func tabs(_ session: NotebookSession) -> some View {
        TabView(selection: $selection) {
            tabContent(session)
        }
        // A card received in Messenger opens on top of Today: back leads there.
        .onChange(of: session.pendingEntry, initial: true) { _, entry in
            guard let entry else { return }
            session.pendingEntry = nil
            selection = .today
            var today = NavigationPath()
            today.append(EntryLink(id: entry))
            paths[.today] = today
        }
    }

    @TabContentBuilder<NotebookTab> private func tabContent(_ session: NotebookSession) -> some TabContent<NotebookTab> {
        ForEach(NotebookTab.allCases, id: \.self) { tab in
            Tab(tab.title, systemImage: tab.symbol, value: tab) {
                NavigationStack(path: Binding(get: { paths[tab] ?? NavigationPath() }, set: { paths[tab] = $0 })) {
                    Group {
                        switch tab {
                        case .today: TodayScreen(session: session) { selection = $0 }
                        case .week: WeekScreen(session: session)
                        case .race: RaceScreen(session: session)
                        }
                    }
                    .navigationDestination(for: SessionLink.self) { link in
                        SessionDetailScreen(session: session, link: link)
                    }
                    .navigationDestination(for: PainLink.self) { link in
                        PainScreen(session: session, link: link)
                    }
                    .navigationDestination(for: EntryLink.self) { link in
                        EntryScreen(session: session, link: link)
                    }
                    .navigationDestination(for: ArchivesLink.self) { _ in
                        ArchivesScreen(session: session)
                    }
                }
                // On the stack, not its root: the screens pushed on it read them too.
                .environment(\.openSettings) { showingSettings = true }
                .environment(\.notebook, session)
            }
        }
    }
}

#Preview("Connecté") {
    let server = try! Server(host: "coach.example.org")
    RootView(session: NotebookSession(server: server, store: PreviewTokenStore(token: "preview")))
}

#Preview("À connecter") {
    let server = try! Server(host: "coach.example.org")
    RootView(session: NotebookSession(server: server, store: PreviewTokenStore(token: nil)))
}

/// A store that never touches the keychain, for previews.
private struct PreviewTokenStore: DeviceTokenStore {
    let token: String?
    func read() -> String? { token }
    func save(_ token: String) throws {}
    func delete() {}
}
