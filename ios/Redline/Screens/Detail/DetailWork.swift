import Charts
import RedlineKit
import SwiftUI

/// The work itself: for a quality session, the heart rate over its zones with the
/// repetitions in the red line, their table and what to read from them; for a steady run,
/// the same curve and the kilometres.
struct DetailWork: View {
    let detail: Notebook.SessionView

    var body: some View {
        if let reps = detail.reps, !reps.isEmpty {
            VStack(alignment: .leading, spacing: Space.s) {
                DetailHead(title: "Les répétitions",
                           aside: "\(reps.count) × \(Numbers.minutes(reps.reduce(0) { $0 + $1.timeS } / Double(reps.count)))")
                HeartChart(series: detail.series, bounds: detail.hrBounds ?? [], dplus: detail.fact.dplus)
                RepsTable(reps: reps, bounds: detail.hrBounds ?? [])
                if let note = repsNote(reps) { Text(note).textRole(.caption).foregroundStyle(Palette.muted) }
                ReadRows(rows: qualityRead())
            }
        } else if detail.series?.hr?.contains(where: { $0 != nil }) == true || !(detail.km ?? []).isEmpty {
            VStack(alignment: .leading, spacing: Space.s) {
                DetailHead(title: "Au kilomètre", aside: "temps en mouvement")
                HeartChart(series: detail.series, bounds: detail.hrBounds ?? [], dplus: detail.fact.dplus)
                if let splits = detail.km, !splits.isEmpty { KmTable(splits: splits, bounds: detail.hrBounds ?? []) }
                if let stops = detail.stops {
                    Text(stopsLine(stops)).textRole(.caption).foregroundStyle(Palette.muted)
                }
                ReadRows(rows: steadyRead())
            }
        }
    }

    private func repsNote(_ reps: [Notebook.Rep]) -> String? {
        var parts: [String] = []
        let recoveries = (detail.phases ?? []).filter { $0.kind == .recovery }
        if !recoveries.isEmpty { parts.append("Récupérations : \(recoveries.map { Numbers.duration($0.timeS) }.joined(separator: " · ")).") }
        let drops = reps.compactMap(\.hrDrop).filter { $0 > 0 }
        if let low = drops.min(), let high = drops.max() {
            parts.append("Une minute après chaque répétition, la FC redescend de \(low)" + (high != low ? " à \(high)" : "") + " bpm.")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    private func stopsLine(_ stops: Notebook.StopSummary) -> String {
        var line = "Arrêts : \(Numbers.duration(Double(stops.totalS))) en \(stops.count) fois"
        if stops.count > 1, let km = stops.longestKm {
            line += ", dont \(Numbers.duration(Double(stops.longestS))) au \(km)\(km == 1 ? "ᵉʳ" : "ᵉ") km"
        }
        return line + "."
    }

    private func qualityRead() -> [(String, String, String?)] {
        var rows: [(String, String, String?)] = []
        let targets = detail.plan?.targets, rpe = detail.fact.rpe
        if let rpe, let high = targets?.rpeMax, let range = targets?.rpeRange {
            let low = Double(targets?.rpeMin ?? 0)
            let title = rpe >= low && rpe <= Double(high) ? "Effort dans la bande"
                : rpe > Double(high) ? "Effort au-dessus de la bande" : "Effort sous la bande"
            rows.append(("gauge.with.needle", title, "RPE \(Numbers.text(rpe)) pour \(range) visé"))
        }
        if let reading = detail.reading {
            let range = "\(Numbers.pace(Double(reading.fastestBefore))) – \(Numbers.pace(Double(reading.slowestBefore)))"
            rows.append(reading.finalAcceleration
                ? ("chart.line.uptrend.xyaxis", "Accélération finale", "la dernière à \(Numbers.pace(Double(reading.last))), plus vite que les précédentes (\(range))")
                : ("chart.line.flattrend.xyaxis", "Pas d’accélération finale", "la dernière à \(Numbers.pace(Double(reading.last))), dans l’écart des précédentes (\(range))"))
        }
        if let reserve = detail.fact.reserve {
            rows.append(("questionmark.circle", reserve > 0 ? "Une répétition en réserve" : "Pas de réserve",
                         reserve > 0 ? "il t’en restait une à la fin" : "tu étais au bout"))
        } else {
            rows.append(("questionmark.circle", "Réserve non déclarée",
                         detail.ask?.fields.contains("reserve") == true ? "demandée au débrief" : nil))
        }
        return rows
    }

    private func steadyRead() -> [(String, String, String?)] {
        var rows: [(String, String, String?)] = []
        if let drift = detail.drift {
            let delta = drift.second - drift.first
            rows.append(("chart.line.uptrend.xyaxis",
                         abs(delta) < 3 ? "Pas de dérive cardiaque" : "FC \(delta > 0 ? "en hausse" : "en baisse") de \(abs(delta)) bpm",
                         "\(drift.first) sur la 1ʳᵉ moitié, \(drift.second) sur la 2ᵈᵉ"))
        }
        if let rpe = detail.fact.rpe, let targets = detail.plan?.targets, let high = targets.rpeMax, let range = targets.rpeRange {
            let low = Double(targets.rpeMin ?? high)
            rows.append(("gauge.with.needle",
                         rpe > Double(high) ? "Effort au-dessus de la cible" : rpe < low ? "Effort sous la cible" : "Effort dans la cible",
                         "RPE \(Numbers.text(rpe)) pour \(range) visé"))
        }
        return rows
    }
}

/// What to read from the work, one line each.
private struct ReadRows: View {
    let rows: [(String, String, String?)]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            ForEach(rows, id: \.1) { symbol, title, detail in
                Label {
                    Text("\(Text(title).bold())\(detail.map { " · \($0)" } ?? "")").textRole(.secondary)
                } icon: {
                    Image(systemName: symbol).foregroundStyle(Palette.muted)
                }
            }
        }
    }
}

