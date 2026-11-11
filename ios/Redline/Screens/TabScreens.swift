import RedlineKit
import SwiftUI

/// Week: the week judged so far, and the rest prescribed.
struct WeekScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: NotebookTab.week.title, session: session,
                       model: ScreenModel(.week(Day(.now).isoWeek))) { week in
            Kicker(text: Self.span(of: week))
        } content: { _ in
            EmptyView()
        }
    }

    /// « Semaine 46 · du 9 novembre » from the week's label and its Monday.
    private static func span(of week: Notebook.WeekView) -> String {
        let number = week.week.split(separator: "W").last.flatMap { Int($0) }.map { "Semaine \($0)" } ?? week.week
        guard let monday = Day(week.monday) else { return number }
        return "\(number) · du \(monday.spelled(weekday: false))"
    }
}

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
