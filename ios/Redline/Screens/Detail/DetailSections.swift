import RedlineKit
import SwiftUI

/// D+, heart rate on its zone, cadence: three tiles, one under the other at the
/// accessibility sizes.
struct DetailFacts: View {
    let detail: Notebook.SessionView

    var body: some View {
        let fact = detail.fact
        DetailTiles {
            DetailTile(label: "Dénivelé", symbol: "mountain.2", value: fact.dplus.map { "\(Int($0.rounded()))" } ?? "—",
                       suffix: fact.dplus != nil ? "m D+" : nil)
            DetailTile(label: "FC moy", symbol: "heart", value: fact.hrAvg.map { Numbers.text($0, digits: 0) } ?? "—") {
                if let heart = fact.hrAvg {
                    HStack(spacing: Space.xxs) {
                        ZoneChip(zone: heartZone(heart, bounds: detail.hrBounds ?? []))
                        if let peak = fact.hrMax { Text("max \(Numbers.text(peak, digits: 0))") }
                    }
                } else {
                    Text("sans brassard")
                }
            }
            DetailTile(label: "Cadence", symbol: "shoeprints.fill", value: fact.cadence.map { Numbers.text($0, digits: 0) } ?? "—") {
                Text("pas/min")
            }
        }
        .padding(.top, Space.l)
    }
}

/// How it felt: one tile per watched zone's pain, the effort, the pleasure, in two columns
/// or one at the accessibility sizes; what is still asked shows where it will go. The
/// wake-up readings around the run close it.
struct DetailFeelings: View {
    let detail: Notebook.SessionView
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let fact = detail.fact, ask = detail.ask, missing = ask?.fields ?? []
        let known = Dictionary(fact.pains.map { ("pain:\($0.zone)", $0) }, uniquingKeysWith: { first, _ in first })
        let watched: [(String, String)] = if let labels = ask?.labels, !labels.isEmpty {
            labels.sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
        } else {
            fact.pains.map { ("pain:\($0.zone)", $0.label) }
        }
        let feelings = watched.map { ($0.0, "Douleur \($0.1)", known[$0.0]?.value) }
            + [("rpe", "Effort (RPE)", fact.rpe), ("enjoyment", "Plaisir", fact.enjoyment)]
        VStack(alignment: .leading, spacing: 0) {
            DetailHead(title: "Ressenti", aside: ask != nil ? "un geste par valeur" : "déclaré après la séance")
            let columns = Array(repeating: GridItem(.flexible(), spacing: Space.s, alignment: .top),
                                count: typeSize.isAccessibilitySize ? 1 : 2)
            LazyVGrid(columns: columns, alignment: .leading, spacing: 0) {
                ForEach(feelings, id: \.0) { metric, label, value in
                    if let value {
                        DetailTile(label: label, value: Numbers.text(value), suffix: "/10") { TenSegments(value: value) }
                    } else {
                        let now = missing.first == metric
                        DetailTile(label: label, value: now ? "?" : "—", pending: true) {
                            Text(now ? "en cours" : missing.contains(metric) ? "ensuite" : "non noté")
                        }
                    }
                }
            }
            if let ask {
                DebriefCard(ask: ask, targets: detail.plan?.targets).padding(.top, Space.s)
            }
            ForEach(detail.wake.zones, id: \.zone) { wake in
                let who = detail.wake.zones.count > 1 ? " · \(wake.label)" : ""
                let parts = [wake.before.map { "Réveil du jour\(who) \(Numbers.text($0))/10" },
                             wake.after.map { "réveil du lendemain \(Numbers.text($0))/10" }
                                ?? (detail.wake.afterDue ? "lendemain\(wake.before == nil ? who : "") : à noter au réveil" : nil)]
                    .compactMap { $0 }
                if !parts.isEmpty {
                    Text(parts.joined(separator: " · ")).textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
                }
            }
        }
    }
}

/// A value out of ten as ten segments, half-filled for a half point, as tall as the text
/// size makes them.
struct TenSegments: View {
    let value: Double
    @ScaledMetric(relativeTo: .footnote) private var height: CGFloat = 5

    var body: some View {
        HStack(spacing: 2) {
            ForEach(1...10, id: \.self) { index in
                let full = value >= Double(index), half = !full && value >= Double(index) - 0.5
                RoundedRectangle(cornerRadius: Radius.mark / 2)
                    .fill(full ? Palette.data : Palette.ghost)
                    .overlay(alignment: .leading) {
                        if half { GeometryReader { Rectangle().fill(Palette.data).frame(width: $0.size.width / 2) } }
                    }
                    .frame(height: height)
            }
        }
        .accessibilityHidden(true)
    }
}

