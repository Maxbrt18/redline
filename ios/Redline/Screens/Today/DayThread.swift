import RedlineKit
import SwiftUI

/// A day's thread: what is done, in order, then what remains. Detail decreases with
/// distance: the step to do now has everything, the next ones less. The first step to do
/// reads « À faire » in the red line; nothing else marks it.
struct DayThread: View {
    let items: [Notebook.SessionItem]
    let extra: [Notebook.UnplannedFact]
    /// The session the hero already shows: its step keeps its body but not its title.
    var hero: Notebook.SessionItem?
    var strength: Notebook.StrengthOffer?
    /// Today's thread names its steps by role; another day's thread only lists them.
    var today = true
    /// Today's questions: the morning's first, then those about an earlier run, each under
    /// its own run when that run is today's.
    var asks: [Notebook.Ask] = []
    var date: String?
    @Environment(\.notebook) private var session

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            if !wakes.isEmpty || daily != nil {
                ThreadStep(kicker: "Ce matin", now: true, title: "Comment tu te réveilles ?", family: nil, small: true) {
                    MorningCard(wakes: wakes, daily: daily)
                }
            }
            ForEach(Array(loose.enumerated()), id: \.offset) { index, ask in
                looseStep(ask, now: index == 0 && wakes.isEmpty && daily == nil)
            }
            ForEach(Array(done.enumerated()), id: \.offset) { _, entry in
                doneStep(entry)
            }
            ForEach(Array(pending.enumerated()), id: \.element.id) { index, item in
                pendingStep(item, index: index)
            }
        }
    }

    private var wakes: [Notebook.WakeAsk] {
        asks.compactMap { if case .wake(let wake) = $0 { wake } else { nil } }
    }

    private var daily: Notebook.DailyAsk? {
        asks.lazy.compactMap { if case .daily(let daily) = $0 { daily } else { nil } }.first
    }

    /// Today's done runs carry their own debrief and heart question.
    private var ownRuns: [String] { items.filter { !$0.isStrength && $0.isDone }.compactMap { $0.runFact?.observationId } }

    private var loose: [Notebook.Ask] {
        let own = Set(ownRuns.map { "cell:\($0)" })
        return asks.filter { ask in
            switch ask {
            case .cells(let cells): !own.contains(cells.id)
            case .hr(let hr): !(hr.date == date && !ownRuns.isEmpty)
            default: false
            }
        }
    }

    @ViewBuilder private func looseStep(_ ask: Notebook.Ask, now: Bool) -> some View {
        switch ask {
        case .cells(let cells):
            ThreadStep(kicker: "À compléter", now: now, title: Self.askTitle(Notebook.kindName(cells.kind), cells.date, km: cells.km),
                       family: Family(kind: cells.kind), small: true) {
                DebriefCard(ask: cells)
            }
        case .hr(let hr):
            ThreadStep(kicker: "À compléter", now: now, title: Self.askTitle(Notebook.kindName("easy"), hr.date, km: nil),
                       family: .run, small: true) {
                HeartCard(ask: hr)
            }
        default:
            EmptyView()
        }
    }

    /// « EF de mardi 7 · 5,7 km ».
    private static func askTitle(_ what: String, _ date: String, km: Double?) -> String {
        "\(what) de \(Day(date)?.weekdayAndNumber ?? date)" + (km.map { " · \(Numbers.text($0)) km" } ?? "")
    }

    private func ownCells(_ observation: String) -> Notebook.CellsAsk? {
        asks.lazy.compactMap { if case .cells(let cells) = $0, cells.id == "cell:\(observation)" { cells } else { nil } }.first
    }

    private var ownHeart: Notebook.HrAsk? {
        guard !ownRuns.isEmpty else { return nil }
        return asks.lazy.compactMap { if case .hr(let hr) = $0, hr.date == date { hr } else { nil } }.first
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
                    DetailLink(link: SessionLink(id: fact.observationId, title: item.title.shortTitle))
                    if today, fact.observationId == ownRuns.first, let heart = ownHeart { HeartCard(ask: heart) }
                    if today, let cells = ownCells(fact.observationId) { DebriefCard(ask: cells, targets: item.targets) }
                }
            }
        case .unplanned(let fact):
            ThreadStep(kicker: ["Fait", fact.startedAt.flatMap(Numbers.clock), "hors programme"].compactMap { $0 }.joined(separator: " · "),
                       title: Notebook.kindName(fact.kind), family: Family(kind: fact.kind)) {
                FactTiles(fact: Notebook.Fact(unplanned: fact), targets: Notebook.Targets.none)
                DetailLink(link: SessionLink(id: fact.observationId, title: Notebook.kindName(fact.kind)))
            }
        }
    }

    @ViewBuilder private func pendingStep(_ item: Notebook.SessionItem, index: Int) -> some View {
        let now = today && index == 0 && wakes.isEmpty && daily == nil && loose.isEmpty
        // Today's strength session ready to open is a card that carries its own title.
        let launcher: Notebook.StrengthReady? = if item.isStrength, today, item.status != "held",
                                                   case .ready(let ready)? = strength { ready } else { nil }
        let started = launcher.flatMap { StrengthLauncher.resume(of: $0, in: session) }
        let role = !today ? nil : started != nil ? "En cours" : now ? "À faire" : index > 0 ? "Puis" : "Ensuite"
        let time = started == nil ? item.startTime.flatMap(Numbers.clock) : nil
        let kicker = [role, time, item.location].compactMap { $0 }.joined(separator: " · ")
        ThreadStep(kicker: kicker, now: now, title: item.id == hero?.id || launcher != nil ? nil : item.title,
                   family: item.family, small: item.isStrength || !today) {
            if item.status == "held" {
                Text("Une question t’attend dans Messenger avant cette séance.").textRole(.secondary).foregroundStyle(Palette.muted)
            } else if let launcher {
                StrengthLauncher(ready: launcher, title: item.title, note: item.note)
            } else if item.isStrength {
                if let note = item.note { Text(note).textRole(.secondary).foregroundStyle(Palette.muted) }
            } else {
                // The hero already shows the session's limits: its step keeps the rest.
                if today, item.id != hero?.id { TargetCursors(targets: item.targets) }
                if let note = item.note, today { Text(note).textRole(.body) }
                SessionWeather(item: item, size: today ? .full : .line)
            }
        }
    }
}

