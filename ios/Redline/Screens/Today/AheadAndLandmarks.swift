import RedlineKit
import SwiftUI

/// A section title on a rule, never a box; a few words may follow it on the right. The
/// space above it is the page's: sections are stacked `Space.section` apart, so a section
/// that opens a page sits right under its head.
struct SectionTitle: View {
    let text: String
    var meta: String?

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline) {
                title
                Spacer(minLength: Space.xs)
                if let meta { Text(meta).textRole(.caption).foregroundStyle(Palette.muted).multilineTextAlignment(.trailing) }
            }
            VStack(alignment: .leading, spacing: Space.xxs) {
                title
                if let meta { Text(meta).textRole(.caption).foregroundStyle(Palette.muted) }
            }
        }
        .padding(.bottom, Space.xs)
    }

    private var title: some View {
        Text(text).textRole(.heading).accessibilityAddTraits(.isHeader)
    }
}

/// What is coming: one line per day for the next three days, as the Week tab lists them.
/// At the accessibility sizes the day goes above, on a line of its own, and the figure
/// under the title.
struct AheadList: View {
    let items: [Notebook.SessionItem]
    /// The day whose line carries the weather: tomorrow.
    var tomorrow: String?
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let dates = items.map(\.date).reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }.prefix(3)
        if !dates.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                SectionTitle(text: "À venir")
                ForEach(Array(dates), id: \.self) { date in
                    row(date: date, sessions: items.filter { $0.date == date })
                }
            }
        }
    }

    @ViewBuilder private func row(date: String, sessions: [Notebook.SessionItem]) -> some View {
        let main = sessions.first { !$0.isStrength } ?? sessions[0]
        let title = sessions.count > 1 && !main.isStrength
            ? "\(main.title.split(separator: ",").first.map(String.init) ?? main.title), puis renfo" : main.title
        let sub = [main.startTime.flatMap(Numbers.clock), main.location, main.targets.hrCapBpm.map { "≤\($0) bpm" }]
            .compactMap { $0 }.joined(separator: " · ")
        let words = VStack(alignment: .leading, spacing: 2) {
            Text(title).textRole(.secondary).fontWeight(.semibold)
            if !sub.isEmpty { Text(sub).textRole(.caption).foregroundStyle(Palette.muted) }
            if date == tomorrow, !main.isStrength {
                InlineWeather(item: main).textRole(.caption).foregroundStyle(Palette.muted)
            }
        }
        let value = Text(main.plannedValue).textRole(.secondary).foregroundStyle(Palette.muted)
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    DayNumber(date: date, inline: true)
                    words
                    value
                }
            } else {
                HStack(alignment: .center, spacing: Space.s) {
                    DayNumber(date: date)
                    words
                    Spacer(minLength: Space.xs)
                    value
                }
            }
        }
        .padding(.vertical, Space.s)
        .accessibilityElement(children: .combine)
    }
}