/// Planned and done on one red stroke, each crossed limit named under it, then the steps of
/// a quality session.
struct DetailPlanSection: View {
    let detail: Notebook.SessionView

    var body: some View {
        if let plan = detail.plan {
            let rows = LimitRows(targets: plan.targets, fact: detail.runFact)
            let steps = self.steps(plan.targets)
            if rows != nil || !steps.isEmpty {
                VStack(alignment: .leading, spacing: Space.s) {
                    DetailHead(title: "Prévu et fait")
                    if let rows {
                        rows
                        // A limit crossed is said in words after a symbol, not by the red alone.
                        ForEach(rows.rows.filter(\.crossed)) { row in
                            Label("\(row.label) au-delà de la limite", systemImage: "arrow.up.right")
                                .font(TextRole.caption.font.weight(.semibold))
                                .foregroundStyle(Palette.accent)
                        }
                    }
                    ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                        ViewThatFits(in: .horizontal) {
                            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                                stepName(step)
                                Spacer(minLength: Space.xs)
                                stepValue(step, alignment: .trailing)
                            }
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                stepName(step)
                                stepValue(step, alignment: .leading).padding(.leading, Space.m)
                            }
                        }
                    }
                    if let note = plan.note {
                        DisclosureGroup {
                            Text(note).textRole(.secondary).foregroundStyle(Palette.muted).frame(maxWidth: .infinity, alignment: .leading)
                        } label: {
                            Text("La consigne").textRole(.body)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .tint(Palette.ink)
                    }
                }
            }
        }
    }

    private func stepName(_ step: Step) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Circle().fill(step.quality ? Palette.accent : Palette.data.opacity(0.4)).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(step.name).textRole(.secondary).fontWeight(.semibold)
                if let sub = step.sub { Text(sub).textRole(.caption).foregroundStyle(Palette.muted) }
            }
        }
    }

    private func stepValue(_ step: Step, alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 1) {
            Text(step.value).figure(.inline).foregroundStyle(Palette.ink)
            if let sub = step.valueSub { Text(sub).textRole(.caption).foregroundStyle(Palette.muted) }
        }
    }

    private struct Step {
        let quality: Bool
        let name: String
        let sub: String?
        let value: String
        let valueSub: String?
    }

    private func steps(_ targets: Notebook.Targets) -> [Step] {
        let phases = detail.phases ?? [], reps = detail.reps ?? []
        var steps: [Step] = []
        if let warm = phases.first(where: { $0.kind == .warmup }) {
            steps.append(Step(quality: false, name: "Échauffement", sub: nil, value: Numbers.duration(warm.timeS),
                              valueSub: warm.hr.map { "FC \($0)" }))
        }
        if !reps.isEmpty {
            let times = reps.map(\.timeS), beats = reps.compactMap(\.hr)
            let low = times.min()!, high = times.max()!
            steps.append(Step(quality: true,
                              name: "\(reps.count) × \(Numbers.minutes(times.reduce(0, +) / Double(reps.count)))",
                              sub: targets.rpeRange.map { "effort visé \($0.replacingOccurrences(of: "≤", with: ""))" },
                              value: Numbers.duration(low) + (high != low ? " – \(Numbers.duration(high))" : ""),
                              valueSub: beats.isEmpty ? nil
                                : "FC \(beats.min()!)" + (beats.max() != beats.min() ? " – \(beats.max()!)" : "")))
        }
        if let cool = phases.first(where: { $0.kind == .cooldown }) {
            steps.append(Step(quality: false, name: "Retour au calme", sub: nil, value: Numbers.duration(cool.timeS),
                              valueSub: cool.hr.map { "FC \($0)" }))
        }
        return steps
    }
}

extension Notebook.SessionView {
    /// The detail's fact read as a run's fact, for the planned-and-done rows.
    var runFact: Notebook.Fact {
        Notebook.Fact(observationId: fact.observationId, kind: fact.kind, date: fact.date, km: fact.km,
                      durationMin: fact.durationMin, hrAvg: fact.hrAvg, rpe: fact.rpe, pains: fact.pains,
                      enjoyment: fact.enjoyment, dplus: fact.dplus, startedAt: fact.startedAt, over: fact.over)
    }
}

