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
    @State private var confirming: String?
    @State private var failure: String?
    @Environment(\.dynamicTypeSize) private var typeSize

    private static let weekdays = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if view.habits.isEmpty {
                Text("Aucune habitude pour le moment.").textRole(.secondary).foregroundStyle(Palette.muted)
            } else {
                SectionTitle(text: "Chaque semaine")
                ForEach(view.habits, id: \.id) { habit in
                    row(habit)
                }
            }
            if let failure { RequestFailure(text: failure).padding(.top, Space.xs) }
        }
        .padding(.bottom, Space.l)
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
        let words = VStack(alignment: .leading, spacing: 2) {
            Text(name.capitalizedFirst + " · " + DayWords.label(kind).lowercased()).textRole(.secondary).fontWeight(.semibold)
            Text(detail).textRole(.caption).foregroundStyle(Palette.muted)
        }
        return Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: Space.xs) {
                    words
                    stopButton(habit, name: name)
                }
            } else {
                HStack(spacing: Space.s) {
                    words
                    Spacer(minLength: Space.xs)
                    stopButton(habit, name: name)
                }
            }
        }
        .padding(.vertical, Space.s)
    }

    /// Stopping a habit asks first, in a dialog anchored on the button that asked.
    private func stopButton(_ habit: Notebook.Habit, name: String) -> some View {
        Button { confirming = habit.id } label: {
            Text("Arrêter").frame(minHeight: minTarget)
        }
        .buttonStyle(.bordered)
        .tint(Palette.accent)
        .disabled(stopping != nil)
        .confirmationDialog("Arrêter l’habitude du \(name) ?", isPresented: Binding(
            get: { confirming == habit.id }, set: { if !$0 { confirming = nil } }), titleVisibility: .visible) {
            Button("Arrêter l’habitude", role: .destructive) { Task { await stop(habit) } }
        } message: {
            Text("Elle ne reviendra plus à partir d’aujourd’hui. Tu peux en recréer une depuis Réorganiser.")
        }
    }

    /// A bordered button's label at this height gives a tap target of at least 44 pt.
    @ScaledMetric(relativeTo: .body) private var minTarget: CGFloat = 32

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
