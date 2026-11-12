import RedlineKit
import SwiftUI

/// What comes back every week: ticked in advance on its weekday, read by the coach when
/// writing the week. A habit is made from a day of the reorganisation.
struct HabitsScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: "Mes habitudes", session: session, model: ScreenModel(.habits), onTrack: false) { _ in
            Intro(text: "Elles sont cochées d’avance sur tes jours, et le coach les lit quand il écrit ta semaine. Pour en créer une, touche un jour dans Réorganiser puis « Tous les … ».")
        } content: { view in
            HabitsList(view: view, session: session)
        }
    }
}

private struct HabitsList: View {
    let view: Notebook.HabitsView
    let session: NotebookSession
    @Environment(\.reloadScreen) private var reload
    @State private var stopping: String?
    @State private var failure: String?

    private static let weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if view.habits.isEmpty {
                Text("Aucune habitude pour le moment.").font(.subheadline).foregroundStyle(Palette.muted)
            } else {
                SectionTitle(text: "Chaque semaine")
                ForEach(view.habits, id: \.id) { habit in
                    row(habit)
                }
            }
            if let failure { Text(failure).font(.footnote).foregroundStyle(Palette.over).padding(.top, 8) }
        }
        .padding(.bottom, 24)
    }

    private func row(_ habit: Notebook.Habit) -> some View {
        let name = Self.weekdays[max(0, min(6, habit.weekday - 1))]
        let kind = StatedDay.Kind(rawValue: habit.kind.rawValue)
        let day = StatedDay(date: habit.startsOn, kind: kind, minutes: habit.minutes,
                            pace: habit.pace.map { StatedDay.Pace(rawValue: $0.rawValue) },
                            required: habit.required, sport: habit.sport, demanding: habit.demanding, runToo: habit.runToo)
        let since = (habit.startsOn > Day(.now).text ? "à partir du " : "depuis le ") + (Day(habit.startsOn)?.spelled(weekday: false) ?? habit.startsOn)
        let detail = [DayWords.line(day), since, habit.endsOn.flatMap { Day($0)?.spelled(weekday: false) }.map { "jusqu’au " + $0 }]
            .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name.capitalizedFirst + " · " + DayWords.label(kind).lowercased()).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(Palette.muted)
            }
            Spacer()
            Button("Arrêter", role: .destructive) { Task { await stop(habit) } }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(stopping != nil)
        }
        .padding(.vertical, 12)
    }

    private func stop(_ habit: Notebook.Habit) async {
        guard case .signedIn(let client) = session.state else { return }
        stopping = habit.id
        defer { stopping = nil }
        do {
            try await client.endHabit(habit.id)
            failure = nil
            await reload?()
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }
}