/// Time in each zone, as bars in the zone's shade under its `Z1`–`Z5` label, with its share.
/// The label and figure columns follow the text size.
struct DetailZones: View {
    let detail: Notebook.SessionView
    @ScaledMetric(relativeTo: .footnote) private var labelWidth: CGFloat = 56
    @ScaledMetric(relativeTo: .body) private var valueWidth: CGFloat = 56

    var body: some View {
        if let zones = detail.zones {
            let seconds = zones.seconds, total = max(seconds.reduce(0, +), 1), top = max(seconds.max() ?? 1, 1)
            let bounds = detail.hrBounds ?? []
            VStack(alignment: .leading, spacing: Space.xs) {
                DetailHead(title: "Temps par zone", aside: zones.max.map { "FC max estimée \($0)" })
                ForEach(Array(seconds.enumerated()), id: \.offset) { index, value in
                    let zone = index + 1, share = Int((100 * Double(value) / Double(total)).rounded())
                    HStack(spacing: Space.xs) {
                        VStack(alignment: .leading, spacing: 1) {
                            ZoneChip(zone: zone)
                            Text(span(zone, bounds)).font(.caption2).foregroundStyle(Palette.muted)
                        }
                        .frame(width: labelWidth, alignment: .leading)
                        GeometryReader { geometry in
                            RoundedRectangle(cornerRadius: Radius.mark).fill(ZoneShade.color(zone))
                                .frame(width: max(geometry.size.width * Double(value) / Double(top), value > 0 ? 2 : 0))
                        }
                        .frame(height: 12)
                        VStack(alignment: .trailing, spacing: 1) {
                            Text(Numbers.minutes(Double(value))).figure(.inline).foregroundStyle(Palette.ink)
                            Text(value > 0 && share == 0 ? "< 1 %" : "\(share) %").font(.caption2).foregroundStyle(Palette.muted)
                        }
                        .frame(width: valueWidth, alignment: .trailing)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .dynamicTypeSize(...DynamicTypeSize.accessibility2)
        }
    }

    private func span(_ zone: Int, _ bounds: [Int]) -> String {
        guard bounds.count >= 4 else { return "" }
        switch zone {
        case 1: return "< \(bounds[0])"
        case 5: return "> \(bounds[3])"
        default: return "\(bounds[zone - 2])–\(bounds[zone - 1])"
        }
    }
}

/// The previous session of the same kind, side by side with this one.
struct DetailPrevious: View {
    let detail: Notebook.SessionView

    var body: some View {
        if let before = detail.previous {
            let fact = detail.fact, reps = detail.reps ?? []
            let number = { (value: Double?, digits: Int) in value.map { Numbers.text($0, digits: digits) } ?? "—" }
            let repDplus = reps.isEmpty ? nil : Int((Double(reps.reduce(0) { $0 + $1.dplus }) / Double(reps.count)).rounded())
            let beats = reps.compactMap(\.hr)
            let repBeat = beats.isEmpty ? nil : Int((Double(beats.reduce(0, +)) / Double(beats.count)).rounded())
            let then = Dictionary(before.pains.map { ($0.zone, $0.value) }, uniquingKeysWith: { first, _ in first })
            var rows: [(String, String, String)] = []
            if detail.quality, let now = repDplus, let old = before.repDplus { rows.append(("Montée par rép.", "+\(now) m", "+\(old) m")) }
            if detail.quality, let now = repBeat, let old = before.repHr { rows.append(("FC moy des rép.", "\(now)", "\(old)")) }
            if !detail.quality {
                rows.append(("Distance", number(fact.km, 1), number(before.km, 1)))
                rows.append(("FC moyenne", number(fact.hrAvg, 0), number(before.hrAvg, 0)))
            }
            rows.append(("Effort (RPE)", number(fact.rpe, 1), number(before.rpe, 1)))
            for pain in fact.pains { rows.append(("Douleur \(pain.label)", number(pain.value, 1), number(then[pain.zone], 1))) }
            rows.append(("Plaisir", number(fact.enjoyment, 1), number(before.enjoyment, 1)))
            let title = detail.quality ? "Le seuil précédent"
                : detail.kind == "long" ? "La sortie longue précédente" : "La séance précédente du même type"
            return AnyView(VStack(alignment: .leading, spacing: 0) {
                DetailHead(title: title, aside: shortDate(before.date))
                Grid(alignment: .leading, horizontalSpacing: Space.s, verticalSpacing: Space.xs) {
                    GridRow {
                        Text("")
                        Text(shortDate(detail.date)).font(TextRole.caption.font.weight(.semibold)).foregroundStyle(Palette.ink)
                        Text(shortDate(before.date)).font(TextRole.caption.font.weight(.semibold)).foregroundStyle(Palette.muted)
                    }
                    ForEach(rows, id: \.0) { label, now, old in
                        GridRow {
                            Text(label).textRole(.secondary).foregroundStyle(Palette.muted)
                            Text(now).figure(.inline).foregroundStyle(Palette.ink)
                            Text(old).figure(.inline, .planned).foregroundStyle(Palette.muted)
                        }
                    }
                }
                if detail.quality, let now = repDplus, let old = before.repDplus,
                   abs(Double(now - old)) > 0.2 * Double(max(now, old)) {
                    Text("Côte \(now > old ? "plus raide" : "moins raide") que la dernière fois : les allures ne se comparent pas d’une côte à l’autre.")
                        .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
                }
            })
        }
        return AnyView(EmptyView())
    }
}

/// `06/10` from an API day.
func shortDate(_ text: String) -> String {
    Day(text).map { String(format: "%02d/%02d", $0.day, $0.month) } ?? text
}

/// The weather at the start and the shoes worn, side by side or one under the other at the
/// accessibility sizes.
struct DetailConditions: View {
    let detail: Notebook.SessionView

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            DetailHead(title: "Conditions")
            DetailTiles {
                if let weather = detail.weather, let temp = weather.temp {
                    DetailTile(label: "Météo au départ", symbol: "cloud.sun", value: Numbers.text(temp, digits: 0), suffix: "°C") {
                        Text([weather.conditions, weather.feelsLike.map { "ressenti \(Numbers.text($0, digits: 0)) °C" },
                              weather.wbgt.map { "WBGT \(Numbers.text($0, digits: 0))" }].compactMap { $0 }.joined(separator: " · "))
                    }
                } else if let wbgt = detail.weather?.wbgt {
                    DetailTile(label: "Météo au départ", symbol: "cloud.sun", value: Numbers.text(wbgt, digits: 0), suffix: "°C WBGT") {
                        Text("température au thermomètre-globe")
                    }
                } else {
                    DetailTile(label: "Météo au départ", symbol: "cloud.sun", value: "—", pending: true) { Text("non relevée") }
                }
                if let gear = detail.gear {
                    let wear = gear.status == "retired" ? "retirée" : gear.retireAtKm.map { "contrôle à \(Numbers.text($0, digits: 0))" }
                    DetailTile(label: "Chaussures", symbol: "shoe", value: "\(gear.km)", suffix: "km") {
                        Text([gear.name, wear].compactMap { $0 }.joined(separator: " · "))
                    }
                } else {
                    DetailTile(label: "Chaussures", symbol: "shoe", value: "—", pending: true) { Text("non renseignées") }
                }
            }
        }
    }
}

/// The session's week: its kilometres against the target, and what comes next.
struct DetailWeek: View {
    let detail: Notebook.SessionView

