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