/// The landmarks, on paper: the week with its bars, each watched zone against its ceiling,
/// the race. Each leads to its screen: the week and the race to their tab, a zone to its
/// pain.
struct Landmarks: View {
    let today: Notebook.TodayView
    let openTab: (NotebookTab) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Repères")
            VStack(alignment: .leading, spacing: Space.l) {
                week
                ForEach(today.pain.zones, id: \.zone) { zone in
                    pain(zone)
                }
                if let race = today.race { self.race(race) }
            }
        }
    }

    /// Done in full against planned in a lighter weight; the figures sit on one line while
    /// they fit, one under the other otherwise.
    private var week: some View {
        let totals = today.totals
        let runs = "\(totals.runsDone) sortie\(totals.runsDone > 1 ? "s" : "")" + (totals.runsPlanned > 0 ? " sur \(totals.runsPlanned)" : "")
        let figures = Group {
            Text(Numbers.text(totals.kmDone)).figure(.large, .done).foregroundStyle(Palette.ink)
            if let target = totals.kmTarget {
                HStack(alignment: .lastTextBaseline, spacing: Space.xxs) {
                    Text("sur").textRole(.secondary).foregroundStyle(Palette.muted)
                    Text(Numbers.text(target)).figure(.large, .planned).foregroundStyle(Palette.muted)
                }
            }
        }
        return VStack(alignment: .leading, spacing: Space.xs) {
            Button { openTab(.week) } label: {
                LandmarkLabel(text: "Semaine \(Int(today.week.suffix(2)) ?? 0)", family: .run, opens: true)
            }
            .buttonStyle(.plain)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .lastTextBaseline, spacing: Space.xs) { figures }
                VStack(alignment: .leading, spacing: 0) { figures }
            }
            Text("km · \(runs)").textRole(.secondary).foregroundStyle(Palette.muted)
            WeekBars(days: today.strip).padding(.top, Space.xs)
        }
        .accessibilityElement(children: .combine)
    }

    private func pain(_ zone: Notebook.PainTrendZone) -> some View {
        let peak = zone.weeks.last?.peak
        let top = max(3, ((peak ?? 0) + 0.5).rounded(.up))
        return VStack(alignment: .leading, spacing: Space.xs) {
            NavigationLink(value: PainLink(zone: zone.zone, label: zone.label)) {
                LandmarkLabel(text: zone.label, family: .pain, opens: true)
            }
            .buttonStyle(.plain)
            RedLineCursor(label: "Pic d’effort, cette semaine", value: peak ?? 0, unit: "sur 10", scale: 0...top,
                          limit: today.pain.cap, limitLabel: "plafond \(Numbers.text(today.pain.cap))",
                          format: { Numbers.text($0) }, planned: peak == nil)
        }
    }

    private func race(_ race: Notebook.Race) -> some View {
        let date = Day(race.date)?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR"))
            .weekday(.abbreviated).day().month(.wide))
        let facts = [date, race.startTime.flatMap(Numbers.clock).map { "départ \($0)" },
                     race.distanceKm.map { "\(Numbers.text($0)) km" }, race.target.map { "cible \($0)" }]
            .compactMap { $0 }.joined(separator: " · ")
        return VStack(alignment: .leading, spacing: Space.xs) {
            Button { openTab(.race) } label: {
                LandmarkLabel(text: race.name, family: .race, opens: true)
            }
            .buttonStyle(.plain)
            HStack(alignment: .lastTextBaseline, spacing: Space.xs) {
                Text("\(race.days)").figure(.large, .done)
                Text(race.days > 1 ? "jours" : "jour").textRole(.secondary).foregroundStyle(Palette.muted)
            }
            Text(facts).textRole(.caption).foregroundStyle(Palette.muted)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A landmark's name, with its symbol, on a line a finger can press.
private struct LandmarkLabel: View {
    let text: String
    let family: Family
    /// The landmark leads to its screen: a chevron says so.
    var opens = false

    var body: some View {
        HStack(spacing: Space.xs) {
            FamilyBadge(family: family, small: true)
            Text(text).textRole(.secondary).fontWeight(.semibold)
            if opens {
                Spacer(minLength: Space.xs)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .frame(minHeight: 44)
        .contentShape(.rect)
    }
}

/// The week's seven days as bars: done solid, planned in dots, today's letter in the red
/// line. On the track the bars turn white. Seven columns cannot grow far: the words stop at
/// the first accessibility size, and the bars keep their height whatever the text size.
struct WeekBars: View {
    let days: [Notebook.StripDay]
    var height: CGFloat = 52
    /// Under each letter, the day's session in a word (or « repos »).
    var abbreviations = false
    @Environment(\.onTrack) private var onTrack

    var body: some View {
        let peak = max(1, days.map { max($0.doneKm ?? 0, $0.plannedKm ?? 0) }.max() ?? 1)
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(days, id: \.date) { day in
                VStack(spacing: Space.xxs) {
                    Text(label(day)).font(.caption2.weight(.semibold))
                        .foregroundStyle(day.doneKm != nil ? ink : quiet)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    bar(day, peak: peak).frame(height: height, alignment: .bottom)
                    Text(day.letter).font(.caption2.weight(day.today ? .bold : .regular))
                        .foregroundStyle(day.today ? (onTrack ? Palette.onTrack : Palette.accent) : muted)
                        .underline(day.today && onTrack)
                    if abbreviations {
                        Text(day.abbr.isEmpty ? (day.rest ? "repos" : "—") : day.abbr)
                            .font(.caption2).foregroundStyle(quiet).lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
                .frame(maxWidth: .infinity)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
        .accessibilityHidden(true)
    }

    private var ink: Color { onTrack ? Palette.onTrack : Palette.ink }
    private var muted: Color { onTrack ? Palette.onTrack.opacity(0.8) : Palette.muted }
    private var quiet: Color { onTrack ? Palette.onTrack.opacity(0.75) : Palette.muted }

    private func label(_ day: Notebook.StripDay) -> String {
        if let done = day.doneKm, done > 0 { return Numbers.text(done) }
        if let planned = day.plannedKm { return Numbers.text(planned) }
        return day.plannedLabel ?? ""
    }

    @ViewBuilder private func bar(_ day: Notebook.StripDay, peak: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: Radius.mark)
        if let done = day.doneKm, done > 0 {
            shape.fill(onTrack ? Palette.onTrack : Palette.data).frame(width: 18, height: max(4, height * done / peak))
        } else if day.plannedKm != nil || day.plannedLabel != nil {
            DotPattern(color: onTrack ? Palette.onTrack.opacity(0.85) : Palette.data.opacity(0.75), spacing: 4, radius: 1.05)
                .frame(width: 18, height: day.plannedKm.map { max(4, height * $0 / peak) } ?? height / 2)
                .clipShape(shape)
        } else {
            shape.fill(onTrack ? Palette.onTrack.opacity(0.5) : Palette.ghost).frame(width: 18, height: 2)
        }
    }
}