/// One step of a thread: its role, its title with its family badge, then its body.
struct ThreadStep<Body: View>: View {
    let kicker: String
    var now = false
    let title: String?
    /// Nil for a step that is no session, such as the morning's questions.
    let family: Family?
    var small = false
    @ViewBuilder var content: Body

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if !kicker.isEmpty {
                Text(kicker)
                    .font(TextRole.caption.font.weight(.semibold))
                    .foregroundStyle(now ? Palette.accent : Palette.muted)
            }
            if let title {
                HStack(spacing: Space.xs) {
                    if let family { FamilyBadge(family: family, small: small) }
                    Text(title).font(small ? TextRole.body.font.weight(.semibold) : TextRole.heading.font)
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension ThreadStep where Body == EmptyView {
    init(kicker: String, now: Bool = false, title: String?, family: Family?, small: Bool = false) {
        self.init(kicker: kicker, now: now, title: title, family: family, small: small) { EmptyView() }
    }
}

/// The day's strength exercises, folded under their count.
struct ExerciseList: View {
    let exercises: [(name: String, dose: String)]

    init(session: Notebook.StrengthSession) {
        exercises = session.exercises.map { ($0.name, $0.dose.text) }
    }

    var body: some View {
        if !exercises.isEmpty {
            DisclosureGroup("\(exercises.count) exercice\(exercises.count > 1 ? "s" : "")") {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(exercises.enumerated()), id: \.offset) { _, exercise in
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text(exercise.name).textRole(.body)
                            if !exercise.dose.isEmpty {
                                Text(exercise.dose).textRole(.secondary).foregroundStyle(Palette.muted)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, Space.s)
                    }
                }
            }
            .tint(Palette.ink)
        }
    }

    /// The slug and name of each exercise of a prescription, to name a report's lines.
    static func slugsAndNames(_ session: [String: JSONValue]) -> [(String, String)] {
        guard case .array(let list)? = session["exercises"] else { return [] }
        return list.compactMap { item in
            guard case .object(let exercise) = item, case .string(let slug)? = exercise["slug"] else { return nil }
            if case .string(let name)? = exercise["name"] { return (slug, name) }
            return nil
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

/// The way into a done run's detail, under its step.
struct DetailLink: View {
    let link: SessionLink

    var body: some View {
        NavigationLink(value: link) {
            HStack {
                Text("Détail de la séance").textRole(.secondary).fontWeight(.semibold)
                Spacer(minLength: Space.xs)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }
}
