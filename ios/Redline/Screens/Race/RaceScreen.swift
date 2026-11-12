import RedlineKit
import SwiftUI

/// The race plan, opened from the Race tab.
struct RacePlanLink: Hashable {}

/// Race: what the training aims at. The days left and the course on the track; then the
/// finish at the pace chosen with the coach, the milestones, the race capabilities.
struct RaceScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: NotebookTab.race.title, session: session, model: ScreenModel(.race)) { view in
            RaceHero(view: view)
        } content: { view in
            RaceContent(view: view)
        }
        .navigationDestination(for: RacePlanLink.self) { _ in
            RacePlanScreen(session: session)
        }
    }
}

private struct RaceHero: View {
    let view: Notebook.RaceView

    var body: some View {
        if let race = view.race {
            VStack(alignment: .leading, spacing: Space.xs) {
                Kicker(text: [race.name, Day(race.date)?.spelled(), race.startTime.flatMap(Numbers.clock).map { "départ \($0)" }]
                    .compactMap { $0 }.joined(separator: " · "))
                // The days left on one line with the target while both fit, the target under
                // them otherwise; the figure never breaks.
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .lastTextBaseline, spacing: Space.s) { countdown(race) }
                    VStack(alignment: .leading, spacing: 0) { countdown(race) }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Dans \(race.days) jours" + (race.target.map { ", cible \($0)" } ?? ""))
                if let plan = view.plan {
                    RaceProfile(plan: plan).padding(.top, Space.xs)
                } else if let facts = Self.facts(race) {
                    Text(facts).textRole(.secondary).opacity(0.9)
                }
            }
        } else {
            Kicker(text: "Aucune course datée")
        }
    }

    @ViewBuilder private func countdown(_ race: Notebook.Race) -> some View {
        Text("J-\(race.days)").figure(.hero)
        if let target = race.target {
            Text("cible \(target)").textRole(.secondary).fontWeight(.semibold).opacity(0.9)
        }
    }

    /// Without a measured route, what the race declares of itself.
    static func facts(_ race: Notebook.Race) -> String? {
        let approximate = race.elevationApproximate == true ? "≈ " : ""
        let parts = [race.distanceKm.map { "\(Numbers.text($0)) km" },
                     race.elevationGainM.map { "D+ \(approximate)\(Int($0)) m" }].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

private struct RaceContent: View {
    let view: Notebook.RaceView

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if view.race == nil {
                Text("Aucune course datée.").textRole(.secondary).foregroundStyle(Palette.muted)
            } else if let plan = view.plan {
                RaceSummary(plan: plan)
            }
            if !view.milestones.isEmpty { Milestones(items: view.milestones) }
            if !view.capabilities.isEmpty { Capabilities(items: view.capabilities) }
            RaceArchives(items: view.archives)
        }
        .padding(.bottom, Space.l)
    }
}

/// The finish at the pace chosen with the coach, never at one being explored, and the
/// way to the plan.
private struct RaceSummary: View {
    let plan: Notebook.RacePlan

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionTitle(text: "Plan de course", meta: plan.paceSPerKm.map { "à \(Numbers.pace($0))/km à plat éq." })
            if let pace = plan.paceSPerKm, let finish = plan.passages(pace: pace).last {
                // The time aimed at is a target, not a limit: an ink mark on its band.
                let spread = finish.likely.upperBound - finish.likely.lowerBound
                RedLineCursor(label: "Temps de course, arrêts compris", value: finish.hours,
                              scale: (finish.likely.lowerBound - spread * 0.4)...(finish.likely.upperBound + spread * 0.4),
                              band: finish.likely, target: finish.hours, format: { Numbers.hours($0) }, planned: true)
                Text(Self.finishLine(plan: plan, finish: finish))
                    .textRole(.caption).foregroundStyle(Palette.muted)
            } else {
                Text("Allure à choisir avec le coach : les passages s’afficheront ensuite.")
                    .textRole(.caption).foregroundStyle(Palette.muted)
            }
            NavigationLink(value: RacePlanLink()) {
                ChevronRow(text: plan.paceSPerKm == nil ? "Profil et ravitos" : "Passages, ravitos et glucides")
            }
            .buttonStyle(.plain)
        }
    }

    static func finishLine(plan: Notebook.RacePlan, finish: Notebook.RacePlan.TimedPassage) -> String {
        let band = "le plus souvent entre \(Numbers.hours(finish.likely.lowerBound)) et \(Numbers.hours(finish.likely.upperBound))"
        guard let clock = plan.wallClock(after: finish.hours).flatMap(Numbers.clock) else { return band.capitalizedFirst + "." }
        return "Arrivée vers \(clock), \(band) de course."
    }
}

/// A row that opens another screen: its words, then a chevron.
struct ChevronRow: View {
    let text: String

    var body: some View {
        HStack {
            Text(text).textRole(.secondary).fontWeight(.semibold)
            Spacer(minLength: Space.xs)
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, Space.s)
        .contentShape(.rect)
    }
}

/// The milestones as a thread: the next one to meet in ink, the race on the red line, an
/// overdue deadline said in words and in the red line.
private struct Milestones: View {
    let items: [Notebook.Milestone]
    /// The dot sits on the first line of its words and grows with them.
    @ScaledMetric(relativeTo: .footnote) private var dot: CGFloat = 12
    @ScaledMetric(relativeTo: .footnote) private var lead: CGFloat = 3