/// The heart rate over the five zones of the instance, each named `Z1`–`Z5` at its right,
/// the repetitions as numbered bands in the red line, the stops named; under it, the
/// altitude when the course is not flat. A graphic: its labels stop growing at the first
/// accessibility size.
struct HeartChart: View {
    let series: Notebook.Series?
    let bounds: [Int]
    var dplus: Double?

    /// Both charts reserve the same width for their labels, so that their minutes align.
    @ScaledMetric(relativeTo: .caption2) private var labelWidth: CGFloat = 38

    var body: some View {
        if let series, let hr = series.hr, hr.contains(where: { $0 != nil }) {
            let edges = [100] + bounds + [200]
            let points = Self.segments(times: series.t, values: hr.map { $0.map(Double.init) })
            let beats = points.map(\.value)
            VStack(alignment: .leading, spacing: Space.xs) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        chartTitle
                        Spacer(minLength: Space.xs)
                        flat(series)
                    }
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        chartTitle
                        flat(series)
                    }
                }
                .padding(.bottom, Space.s)
                Chart {
                    ForEach(1..<min(edges.count, 6), id: \.self) { zone in
                        RectangleMark(yStart: .value("bas", edges[zone - 1]), yEnd: .value("haut", edges[zone]))
                            .foregroundStyle(ZoneShade.color(zone).opacity(0.18))
                    }
                    ForEach(Array(series.bands.enumerated()), id: \.offset) { index, band in
                        if band.count == 2 {
                            RectangleMark(xStart: .value("début", band[0] / 60), xEnd: .value("fin", band[1] / 60))
                                .foregroundStyle(Palette.accent.opacity(0.14))
                                .annotation(position: .overlay, alignment: .top, spacing: 2) {
                                    Text("\(index + 1)").font(.caption2.weight(.bold)).foregroundStyle(Palette.accent)
                                }
                        }
                    }
                    ForEach(Array(series.stops.enumerated()), id: \.offset) { _, stop in
                        if stop.count == 2 {
                            RectangleMark(xStart: .value("début", Double(stop[0]) / 60), xEnd: .value("fin", Double(stop[1]) / 60))
                                .foregroundStyle(.clear)
                                .annotation(position: .overlay, alignment: .bottom, spacing: 2) {
                                    Text("arrêt").font(.caption2).foregroundStyle(Palette.muted).fixedSize()
                                }
                        }
                    }
                    // A stop breaks the line: each run of known values is its own series.
                    ForEach(Array(points.enumerated()), id: \.offset) { _, point in
                        LineMark(x: .value("minute", point.minute), y: .value("FC", point.value),
                                 series: .value("segment", point.segment))
                            .foregroundStyle(Palette.ink)
                            .lineStyle(StrokeStyle(lineWidth: 1.4))
                            .interpolationMethod(.monotone)
                    }
                }
                .chartYScale(domain: 100...200)
                .chartXScale(domain: 0...Double(series.duration) / 60)
                .chartYAxis {
                    AxisMarks(position: .trailing, values: Self.zoneMiddles(edges)) { value in
                        AxisValueLabel {
                            if let middle = value.as(Double.self), let zone = Self.zoneMiddles(edges).firstIndex(of: middle) {
                                Text("Z\(zone + 1)").font(.caption2.weight(.semibold)).foregroundStyle(Palette.muted)
                                    .frame(width: labelWidth, alignment: .leading)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .stride(by: series.duration > 75 * 60 ? 20 : series.duration > 30 * 60 ? 15 : 5)) { value in
                        AxisValueLabel { if let minute = value.as(Double.self) { Text(minute == 0 ? "0" : "\(Int(minute))′") } }
                    }
                }
                .frame(height: 150)
                .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                .accessibilityLabel("Fréquence cardiaque de \(Int(beats.min() ?? 0)) à \(Int(beats.max() ?? 0)) bpm, sur le fond des cinq zones")
                if let alt = series.alt {
                    AltitudeChart(times: series.t, altitudes: alt, duration: series.duration, dplus: dplus,
                                  labelWidth: labelWidth)
                }
            }
        }
    }

    private var chartTitle: some View {
        Text("\(Text("Fréquence cardiaque").bold()) sur ses zones").textRole(.caption)
    }

    @ViewBuilder private func flat(_ series: Notebook.Series) -> some View {
        if series.alt == nil { Text("parcours plat, profil masqué").textRole(.caption).foregroundStyle(Palette.muted) }
    }

    /// The middle of each zone, where its name is written.
    static func zoneMiddles(_ edges: [Int]) -> [Double] {
        zip(edges, edges.dropFirst()).prefix(5).map { Double($0 + $1) / 2 }
    }

    /// The known values in minutes, numbered by the run of known values they belong to.
    static func segments(times: [Int], values: [Double?]) -> [(minute: Double, value: Double, segment: Int)] {
        var points: [(minute: Double, value: Double, segment: Int)] = [], segment = 0, open = false
        for (time, value) in zip(times, values) {
            guard let value else {
                if open { segment += 1 }
                open = false
                continue
            }
            open = true
            points.append((Double(time) / 60, value, segment))
        }
        return points
    }
}

