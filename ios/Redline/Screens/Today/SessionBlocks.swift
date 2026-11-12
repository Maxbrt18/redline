import RedlineKit
import SwiftUI

extension LimitRows {
    /// A session's limits as rows: done when there is a fact, planned otherwise.
    init?(targets: Notebook.Targets, fact: Notebook.Fact?) {
        var rows: [Row] = []
        let cap = targets.distanceCapKm, aim = targets.distanceTargetKm
        if let reference = cap ?? aim {
            rows.append(Row(label: "Distance", low: reference / 2, cap: cap, target: cap == nil ? aim : nil,
                            fill: fact?.km,
                            value: fact?.km.map { Numbers.text($0, digits: 2) } ?? Numbers.text(aim ?? reference),
                            sub: cap.map { "\(Numbers.text($0)) max" } ?? "visés"))
        }
        if let minutes = targets.durationCapMin {
            rows.append(Row(label: "Durée", low: Double(minutes) / 3, cap: Double(minutes),
                            fill: fact?.durationMin.map(Double.init),
                            value: "\(fact?.durationMin ?? minutes)′", sub: "\(minutes)′ max"))
        }
        if let heart = targets.hrCapBpm {
            rows.append(Row(label: "FC moy.", low: Double(heart - 30), cap: Double(heart), fill: fact?.hrAvg,
                            value: fact?.hrAvg.map { Numbers.text($0, digits: 0) } ?? "\(heart)", sub: "\(heart) max"))
        }
        if let rpeMax = targets.rpeMax, let range = targets.rpeRange {
            rows.append(Row(label: "Effort", cap: Double(rpeMax), band: targets.rpeMin.map(Double.init), fill: fact?.rpe,
                            value: fact?.rpe.map { Numbers.text($0) } ?? range, sub: "visé \(range)"))
        }
        guard !rows.isEmpty else { return nil }
        self.init(rows: rows)
    }
}

/// A run's measures without limits to read them against: up to four figures.
struct FactTiles: View {
    let fact: Notebook.Fact
    let targets: Notebook.Targets

    var body: some View {
        let over = Set(fact.over)
        var cells: [(String, String, Bool)] = []
        if let km = fact.km { cells.append((Numbers.text(km, digits: 2), "km", over.contains("km"))) }
        if let minutes = fact.durationMin {
            cells.append(("\(minutes)", "min" + (targets.durationCapMin.map { " · ≤\($0)" } ?? ""), over.contains("duration_min")))
        }
        if let heart = fact.hrAvg {
            cells.append((Numbers.text(heart, digits: 0), "FC" + (targets.hrCapBpm.map { " · ≤\($0)" } ?? " moy"), over.contains("hr_avg")))
        }
        if let pace = fact.pace { cells.append((pace, "/km", false)) }
        return HStack(alignment: .top, spacing: 0) {
            ForEach(Array(cells.prefix(4).enumerated()), id: \.offset) { _, cell in
                VStack(alignment: .leading, spacing: 2) {
                    Text(cell.0).font(.figure(26)).monospacedDigit().foregroundStyle(cell.2 ? Palette.over : Palette.ink)
                    Text(cell.1).font(.caption).foregroundStyle(Palette.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 12)
            }
        }
    }
}

/// What a done run passed, in words: « FC moyenne 6 bpm au-dessus de ton plafond. »
struct OverLine: View {
    let fact: Notebook.Fact
    let targets: Notebook.Targets

    var body: some View {
        let over = Set(fact.over)
        var parts: [String] = []
        if over.contains("hr_avg"), let heart = fact.hrAvg, let cap = targets.hrCapBpm {
            parts.append("FC moyenne \(Numbers.text(heart - Double(cap), digits: 0)) bpm au-dessus de ton plafond")
        }
        if over.contains("rpe"), let rpeMax = targets.rpeMax { parts.append("RPE au-dessus de \(rpeMax)") }
        if over.contains("km"), let km = fact.km, let cap = targets.distanceCapKm {
            parts.append("\(Numbers.text(km - cap)) km au-delà du plafond")
        }
        if over.contains("duration_min"), let minutes = fact.durationMin, let cap = targets.durationCapMin {
            parts.append("\(minutes - cap) min au-delà du plafond")
        }
        return Group {
            if !parts.isEmpty {
                Text(parts.joined(separator: " · ").capitalizedFirst + ".")
                    .font(.subheadline)
                    .foregroundStyle(Palette.over)
            }
        }
    }
}

/// A planned run's limits as cursors, nothing done yet: distance or duration, then the
/// heart rate under its ceiling.
struct TargetCursors: View {
    let targets: Notebook.Targets

    var body: some View {
        VStack(spacing: 18) {
            if let reference = targets.distanceCapKm ?? targets.distanceTargetKm {
                let cap = targets.distanceCapKm, aim = targets.distanceTargetKm
                let top = (reference * 1.25).rounded(.up)
                RedLineCursor(label: "Distance", value: aim ?? reference, unit: aim != nil ? "km visés" : "km au plus",
                              scale: 0...top, limit: cap, limitLabel: cap.map { Numbers.text($0) },
                              target: aim, targetLabel: aim != nil && cap != nil ? Numbers.text(aim!) : nil,
                              format: { Numbers.text($0) }, planned: true)
            } else if let minutes = targets.durationCapMin {
                let top = (Double(minutes) * 1.2 / 10).rounded(.up) * 10
                RedLineCursor(label: "Durée", value: Double(minutes), unit: "min au plus", scale: 0...top,
                              limit: Double(minutes), limitLabel: "\(minutes)′", format: { Numbers.text($0, digits: 0) },
                              planned: true)
            }
            if let heart = targets.hrCapBpm {
                RedLineCursor(label: "FC moyenne", value: Double(heart), unit: "bpm au plus",
                              scale: Double(heart - 47)...Double(heart + 13), limit: Double(heart),
                              format: { Numbers.text($0, digits: 0) }, planned: true)
            }
        }
    }
}
