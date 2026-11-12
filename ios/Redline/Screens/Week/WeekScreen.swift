import Charts
import RedlineKit
import SwiftUI

/// A week opened from another screen, by its ISO label.
struct WeekLink: Hashable {
    let label: String
}

/// Week: the week judged so far, and the rest prescribed. The tab always shows the
/// current week; another week opens on paper above it.
struct WeekScreen: View {
    let session: NotebookSession
    var label: String = Day(.now).isoWeek
    var root = true

    var body: some View {
        NotebookScreen(title: root ? NotebookTab.week.title : "Semaine \(Int(label.suffix(2)) ?? 0)",
                       session: session, model: ScreenModel(.week(label)), onTrack: root) { week in
            WeekHero(week: week)
        } content: { week in
            WeekContent(week: week, session: session, root: root)
        }
        .navigationDestination(for: WeekLink.self) { link in
            WeekScreen(session: session, label: link.label, root: false)
        }
        .navigationDestination(for: AllWeeks.self) { _ in
            AllWeeksScreen(session: session)
        }
    }
}

extension Notebook.WeekView {
    /// A week to come has nothing done: its figure is the target, not a zero.
    var upcoming: Bool { week > current }
    var open: Bool { week >= current }
}

/// « 5 – 11 octobre », or « 29 sept. – 5 octobre » across two months.
func weekSpan(monday text: String) -> String {
    guard let monday = Day(text) else { return text }
    let sunday = monday.adding(6)
    let french = Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR"))
    if monday.month == sunday.month {
        return "\(monday.day) – \(sunday.date.formatted(french.day().month(.wide)))"
    }
    return "\(monday.date.formatted(french.day().month(.abbreviated))) – \(sunday.date.formatted(french.day().month(.wide)))"
}

/// The head: done in full against planned in a lighter weight, what remains, then the
/// seven days. The figures sit on one line while they fit, one under the other otherwise.
private struct WeekHero: View {
    let week: Notebook.WeekView

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Kicker(text: "Semaine \(Int(week.week.suffix(2)) ?? 0) · \(weekSpan(monday: week.monday))")
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .lastTextBaseline, spacing: 10) { figures }
                VStack(alignment: .leading, spacing: 0) { figures }
            }
            Text(summary).textRole(.secondary).opacity(0.9)
            if !week.strip.isEmpty {
                WeekBars(days: week.strip, height: 100, abbreviations: true).padding(.top, Space.s)
            }
        }
    }

    @ViewBuilder private var figures: some View {
        let totals = week.totals
        if week.upcoming {
            HeroFigure(value: totals.kmTarget.map { Numbers.text($0) } ?? "—", unit: "km prévus", state: .planned)
        } else {
            HeroFigure(value: Numbers.text(totals.kmDone), unit: totals.kmTarget == nil ? "km" : nil, state: .done)
            if let target = totals.kmTarget {
                HeroFigure(value: Numbers.text(target), lead: "sur", unit: "km", state: .planned)
            }
        }
    }

    private var summary: AttributedString {
        let totals = week.totals
        var head: AttributedString
        if week.upcoming {
            head = AttributedString("\(totals.runsPlanned) sortie\(totals.runsPlanned > 1 ? "s" : "")")
            head.font = .subheadline.bold()
            if totals.strengthPlanned > 0 {
                head += AttributedString(" · \(totals.strengthPlanned) renfo\(totals.strengthPlanned > 1 ? "s" : "")")
            }
            return head
        }
        head = AttributedString("\(totals.runsDone) sortie\(totals.runsDone > 1 ? "s" : "")"
                                + (totals.runsPlanned > 0 ? " sur \(totals.runsPlanned)" : ""))
        head.font = .subheadline.bold()
        var rest: [String] = []
        if week.open, let target = totals.kmTarget, target > totals.kmDone {
            rest.append("reste ~\(Numbers.text(target - totals.kmDone, digits: 0)) km")
        } else if totals.dplus > 0 {
            rest.append("\(Numbers.text(totals.dplus, digits: 0)) m D+")
        }
        for peak in totals.painPeaks {
            guard let value = peak.peak else { continue }
            rest.append("douleur au pic " + (totals.painPeaks.count > 1 ? "\(peak.label.lowercased()) " : "") + Numbers.text(value))
        }
        if !rest.isEmpty { head += AttributedString(" · " + rest.joined(separator: " · ")) }
        return head
    }
}

