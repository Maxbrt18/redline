import RedlineKit
import SwiftUI

/// One day, in a sheet: what is planned, how the day presents itself and its details,
/// and whether it comes back every week. Each change recomputes the variants underneath.
struct DaySheet: View {
    let model: ReorganizeModel
    let day: Notebook.ReorganizeDay
    let session: NotebookSession
    @Environment(\.dismiss) private var dismiss
    @State private var savingHabit = false
    @State private var failure: String?

    private var stated: StatedDay { model.known(day) ?? StatedDay(date: day.date) }

    var body: some View {
        NavigationStack {
            Form {
                if !day.sessions.isEmpty {
                    Section("Prévu") {
                        ForEach(day.sessions, id: \.id) { planned in
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: Space.s) {
                                    FamilyBadge(family: Family(kind: planned.kind, workout: planned.workout), small: true)
                                    Text(planned.title).textRole(.body)
                                    Spacer()
                                    Text(planned.figure).textRole(.secondary).foregroundStyle(Palette.muted)
                                }
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text(planned.title).textRole(.body)
                                    Text(planned.figure).textRole(.secondary).foregroundStyle(Palette.muted)
                                }
                            }
                        }
                    }
                }
                Section("Ce jour-là") {
                    Picker("Ce jour-là", selection: Binding(get: { stated.kind }, set: { change(StatedDay.fresh(date: day.date, kind: $0)) })) {
                        ForEach(DayWords.states, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    .pickerStyle(.inline)
                    .labelsHidden()
                }
                details
                Section {
                    Toggle("Tous les \(DayWords.weekdayName(day.date))s", isOn: Binding(get: { day.habitId != nil }, set: habit))
                        .disabled(savingHabit || (day.habitId == nil && stated.kind == .asPlanned))
                } footer: {
                    Text(day.habitId != nil
                         ? "Une habitude : elle revient chaque semaine. Tu la retrouves dans Mes habitudes."
                         : "En faire une habitude : elle reviendra chaque semaine, et le coach en tiendra compte en écrivant ta semaine.")
                    if let failure { RequestFailure(text: failure) }
                }
            }
            .navigationTitle(Day(day.date)?.weekdayAndNumber.capitalizedFirst ?? day.date)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Fermer", systemImage: "checkmark") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .sensoryFeedback(.selection, trigger: stated)
    }

    @ViewBuilder private var details: some View {
        switch stated.kind {
        case .short:
            Section {
                segments("Au plus", [(30, "30 min"), (45, "45 min"), (60, "1 h")], value: stated.minutes ?? 45) { minutes in
                    var next = stated
                    next.minutes = minutes
                    change(next)
                }
            } header: {
                Text("Le temps")
            } footer: {
                Text("Seule une séance qui tient dans ce temps s’y place.")
            }
        case .withOthers:
            Section {
                segments("Durée", [(45, "45 min"), (60, "1 h"), (90, "1 h 30"), (120, "2 h +")], value: stated.minutes ?? 60) { minutes in
                    var next = stated
                    next.minutes = minutes
                    change(next)
                }
                segments("Allure", DayWords.paces, value: stated.pace ?? .easy) { pace in
                    var next = stated
                    next.pace = pace
                    change(next)
                }
                segments("Rendez-vous", [(false, "Si ça rentre"), (true, "Obligatoire")], value: stated.required ?? false) { required in
                    var next = stated
                    next.required = required
                    change(next)
                }
            } header: {
                Text("Le groupe")
            } footer: {
                Text("Une séance qui va avec le groupe s’y place : une EF ou une SL pour une allure tranquille, une séance de qualité seulement pour une séance de club.")
            }
        case .otherSport:
            Section {
                // The engine never reads the name: it is kept at each keystroke, nothing is
                // recomputed.
                TextField("Nom du sport", text: Binding(
                    get: { stated.sport == StatedDay.unnamedSport ? "" : stated.sport ?? "" },
                    set: { name in
                        var next = stated
                        let trimmed = String(name.prefix(40)).trimmingCharacters(in: .whitespaces)
                        next.sport = trimmed.isEmpty ? StatedDay.unnamedSport : trimmed
                        model.keep(next)
                    }), prompt: Text("Volley, vélo…"))
                    .submitLabel(.done)
                segments("Effort", [(false, "Léger"), (true, "Exigeant")], value: stated.demanding ?? false) { demanding in
                    var next = stated
                    next.demanding = demanding
                    change(next)
                }
                Toggle("Courir aussi ce jour-là", isOn: Binding(get: { stated.runToo ?? false }, set: { runToo in
                    var next = stated
                    next.runToo = runToo
                    change(next)
                }))
            } header: {
                Text("Le sport")
            } footer: {
                Text("Exigeant : il compte comme un jour dur, donc pas de séance exigeante la veille ni le lendemain. Léger : le jour est seulement pris.")
            }
        default:
            EmptyView()
        }
    }

    private func segments<Value: Hashable>(_ label: String, _ options: [(Value, String)], value: Value,
                                           set: @escaping (Value) -> Void) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(label).textRole(.secondary)
            Picker(label, selection: Binding(get: { value }, set: set)) {
                ForEach(options, id: \.0) { Text($0.1).tag($0.0) }
            }
            .segmentsOrMenu()
            .labelsHidden()
        }
        .padding(.vertical, Space.xxs)
    }

    private func change(_ next: StatedDay) {
        Task { await model.set(next, session) }
    }

    /// Making a day a habit sends its state as it is now; ending one stops it from today.
    private func habit(_ on: Bool) {
        guard case .signedIn(let client) = session.state else { return }
        savingHabit = true
        Task {
            defer { savingHabit = false }
            do {
                if on {
                    try await client.addHabit(stated, weekday: DayWords.isoWeekday(day.date))
                } else if let id = day.habitId {
                    try await client.endHabit(id)
                }
                failure = nil
                model.forget(day.date)
                await model.load(session)
            } catch {
                failure = NotebookSession.words(for: error as? NotebookError ?? .unreachable)
            }
        }
    }
}