private struct AltitudeChart: View {
    let times: [Int]
    let altitudes: [Double?]
    let duration: Int
    let dplus: Double?
    let labelWidth: CGFloat

    var body: some View {
        let points = zip(times, altitudes).compactMap { time, altitude in altitude.map { (Double(time) / 60, $0) } }
        let low = points.map(\.1).min() ?? 0, high = points.map(\.1).max() ?? 1
        VStack(alignment: .leading, spacing: Space.xxs) {
            Text("\(Text("Altitude").bold())\(dplus.map { " · +\(Int($0.rounded())) m" } ?? "")").textRole(.caption)
            Chart(Array(points.enumerated()), id: \.offset) { _, point in
                AreaMark(x: .value("minute", point.0), yStart: .value("bas", low), yEnd: .value("altitude", point.1))
                    .foregroundStyle(Palette.ghost)
            }
            .chartXScale(domain: 0...Double(duration) / 60)
            .chartYScale(domain: low...max(high, low + 1))
            .chartXAxis(.hidden)
            .chartYAxis {
                AxisMarks(position: .trailing, values: [low, high]) { value in
                    AxisValueLabel {
                        if let metres = value.as(Double.self) {
                            Text("\(Int(metres.rounded())) m").font(.caption2)
                                .frame(width: labelWidth, alignment: .leading)
                        }
                    }
                }
            }
            .frame(height: 52)
            .dynamicTypeSize(...DynamicTypeSize.accessibility1)
            .accessibilityLabel("Altitude de \(Int(low.rounded())) à \(Int(high.rounded())) m")
        }
    }
}

