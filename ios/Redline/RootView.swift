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
    let server: Result<Server, ServerError>
    @State private var selection: NotebookTab = .today

    var body: some View {
        switch server {
        case .success:
            TabView(selection: $selection) {
                ForEach(NotebookTab.allCases, id: \.self) { tab in
                    Tab(tab.title, systemImage: tab.symbol, value: tab) {
                        NavigationStack {
                            ScrollView {}
                                .navigationTitle(tab.title)
                        }
                    }
                }
            }
        case .failure:
            ContentUnavailableView {
                Label("Serveur non configuré", systemImage: "network.slash")
            } description: {
                Text("Renseigne `REDLINE_SERVER_HOST` dans `Config/Local.xcconfig`, puis reconstruis l’app.")
            }
        }
    }
}

#Preview {
    RootView(server: .success(try! Server(host: "coach.example.org")))
}