private struct WeekContent: View {
    let week: Notebook.WeekView
    let session: NotebookSession
    let root: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !week.plan.message.isEmpty {
                Label(week.plan.message, systemImage: "hourglass")
                    .textRole(.secondary)
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
                    .padding(.bottom, Space.m)
            }
            if week.sessions.isEmpty && week.plan.status == "invalid" {
                Text("Aucun programme enregistré pour cette semaine. Le registre reste ci-dessous.")
                    .textRole(.secondary).foregroundStyle(Palette.muted).padding(.bottom, Space.s)
            }
            if let monday = Day(week.monday) {
                ForEach(0..<7, id: \.self) { offset in
                    let date = monday.adding(offset).text
                    WeekDayRow(date: date, items: week.sessions.filter { $0.date == date },
                               extra: week.extra.filter { $0.date == date }, today: Day(.now).text)
                }
            }
            reorganize
            if root, let next = week.next {
                SectionTitle(text: "Semaine prochaine")
                NavigationLink(value: WeekLink(label: next.week)) {
                    WeekLine(title: "Semaine \(Int(next.week.suffix(2)) ?? 0)",
                             detail: "\(weekSpan(monday: next.monday)) · \(next.runsPlanned) sorties",
                             figure: next.kmTarget.map { "~\(Numbers.text($0)) km" } ?? "")
                }
                .buttonStyle(.plain)
            }
            if week.review != nil || week.open || !week.cards.isEmpty {
                SectionTitle(text: "Cartes de la semaine")
                // The review takes its place among the cards: a grey line until it is
                // written, the same line made touchable once it arrives.
                if let review = week.review {
                    let title = "Bilan W\(week.week.suffix(2))"
                    NavigationLink(value: EntryLink(id: review.id, title: title)) {
                        EntryRow(title: title, date: review.occurredOn)
                    }
                    .buttonStyle(.plain)
                } else if week.open {
                    EntryRow(title: "Bilan de la semaine", date: nil, pending: true)
                }
                ForEach(week.cards, id: \.id) { card in
                    NavigationLink(value: EntryLink(id: card.id, title: card.title)) {
                        EntryRow(title: card.title, date: card.occurredOn)
                    }
                    .buttonStyle(.plain)
                }
            }
            if root { PastWeeks(session: session) }
        }
        .padding(.bottom, Space.l)
    }
}

extension WeekContent {
    /// Moving sessions while the week is open and something is left to do; on the current
    /// week before the next is written, saying how the next one's days present themselves.
    @ViewBuilder var reorganize: some View {
        let today = Day(.now).text
        if week.open, week.plan.status == "valid",
           week.sessions.contains(where: { !["done", "cancelled"].contains($0.status) && $0.kind != "rest" && $0.date >= today }) {
            NavigationLink(value: ReorganizeLink(week: week.week)) {
                EntryLine(title: "Réorganiser la semaine", detail: "déplacer des séances selon tes jours")
            }
            .buttonStyle(.plain)
            .padding(.top, Space.s)
        }
        if root, week.next == nil, week.week == week.current, let monday = Day(week.monday) {
            let coming = monday.adding(7).isoWeek
            NavigationLink(value: ReorganizeLink(week: coming)) {
                EntryLine(title: "Préparer la semaine \(Int(coming.suffix(2)) ?? 0)", detail: "dire au coach comment se présentent tes jours")
            }
            .buttonStyle(.plain)
            .padding(.top, Space.s)
        }
    }
}

/// A hero figure on the track, with the words around it.
private struct HeroFigure: View {
    let value: String
    var lead: String?
    var unit: String?
    let state: FigureState

    var body: some View {
        HStack(alignment: .lastTextBaseline, spacing: 10) {
            if let lead { Text(lead).textRole(.secondary).opacity(0.82) }
            Text(value).figure(.hero, state).opacity(state == .planned ? 0.72 : 1)
            if let unit { Text(unit).textRole(.secondary).opacity(0.82) }
        }
    }
}

/// A day of the week on one line: its session and one figure, the fact in ink, the plan
/// in grey, an overshoot as red labels. What else happened that day sits under it; the
/// rest unfolds on touch.
private struct WeekDayRow: View {
    let date: String
    let items: [Notebook.SessionItem]
    let extra: [Notebook.UnplannedFact]
    let today: String
    @State private var open = false
    @Environment(\.dynamicTypeSize) private var typeSize
    /// Under the line, what is said lines up with the session's title, past the day's column.
    @ScaledMetric(relativeTo: .title3) private var indent: CGFloat = 52