/// One line per repetition: its number, its time, its climb, its pace, its heart rate with
/// its zone, its cadence. A table: it stops growing at the first accessibility size.
private struct RepsTable: View {
    let reps: [Notebook.Rep]
    let bounds: [Int]

    var body: some View {
        Grid(alignment: .trailing, horizontalSpacing: Space.xs, verticalSpacing: Space.xs) {
            GridRow {
                Text("")
                Text("Durée"); Text("D+"); Text("Allure"); Text("FC moy"); Text("Cad.")
            }
            .font(.caption2).foregroundStyle(Palette.muted)
            ForEach(reps, id: \.n) { rep in
                GridRow {
                    Text("\(rep.n)").figure(.inline).foregroundStyle(Palette.accent)
                        .gridColumnAlignment(.leading)
                    Text(Numbers.duration(rep.timeS))
                    Text("\(rep.dplus) m")
                    Text(rep.paceS.map { Numbers.pace(Double($0)) } ?? "—")
                    ZonedBeat(beat: rep.hr, bounds: bounds)
                    Text(rep.cadence.map { "\($0)" } ?? "—")
                }
                .figure(.inline)
                .foregroundStyle(Palette.ink)
                .accessibilityElement(children: .combine)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
    }
}

/// A heart rate followed by its zone's label on the zone's shade.
private struct ZonedBeat: View {
    let beat: Int?
    let bounds: [Int]

    var body: some View {
        if let beat {
            HStack(spacing: Space.xxs) {
                Text("\(beat)")
                ZoneChip(zone: heartZone(Double(beat), bounds: bounds))
            }
        } else {
            Text("—")
        }
    }
}

/// One line per kilometre: its pace and a bar (longer is faster), its heart rate with its
/// zone, its cadence. A table: it stops growing at the first accessibility size.
private struct KmTable: View {
    let splits: [Notebook.KmSplit]
    let bounds: [Int]

    var body: some View {
        let paces = splits.map(\.paceS), fast = paces.min() ?? 0, slow = paces.max() ?? 0
        Grid(alignment: .leading, horizontalSpacing: Space.xs, verticalSpacing: Space.xs) {
            GridRow {
                Text("Km"); Text("Allure"); Text("plus long = plus vite"); Text("FC"); Text("Cad.")
            }
            .font(.caption2).foregroundStyle(Palette.muted)
            ForEach(Array(splits.enumerated()), id: \.offset) { index, split in
                let last = index == splits.count - 1 && split.distM < 950
                GridRow {
                    Text(last ? Numbers.text(Double(split.distM) / 1000, digits: 2) : "\(split.n)")
                    Text(Numbers.pace(Double(split.paceS)))
                    GeometryReader { geometry in
                        Capsule().fill(Palette.data)
                            .frame(width: geometry.size.width * (slow == fast ? 0.6
                                : 0.15 + 0.85 * Double(slow - split.paceS) / Double(slow - fast)), height: 6)
                            .frame(maxHeight: .infinity)
                    }
                    .frame(height: 14)
                    ZonedBeat(beat: split.hr, bounds: bounds)
                    Text(split.cadence.map { "\($0)" } ?? "—").foregroundStyle(Palette.muted)
                }
                .figure(.inline)
                .foregroundStyle(Palette.ink)
                .accessibilityElement(children: .combine)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
    }
}
