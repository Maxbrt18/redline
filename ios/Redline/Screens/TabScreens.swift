import RedlineKit
import SwiftUI

/// Race: what the training aims at.
struct RaceScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: NotebookTab.race.title, session: session, model: ScreenModel(.race)) { race in
            if let target = race.race {
                Kicker(text: "\(target.name) · dans \(target.days) jours")
            } else {
                Kicker(text: "Aucune course déclarée")
            }
        } content: { _ in
            EmptyView()
        }
    }
}

/// The small line above a hero: a date, a week, a race.
struct Kicker: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote.weight(.semibold))
            .opacity(0.82)
    }
}

extension String {
    /// The first letter in capital, as a sentence starts.
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