    var body: some View {
        let next = items.firstIndex { item in
            switch item {
            case .deadline(let deadline): !deadline.overdue
            case .session: true
            default: false
            }
        }
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Prochains jalons")
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    if let line = Self.line(item) {
                        step(line, now: index == next, last: index == items.count - 1)
                    }
                }
            }
            .padding(.top, Space.s)
        }
    }

    private struct Line {
        let when: String
        let what: String
        let overdue: Bool
        let race: Bool
    }

    private static func line(_ item: Notebook.Milestone) -> Line? {
        switch item {
        case .deadline(let deadline):
            Line(when: deadline.overdue ? "en retard · \(shortDate(deadline.date))" : "avant le \(shortDate(deadline.date))",
                 what: deadline.label, overdue: deadline.overdue, race: false)
        case .session(let session):
            Line(when: shortDay(session.date), what: "\(session.label), \(Numbers.text(session.km)) km", overdue: false, race: false)
        case .race(let race):
            Line(when: [shortDay(race.date), race.startTime.flatMap(Numbers.clock)].compactMap { $0 }.joined(separator: " · "),
                 what: race.label, overdue: false, race: true)
        case .unknown:
            nil
        }
    }

    private func step(_ line: Line, now: Bool, last: Bool) -> some View {
        HStack(alignment: .top, spacing: Space.s) {
            Circle()
                .fill(line.race ? Palette.accent : now ? Palette.ink : Palette.bg)
                .overlay(Circle().strokeBorder(line.race ? Palette.accent : now ? Palette.ink : Palette.faint, lineWidth: 2))
                .frame(width: dot, height: dot)
                .padding(.top, lead)
                .frame(maxHeight: .infinity, alignment: .top)
                .background(alignment: .top) {
                    if !last {
                        Rectangle().fill(Palette.grid).frame(width: 2).padding(.top, dot + lead).padding(.bottom, -lead)
                    }
                }
            VStack(alignment: .leading, spacing: 2) {
                Text(line.when).textRole(.caption).foregroundStyle(line.overdue ? Palette.accent : Palette.muted)
                Text(line.what).textRole(.secondary).fontWeight(.medium)
            }
            .padding(.bottom, Space.s)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .combine)
    }
}

/// « jeu 26/11 »: a weekday and a date, for a thread of milestones.
func shortDay(_ text: String) -> String {
    guard let day = Day(text) else { return text }
    let weekday = day.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.abbreviated))
    return weekday.replacingOccurrences(of: ".", with: "") + " " + shortDate(text)
}

/// The race capabilities, scored at the review: one row each, its pips against its scale.
/// The labels share a column whose width follows the text size; at the accessibility sizes
/// the label goes above its pips.
private struct Capabilities: View {
    let items: [Notebook.Capability]
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .subheadline) private var labelWidth: CGFloat = 128
    @ScaledMetric(relativeTo: .body) private var scoreWidth: CGFloat = 24

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Capacités course")
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                Group {
                    if typeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: Space.xs) {
                            label(item)
                            HStack(spacing: Space.s) { pips(item); score(item) }
                        }
                    } else {
                        HStack(spacing: Space.s) {
                            label(item).frame(width: labelWidth, alignment: .leading)
                            pips(item)
                            score(item)
                        }
                    }
                }
                .padding(.vertical, Space.xs)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(item.label) : \(item.score) sur \(item.scale)")
            }
            if let latest = items.map(\.evaluatedOn).max() {
                Text("Évaluées le \(shortDate(latest)), au bilan.").textRole(.caption).foregroundStyle(Palette.muted)
                    .padding(.top, Space.xs)
            }
        }
    }

    private func label(_ item: Notebook.Capability) -> some View {
        HStack(spacing: Space.xs) {
            FamilyBadge(family: Family(capability: item.icon), small: true, symbol: Family.symbol(capability: item.icon))
            Text(item.label).textRole(.secondary)
        }
    }

    private func pips(_ item: Notebook.Capability) -> some View {
        HStack(spacing: 3) {
            ForEach(0..<max(item.scale, 1), id: \.self) { index in
                RoundedRectangle(cornerRadius: Radius.mark).fill(index < item.score ? Palette.data : Palette.ghost).frame(height: 7)
            }
        }
    }

    private func score(_ item: Notebook.Capability) -> some View {
        Text("\(item.score)").figure(.inline).frame(minWidth: scoreWidth, alignment: .trailing)
    }
}

extension Family {
    /// The family whose symbol a capability’s icon takes.
    init(capability icon: String) {
        self = switch icon {
        case "str": .strength
        case "rest": .rest
        case "pain": .pain
        case "race", "food", "night": .race
        case "other": .otherSport
        default: .run
        }
    }

    /// The glyph of a capability's icon.
    static func symbol(capability icon: String) -> String? {
        switch icon {
        case "week": "chart.bar"
        case "descent": "arrow.down.right"
        case "food": "fork.knife"
        case "night": "moon"
        case "mind": "brain.head.profile"
        case "long": Family.longRun.symbol
        default: nil
        }
    }
}

/// The latest reviews, then the way to every card.
private struct RaceArchives: View {
    let items: [Notebook.Entry]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Archives", meta: "bilans, monthly")
            ForEach(items, id: \.id) { entry in
                let label = Self.label(entry)
                NavigationLink(value: EntryLink(id: entry.id, title: label)) {
                    EntryRow(title: label, date: nil)
                }
                .buttonStyle(.plain)
            }
            NavigationLink(value: ArchivesLink()) {
                ChevronRow(text: "Toutes les archives")
            }
            .buttonStyle(.plain)
        }
    }

    /// « Monthly · <month> <year> », « Weekly · W<nn> », else the card’s own title.
    static func label(_ entry: Notebook.Entry) -> String {
        if entry.kind == "monthly", let month = Day(entry.period + "-01") {
            return "Monthly · " + month.date.formatted(
                Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).month(.wide).year())
        }
        if entry.kind == "weekly", !entry.period.isEmpty {
            return "Weekly · W" + entry.period.suffix(2)
        }
        return entry.title
    }
}
