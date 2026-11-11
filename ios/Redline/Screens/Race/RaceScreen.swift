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
            VStack(alignment: .leading, spacing: 8) {
                Kicker(text: [race.name, Day(race.date)?.spelled(), race.startTime.flatMap(Numbers.clock).map { "départ \($0)" }]
                    .compactMap { $0 }.joined(separator: " · "))
                HStack(alignment: .lastTextBaseline, spacing: 10) {
                    Text("J-\(race.days)").font(.figure(80))
                    if let target = race.target {
                        Text("cible \(target)").font(.subheadline.weight(.semibold)).opacity(0.9)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Dans \(race.days) jours" + (race.target.map { ", cible \($0)" } ?? ""))
                if let plan = view.plan {
                    RaceProfile(plan: plan).padding(.top, 8)
                } else if let facts = Self.facts(race) {
                    Text(facts).font(.subheadline).opacity(0.9)
                }
            }
        } else {
            Kicker(text: "Aucune course datée")
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
                Text("Aucune course datée.").font(.subheadline).foregroundStyle(Palette.muted)
            } else if let plan = view.plan {
                RaceSummary(plan: plan)
            }
            if !view.milestones.isEmpty { Milestones(items: view.milestones) }
            if !view.capabilities.isEmpty { Capabilities(items: view.capabilities) }
        }
        .padding(.bottom, 24)
    }
}

/// The finish at the pace chosen with the coach, never at one being explored, and the
/// way to the plan.
private struct RaceSummary: View {
    let plan: Notebook.RacePlan

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle(text: "Plan de course", meta: plan.paceSPerKm.map { "à \(Numbers.pace($0))/km à plat éq." })
            if let pace = plan.paceSPerKm, let finish = plan.passages(pace: pace).last {
                // The time aimed at is a target, not a limit: an ink mark on its band.
                let spread = finish.likely.upperBound - finish.likely.lowerBound
                RedLineCursor(label: "Temps de course, arrêts compris", value: finish.hours,
                              scale: (finish.likely.lowerBound - spread * 0.4)...(finish.likely.upperBound + spread * 0.4),
                              band: finish.likely, target: finish.hours, format: { Numbers.hours($0) }, planned: true)
                Text(Self.finishLine(plan: plan, finish: finish))
                    .font(.footnote).foregroundStyle(Palette.muted)
            } else {
                Text("Allure à choisir avec le coach : les passages s’afficheront ensuite.")
                    .font(.footnote).foregroundStyle(Palette.muted)
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
            Text(text).font(.subheadline.weight(.semibold))
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, 10)
        .contentShape(.rect)
    }
}

/// The milestones as a thread: the next one to meet in ink, the race in red, an overdue
/// deadline said so.
private struct Milestones: View {
    let items: [Notebook.Milestone]

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
            .padding(.top, 14)
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
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(line.race ? Palette.tartan : now ? Palette.ink : Palette.bg)
                .overlay(Circle().strokeBorder(line.race ? Palette.tartan : now ? Palette.ink : Palette.faint, lineWidth: 2))
                .frame(width: 12, height: 12)
                .padding(.top, 3)
                .frame(maxHeight: .infinity, alignment: .top)
                .background(alignment: .top) {
                    if !last {
                        Rectangle().fill(Palette.grid).frame(width: 2).padding(.top, 15).padding(.bottom, -3)
                    }
                }
            VStack(alignment: .leading, spacing: 2) {
                Text(line.when).font(.caption).foregroundStyle(line.overdue ? Palette.over : Palette.muted)
                Text(line.what).font(.subheadline.weight(.medium))
            }
            .padding(.bottom, 14)
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
private struct Capabilities: View {
    let items: [Notebook.Capability]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Capacités course")
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                HStack(spacing: 10) {
                    HStack(spacing: 8) {
                        FamilyBadge(family: Family(capability: item.icon), small: true, symbol: Family.symbol(capability: item.icon))
                        Text(item.label).font(.subheadline).lineLimit(2)
                    }
                    .frame(width: 128, alignment: .leading)
                    HStack(spacing: 3) {
                        ForEach(0..<max(item.scale, 1), id: \.self) { index in
                            RoundedRectangle(cornerRadius: 2).fill(index < item.score ? Palette.data : Palette.ghost).frame(height: 7)
                        }
                    }
                    Text("\(item.score)").font(.figure(16)).frame(width: 30, alignment: .trailing)
                }
                .padding(.vertical, 7)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(item.label) : \(item.score) sur \(item.scale)")
            }
            if let latest = items.map(\.evaluatedOn).max() {
                Text("Évaluées le \(shortDate(latest)), au bilan.").font(.footnote).foregroundStyle(Palette.muted).padding(.top, 6)
            }
        }
    }
}

extension Family {
    /// The family whose tint a capability's icon takes.
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