    var body: some View {
        let week = detail.week
        VStack(alignment: .leading, spacing: Space.xs) {
            DetailHead(title: "Dans la semaine", aside: "semaine \(Int(week.week.suffix(2)) ?? 0)" + (week.current ? "" : ", close"))
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                Text(Numbers.text(week.kmDone)).figure(.large).foregroundStyle(Palette.ink)
                Text(week.kmTarget.map { "sur ~\(Numbers.text($0, digits: 0)) km" } ?? "km").textRole(.secondary).foregroundStyle(Palette.muted)
            }
            if let target = week.kmTarget, target > 0 {
                Capsule().fill(Palette.ghost).frame(height: 6)
                    .overlay(alignment: .leading) {
                        GeometryReader { Capsule().fill(Palette.data).frame(width: $0.size.width * min(week.kmDone / target, 1)) }
                    }
            }
            Text("\(week.runsDone) sortie\(week.runsDone > 1 ? "s" : "")" + (week.runsPlanned > 0 ? " sur \(week.runsPlanned)" : ""))
                .textRole(.caption).foregroundStyle(Palette.muted)
            if let next = week.next {
                HStack(spacing: Space.xs) {
                    FamilyBadges(families: next.families.map { Family(kind: $0.first ?? "", workout: $0.dropFirst().first) }, small: true)
                    Text("Ensuite · \(next.titles.joined(separator: " + "))").textRole(.secondary)
                    Spacer(minLength: Space.xs)
                    Text(shortDate(next.date)).textRole(.caption).foregroundStyle(Palette.muted)
                }
                .padding(.top, Space.xs)
            }
        }
    }
}
