import RedlineKit
import SwiftUI

/// A day's thread: what is done, in order, then what remains. Detail decreases with
/// distance: the step to do now has everything, the next ones less. The first step to do
/// reads « À faire » in red; nothing else marks it.
struct DayThread: View {
    let items: [Notebook.SessionItem]
    let extra: [Notebook.UnplannedFact]
    /// The session the hero already shows: its step keeps its body but not its title.
    var hero: Notebook.SessionItem?
    var strength: Notebook.StrengthOffer?
    /// Today's thread names its steps by role; another day's thread only lists them.
    var today = true

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            ForEach(Array(done.enumerated()), id: \.offset) { _, entry in
                doneStep(entry)
            }
            ForEach(Array(pending.enumerated()), id: \.element.id) { index, item in
                pendingStep(item, index: index)
            }
        }
    }

    private enum Done {
        case session(Notebook.SessionItem)
        case unplanned(Notebook.UnplannedFact)

        var startedAt: String {
            switch self {
            case .session(let item): item.runFact?.startedAt ?? "~"
            case .unplanned(let fact): fact.startedAt ?? "~"
            }
        }
    }

    private var done: [Done] {
        let runs = items.filter { !$0.isStrength && $0.isDone }.map(Done.session)
        let strengths = items.filter { $0.isStrength && $0.isDone }.map(Done.session)
        return (runs + extra.map(Done.unplanned) + strengths).sorted { $0.startedAt < $1.startedAt }
    }

    private var pending: [Notebook.SessionItem] {
        items.filter { !$0.isStrength && !$0.isDone } + items.filter { $0.isStrength && !$0.isDone }
    }

    @ViewBuilder private func doneStep(_ entry: Done) -> some View {
        switch entry {
        case .session(let item) where item.isStrength:
            ThreadStep(kicker: ["Fait", item.strengthFact?.durationMin.map { "\($0) min" }].compactMap { $0 }.joined(separator: " · "),
                       title: item.title, family: .strength, small: true)
        case .session(let item):
            let fact = item.runFact
            ThreadStep(kicker: ["Fait", fact?.startClock].compactMap { $0 }.joined(separator: " · "),
                       title: item.id == hero?.id ? nil : item.title, family: item.family) {
                if let fact {
                    if let rows = LimitRows(targets: item.targets, fact: fact) { rows } else { FactTiles(fact: fact, targets: item.targets) }
                    OverLine(fact: fact, targets: item.targets)
                }
            }
        case .unplanned(let fact):
            ThreadStep(kicker: ["Fait", fact.startedAt.flatMap(Numbers.clock), "hors programme"].compactMap { $0 }.joined(separator: " · "),
                       title: Notebook.kindName(fact.kind), family: Family(kind: fact.kind)) {
                FactTiles(fact: Notebook.Fact(unplanned: fact), targets: Notebook.Targets.none)
            }
        }
    }

    @ViewBuilder private func pendingStep(_ item: Notebook.SessionItem, index: Int) -> some View {
        let now = today && index == 0
        let role = !today ? nil : now ? "À faire" : index > 0 ? "Puis" : "Ensuite"
        let kicker = [role, item.startTime.flatMap(Numbers.clock), item.location].compactMap { $0 }.joined(separator: " · ")
        ThreadStep(kicker: kicker, now: now, title: item.id == hero?.id ? nil : item.title, family: item.family,
                   small: item.isStrength || !today) {
            if item.status == "held" {
                Text("Une question t’attend dans Messenger avant cette séance.").font(.subheadline).foregroundStyle(Palette.muted)
            } else if item.isStrength {
                if let note = item.note { Text(note).font(.subheadline).foregroundStyle(Palette.muted) }
                if today, case .ready(let ready)? = strength { ExerciseList(session: ready.session) }
            } else {
                if today { TargetCursors(targets: item.targets) }
                if let note = item.note, today { Text(note).font(.subheadline).foregroundStyle(Palette.muted) }
            }
        }
    }
}

/// One step of a thread: its role, its title with its family badge, then its body.
struct ThreadStep<Body: View>: View {
    let kicker: String
    var now = false
    let title: String?
    let family: Family
    var small = false
    @ViewBuilder var content: Body

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !kicker.isEmpty {
                Text(kicker)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(now ? Palette.red : Palette.muted)
            }
            if let title {
                HStack(spacing: 10) {
                    FamilyBadge(family: family, small: small)
                    Text(title).font(small ? .body.weight(.semibold) : .title3.weight(.semibold))
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension ThreadStep where Body == EmptyView {
    init(kicker: String, now: Bool = false, title: String?, family: Family, small: Bool = false) {
        self.init(kicker: kicker, now: now, title: title, family: family, small: small) { EmptyView() }
    }
}

/// The day's strength exercises, folded under their count.
struct ExerciseList: View {
    let session: [String: JSONValue]

    var body: some View {
        let exercises = Self.exercises(session)
        if !exercises.isEmpty {
            DisclosureGroup("\(exercises.count) exercice\(exercises.count > 1 ? "s" : "")") {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(exercises.enumerated()), id: \.offset) { _, exercise in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(exercise.name)
                            if !exercise.dose.isEmpty {
                                Text(exercise.dose).font(.subheadline).foregroundStyle(Palette.muted)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 10)
                        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
                    }
                }
            }
            .tint(Palette.ink)
        }
    }

    /// The exercises of a prescription: its name (or slug) and its dose in words.
    static func exercises(_ session: [String: JSONValue]) -> [(name: String, dose: String)] {
        guard case .array(let list)? = session["exercises"] else { return [] }
        return list.compactMap { item in
            guard case .object(let exercise) = item else { return nil }
            let name: String? = if case .string(let name)? = exercise["name"] { name } else if case .string(let slug)? = exercise["slug"] { slug } else { nil }
            var dose = ""
            if case .object(let parts)? = exercise["dose"], case .string(let text)? = parts["text"] { dose = text }
            return name.map { ($0, dose) }
        }
    }
}

extension Notebook.Fact {
    /// An unplanned run read as a run's fact: the same measures, no session to carry it.
    init(unplanned fact: Notebook.UnplannedFact) {
        self.init(observationId: fact.observationId, kind: fact.kind, date: fact.date, km: fact.km,
                  durationMin: fact.durationMin, hrAvg: fact.hrAvg, rpe: fact.rpe, pains: fact.pains,
                  enjoyment: fact.enjoyment, dplus: fact.dplus, startedAt: fact.startedAt, over: fact.over)
    }
}

extension Notebook.Targets {
    /// No target at all.
    static var none: Notebook.Targets { Notebook.Targets() }
}