    var body: some View {
        let row = describe()
        let inset = typeSize.isAccessibilitySize ? 0 : indent
        VStack(alignment: .leading, spacing: Space.xs) {
            // A done run opens its detail; a row with nothing to unfold is no button (a
            // disabled one would grey it).
            if let link = row.link {
                NavigationLink(value: link) { line(row) }.buttonStyle(.plain)
            } else if row.detail.isEmpty {
                line(row)
            } else {
                Button { withAnimation(.snappy) { open.toggle() } } label: { line(row) }
                    .buttonStyle(.plain)
                    .accessibilityHint(open ? "Replier" : "Déplier")
            }
            ForEach(row.beside + (open ? row.detail : []), id: \.self) { line in
                Text(line).textRole(.caption).foregroundStyle(Palette.muted).padding(.leading, inset)
            }
            ForEach(reports, id: \.id) { report in
                NavigationLink(value: report) {
                    HStack(spacing: 4) {
                        Text("Compte rendu du renfo")
                        Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                    }
                    .font(TextRole.caption.font.weight(.semibold))
                    .foregroundStyle(Palette.ink)
                }
                .buttonStyle(.plain)
                .padding(.leading, inset)
            }
        }
        .padding(.vertical, Space.s)
    }

    /// The strength sessions of the day with a report card.
    private var reports: [EntryLink] {
        items.filter { $0.isStrength && $0.isDone }.compactMap { item in
            item.entryId.map { EntryLink(id: $0, title: "Compte rendu du renfo") }
        }
    }

    /// The day, the session and its figure on one line; at the accessibility sizes the day
    /// goes above, on a line of its own, and the figure under the title.
    @ViewBuilder private func line(_ row: Row) -> some View {
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    DayNumber(date: date, today: date == today, inline: true)
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            heading(row)
                            value(row)
                        }
                        Spacer(minLength: 0)
                        chevron(row)
                    }
                }
            } else {
                HStack(alignment: .center, spacing: Space.s) {
                    DayNumber(date: date, today: date == today)
                    FamilyBadges(families: row.families, small: true)
                    heading(row)
                    Spacer(minLength: Space.xs)
                    value(row)
                    chevron(row)
                }
            }
        }
        .contentShape(.rect)
    }

    private func heading(_ row: Row) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(row.title).textRole(.secondary).fontWeight(.semibold)
                .foregroundStyle(row.style == .rest ? Palette.muted : Palette.ink)
            if !row.tags.isEmpty {
                // A limit crossed: the gap to it, in the red line, after a symbol that says so.
                Label(row.tags.joined(separator: " · "), systemImage: "arrow.up.right")
                    .font(TextRole.caption.font.weight(.semibold))
                    .foregroundStyle(Palette.accent)
            }
            if let alert = row.alert { Text(alert).textRole(.caption).foregroundStyle(Palette.muted) }
        }
    }

    @ViewBuilder private func value(_ row: Row) -> some View {
        if row.style == .done {
            Text(row.value).figure(.inline, .done).foregroundStyle(Palette.ink)
        } else {
            Text(row.value).textRole(.secondary).foregroundStyle(Palette.muted)
                .strikethrough(row.style == .skipped)
        }
    }

    @ViewBuilder private func chevron(_ row: Row) -> some View {
        if !row.detail.isEmpty || row.link != nil {
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
                .rotationEffect(.degrees(open ? 90 : 0))
        }
    }

    private enum Style { case done, plan, rest, skipped }

    private typealias Row = (title: String, value: String, style: Style, families: [Family], tags: [String],
                             alert: String?, beside: [String], detail: [String], link: SessionLink?)

    private func describe() -> Row {
        let live = items.filter { $0.kind != "rest" }
        let runs = live.filter { !$0.isStrength }, strengths = live.filter(\.isStrength)
        let past = date < today
        var beside: [String] = [], detail: [String] = []
        guard let main = runs.first ?? strengths.first else {
            guard let first = extra.first else {
                return ("Repos", "", .rest, [.rest], [], nil, [], [], nil)
            }
            let value = first.km.map { "\(Numbers.text($0)) km" } ?? first.durationMin.map { "\($0) min" } ?? ""
            return ("\(Notebook.kindName(first.kind)) · hors programme", value, .done, [Family(kind: first.kind)], [], nil,
                    extra.dropFirst().map(Self.unplannedLine), [],
                    SessionLink(id: first.observationId, title: Notebook.kindName(first.kind)))
        }
        let title = ([main.title.split(separator: ",").first.map(String.init) ?? main.title]
                     + (!main.isStrength && !strengths.isEmpty ? ["renfo"] : [])).joined(separator: " + ")
        let families = [main.family] + (!main.isStrength && !strengths.isEmpty ? [Family.strength] : [])
        var value = "", style = Style.plan, tags: [String] = [], alert: String?
        var observed = false
        var link: SessionLink?
        if main.isDone {
            style = .done
            if let fact = main.runFact {
                value = fact.km.map { "\(Numbers.text($0)) km" } ?? fact.durationMin.map { "\($0) min" } ?? "fait"
                tags = Self.crossing(fact, main.targets)
                observed = true
                link = SessionLink(id: fact.observationId, title: main.title.shortTitle)
            } else {
                value = main.strengthFact?.durationMin.map { "\($0) min" } ?? "fait"
            }
        } else if main.status == "cancelled" {
            value = "sauté"
            style = .skipped
            if let note = main.note { detail.append(note) }
        } else if past {
            value = "pas de trace"
        } else {
            value = main.plannedValue
            if main.status == "held" { alert = "en attente d’une réponse dans Messenger" }
        }
        if let change = main.change { observed ? beside.append(change) : detail.append(change) }
        for strength in strengths where strength.isDone && strength.id != main.id {
            let line = "Renfo · " + (strength.strengthFact?.durationMin.map { "\($0) min" } ?? "fait")
            observed ? beside.append(line) : detail.append(line)
        }
        if style == .plan, !past, let note = main.note { detail.append(note) }
        for fact in extra {
            observed ? beside.append(Self.unplannedLine(fact)) : detail.append(Self.unplannedLine(fact))
        }
        return (title, value, style, families, tags, alert, beside, detail, link)
    }

    private static func unplannedLine(_ fact: Notebook.UnplannedFact) -> String {
        "Hors programme · \(Notebook.kindName(fact.kind))" + (fact.km.map { " · \(Numbers.text($0)) km" } ?? "")
    }

    /// What overshot, as the gap to the limit: labels, not a sentence.
    private static func crossing(_ fact: Notebook.Fact, _ targets: Notebook.Targets) -> [String] {
        let over = Set(fact.over)
        var tags: [String] = []
        if over.contains("hr_avg"), let heart = fact.hrAvg, let cap = targets.hrCapBpm {
            tags.append("FC +\(Numbers.text(heart - Double(cap), digits: 0))")
        }
        if over.contains("rpe"), let rpe = fact.rpe, let cap = targets.rpeMax { tags.append("RPE +\(Numbers.text(rpe - Double(cap)))") }
        if over.contains("km"), let km = fact.km, let cap = targets.distanceCapKm { tags.append("+\(Numbers.text(km - cap)) km") }
        if over.contains("duration_min"), let minutes = fact.durationMin, let cap = targets.durationCapMin {
            tags.append("+\(minutes - cap) min")
        }
        return tags
    }
}

