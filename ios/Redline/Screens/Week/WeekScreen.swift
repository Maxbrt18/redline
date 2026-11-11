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

/// The head: done solid against planned in outline, what remains, then the seven days.
private struct WeekHero: View {
    let week: Notebook.WeekView
    @Environment(\.onTrack) private var onTrack

    var body: some View {
        let totals = week.totals
        VStack(alignment: .leading, spacing: 8) {
            Kicker(text: "Semaine \(Int(week.week.suffix(2)) ?? 0) · \(weekSpan(monday: week.monday))")
            HStack(alignment: .lastTextBaseline, spacing: 10) {
                if week.upcoming {
                    OutlinedFigure(text: totals.kmTarget.map { Numbers.text($0) } ?? "—", size: 80, lineWidth: 2.2,
                                   color: onTrack ? Palette.onTrack : Palette.ink)
                    Text("km prévus").font(.subheadline).opacity(0.82)
                } else {
                    Text(Numbers.text(totals.kmDone)).font(.figure(80))
                    if let target = totals.kmTarget {
                        Text("sur").font(.subheadline).opacity(0.82)
                        OutlinedFigure(text: Numbers.text(target), size: 80, lineWidth: 2.2,
                                       color: onTrack ? Palette.onTrack : Palette.ink)
                    }
                    Text("km").font(.subheadline).opacity(0.82)
                }
            }
            Text(summary).font(.subheadline).opacity(0.9)
            if !week.strip.isEmpty {
                WeekBars(days: week.strip, height: 100, abbreviations: true).padding(.top, 10)
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
                    .font(.subheadline)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.panel, in: .rect(cornerRadius: 12))
                    .padding(.bottom, 16)
            }
            if week.sessions.isEmpty && week.plan.status == "invalid" {
                Text("Aucun programme enregistré pour cette semaine. Le registre reste ci-dessous.")
                    .font(.subheadline).foregroundStyle(Palette.muted).padding(.bottom, 12)
            }
            if let monday = Day(week.monday) {
                ForEach(0..<7, id: \.self) { offset in
                    let date = monday.adding(offset).text
                    WeekDayRow(date: date, items: week.sessions.filter { $0.date == date },
                               extra: week.extra.filter { $0.date == date }, today: Day(.now).text)
                }
            }
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
                if let review = week.review {
                    CardLine(title: "Bilan W\(week.week.suffix(2))", date: review.occurredOn)
                } else if week.open {
                    CardLine(title: "Bilan de la semaine", date: nil, pending: true)
                }
                ForEach(week.cards, id: \.id) { card in
                    CardLine(title: card.title, date: card.occurredOn)
                }
            }
            if root { PastWeeks(session: session) }
        }
        .padding(.bottom, 24)
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

    var body: some View {
        let row = describe()
        VStack(alignment: .leading, spacing: 6) {
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
                Text(line).font(.footnote).foregroundStyle(Palette.muted).padding(.leading, 52)
            }
        }
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
    }


    private func line(_ row: Row) -> some View {
        HStack(alignment: .center, spacing: 12) {
            DayNumber(date: date, today: date == today)
            FamilyBadges(families: row.families, small: true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(row.title).font(.subheadline.weight(.semibold))
                        .foregroundStyle(row.style == .rest ? Palette.muted : Palette.ink)
                    ForEach(row.tags, id: \.self) { tag in
                        Text(tag).font(.caption.weight(.semibold)).foregroundStyle(Palette.over)
                    }
                }
                if let alert = row.alert { Text(alert).font(.caption).foregroundStyle(Palette.muted) }
            }
            Spacer(minLength: 6)
            Text(row.value)
                .font(row.style == .done ? .figure(17, weight: .semibold) : .subheadline)
                .foregroundStyle(row.style == .done ? Palette.ink : Palette.faint)
                .strikethrough(row.style == .skipped)
            if !row.detail.isEmpty || row.link != nil {
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
                    .rotationEffect(.degrees(open ? 90 : 0))
            }
        }
        .contentShape(.rect)
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

/// A day's number and its weekday, as a column.
struct DayNumber: View {
    let date: String
    var today = false

    var body: some View {
        let day = Day(date)
        VStack(spacing: 0) {
            Text(day.map { "\($0.day)" } ?? "").font(.figure(20)).foregroundStyle(today ? Palette.red : Palette.ink)
            Text(day?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.abbreviated)) ?? "")
                .font(.caption2).foregroundStyle(today ? Palette.red : Palette.muted)
        }
        .frame(width: 36)
    }
}

/// A week on one line: its name and dates, its figure, a chevron.
struct WeekLine: View {
    let title: String
    let detail: String
    let figure: String
    var progress: Double?

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(Palette.muted)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                if let progress {
                    Capsule().fill(Palette.ghost).frame(width: 64, height: 5)
                        .overlay(alignment: .leading) {
                            Capsule().fill(Palette.data).frame(width: 64 * min(progress, 1), height: 5)
                        }
                }
                Text(figure).font(.caption).monospacedDigit().foregroundStyle(Palette.muted)
            }
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, 12)
        .contentShape(.rect)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
    }
}

/// A card of the week: its title and day. It opens with the archives (step 1.7).
private struct CardLine: View {
    let title: String
    let date: String?
    var pending = false

    var body: some View {
        HStack {
            Text(title).font(.subheadline).foregroundStyle(pending ? Palette.faint : Palette.ink)
            Spacer()
            Text(pending ? "dimanche" : date.flatMap(Day.init).map { String(format: "%02d/%02d", $0.day, $0.month) } ?? "")
                .font(.caption).foregroundStyle(Palette.muted)
        }
        .padding(.vertical, 12)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
    }
}
