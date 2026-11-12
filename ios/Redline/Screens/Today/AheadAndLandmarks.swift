import RedlineKit
import SwiftUI

/// A section title on a rule, never a box; a few words may follow it on the right.
struct SectionTitle: View {
    let text: String
    var meta: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(text).font(.title3.bold()).accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                if let meta {
                    Text(meta).font(.footnote).foregroundStyle(Palette.muted).multilineTextAlignment(.trailing)
                }
            }
        }
        .padding(.top, 30)
        .padding(.bottom, 8)
    }
}

/// What is coming: one line per day for the next three days, as the Week tab lists them.
struct AheadList: View {
    let items: [Notebook.SessionItem]
    /// The day whose line carries the weather: tomorrow.
    var tomorrow: String?

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

    private func row(date: String, sessions: [Notebook.SessionItem]) -> some View {
        let main = sessions.first { !$0.isStrength } ?? sessions[0]
        let title = sessions.count > 1 && !main.isStrength
            ? "\(main.title.split(separator: ",").first.map(String.init) ?? main.title), puis renfo" : main.title
        let sub = [main.startTime.flatMap(Numbers.clock), main.location, main.targets.hrCapBpm.map { "≤\($0) bpm" }]
            .compactMap { $0 }.joined(separator: " · ")
        let day = Day(date)
        return HStack(alignment: .center, spacing: 14) {
            VStack(spacing: 0) {
                Text(day.map { "\($0.day)" } ?? "").font(.figure(22))
                Text(day?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.abbreviated)) ?? "")
                    .font(.caption2).foregroundStyle(Palette.muted)
            }
            .frame(width: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                HStack(spacing: 0) {
                    if !sub.isEmpty { Text(sub) }
                    if date == tomorrow, !main.isStrength {
                        if !sub.isEmpty { Text(" · ") }
                        InlineWeather(item: main)
                    }
                }
                .font(.caption)
                .foregroundStyle(Palette.muted)
            }
            Spacer(minLength: 8)
            Text(main.plannedValue).font(.figure(17, weight: .semibold)).foregroundStyle(Palette.muted)
        }
        .padding(.vertical, 12)
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
        VStack(alignment: .leading, spacing: 22) {
            SectionTitle(text: "Repères")
            week
            ForEach(today.pain.zones, id: \.zone) { zone in
                pain(zone)
            }
            if let race = today.race { self.race(race) }
        }
    }

    private var week: some View {
        let totals = today.totals
        let runs = "\(totals.runsDone) sortie\(totals.runsDone > 1 ? "s" : "")" + (totals.runsPlanned > 0 ? " sur \(totals.runsPlanned)" : "")
        return VStack(alignment: .leading, spacing: 10) {
            Button { openTab(.week) } label: {
                LandmarkLabel(text: "Semaine \(Int(today.week.suffix(2)) ?? 0)", family: .run, opens: true)
            }
            .buttonStyle(.plain)
            HStack(alignment: .lastTextBaseline, spacing: 6) {
                Text(Numbers.text(totals.kmDone)).font(.figure(40))
                if let target = totals.kmTarget {
                    Text("sur").font(.subheadline).foregroundStyle(Palette.muted)
                    Text(Numbers.text(target)).font(.figure(40, weight: .semibold)).monospacedDigit().foregroundStyle(Palette.muted)
                }
                Text("km · \(runs)").font(.subheadline).foregroundStyle(Palette.muted)
            }
            WeekBars(days: today.strip)
        }
        .accessibilityElement(children: .combine)
    }

    private func pain(_ zone: Notebook.PainTrendZone) -> some View {
        let peak = zone.weeks.last?.peak
        let top = max(3, ((peak ?? 0) + 0.5).rounded(.up))
        return VStack(alignment: .leading, spacing: 10) {
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
        return VStack(alignment: .leading, spacing: 8) {
            Button { openTab(.race) } label: {
                LandmarkLabel(text: race.name, family: .race, opens: true)
            }
            .buttonStyle(.plain)
            HStack(alignment: .lastTextBaseline, spacing: 6) {
                Text("\(race.days)").font(.figure(40))
                Text(race.days > 1 ? "jours" : "jour").font(.subheadline).foregroundStyle(Palette.muted)
            }
            Text(facts).font(.footnote).foregroundStyle(Palette.muted)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A landmark's name, with its badge.
private struct LandmarkLabel: View {
    let text: String
    let family: Family
    /// The landmark leads to its screen: a chevron says so.
    var opens = false

    var body: some View {
        HStack(spacing: 8) {
            FamilyBadge(family: family, small: true)
            Text(text).font(.subheadline.weight(.semibold))
            if opens {
                Spacer()
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .contentShape(.rect)
    }
}

/// The week's seven days as bars: done solid, planned in dots, today's letter in red. On
/// the track the bars turn white.
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
                VStack(spacing: 4) {
                    Text(label(day)).font(.caption2.weight(.semibold))
                        .foregroundStyle(day.doneKm != nil ? ink : faint)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    bar(day, peak: peak)
                    Text(day.letter).font(.caption2.weight(day.today ? .bold : .regular))
                        .foregroundStyle(day.today ? (onTrack ? Palette.onTrack : Palette.red) : muted)
                        .underline(day.today && onTrack)
                    if abbreviations {
                        Text(day.abbr.isEmpty ? (day.rest ? "repos" : "—") : day.abbr)
                            .font(.system(size: 9)).foregroundStyle(faint).lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(height: height + (abbreviations ? 54 : 40), alignment: .bottom)
        .accessibilityHidden(true)
    }

    private var ink: Color { onTrack ? Palette.onTrack : Palette.ink }
    private var muted: Color { onTrack ? Palette.onTrack.opacity(0.8) : Palette.muted }
    private var faint: Color { onTrack ? Palette.onTrack.opacity(0.7) : Palette.faint }

    private func label(_ day: Notebook.StripDay) -> String {
        if let done = day.doneKm, done > 0 { return Numbers.text(done) }
        if let planned = day.plannedKm { return Numbers.text(planned) }
        return day.plannedLabel ?? ""
    }

    @ViewBuilder private func bar(_ day: Notebook.StripDay, peak: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: 3)
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