/// A day's number and its weekday: a column whose width follows the text size, or one
/// line (« lun. 5 ») where the day heads its row.
struct DayNumber: View {
    let date: String
    var today = false
    var inline = false
    @ScaledMetric(relativeTo: .title3) private var width: CGFloat = 36

    var body: some View {
        let day = Day(date)
        let number = Text(day.map { "\($0.day)" } ?? "").figure(.medium).foregroundStyle(today ? Palette.accent : Palette.ink)
        let weekday = Text(day?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR"))
            .weekday(.abbreviated)) ?? "")
            .font(.caption2).foregroundStyle(today ? Palette.accent : Palette.muted)
        if inline {
            HStack(alignment: .firstTextBaseline, spacing: Space.xxs) { weekday; number }
                .accessibilityElement(children: .combine)
        } else {
            VStack(spacing: 0) { number; weekday.lineLimit(1).minimumScaleFactor(0.8) }
                .frame(width: width)
                .accessibilityElement(children: .combine)
        }
    }
}

/// A week on one line: its name and dates, its figure, a chevron.
struct WeekLine: View {
    let title: String
    let detail: String
    let figure: String
    var progress: Double?

    var body: some View {
        HStack(spacing: Space.s) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Space.s) {
                    words
                    Spacer(minLength: Space.xs)
                    measure(alignment: .trailing)
                }
                VStack(alignment: .leading, spacing: Space.xxs) {
                    words
                    measure(alignment: .leading)
                }
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, Space.s)
        .contentShape(.rect)
    }

    private var words: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).textRole(.secondary).fontWeight(.semibold)
            Text(detail).textRole(.caption).foregroundStyle(Palette.muted)
        }
    }

    private func measure(alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: Space.xxs) {
            if let progress {
                Capsule().fill(Palette.ghost).frame(width: 64, height: 5)
                    .overlay(alignment: .leading) {
                        Capsule().fill(Palette.data).frame(width: 64 * min(progress, 1), height: 5)
                    }
            }
            Text(figure).textRole(.caption).monospacedDigit().foregroundStyle(Palette.muted)
        }
    }
}

