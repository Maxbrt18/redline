import RedlineKit
import SwiftUI

/// The head of Today always carries one session and its figure: the run still to do, in
/// outline; else the run done today, solid; else the next run.
struct TodayHero: View {
    let kicker: String
    let item: Notebook.SessionItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Kicker(text: [kicker, item.startTime.flatMap(Numbers.clock)].compactMap { $0 }.joined(separator: " · "))
            HStack(spacing: 10) {
                FamilyBadge(family: item.family)
                Text(item.title).font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
            }
            if let figure {
                HStack(alignment: .lastTextBaseline, spacing: 12) {
                    if done {
                        Text(figure.value).font(.figure(96)).minimumScaleFactor(0.5)
                    } else {
                        OutlinedFigure(text: figure.value, size: 96, lineWidth: 2.2, color: Palette.onTrack)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(figure.side, id: \.self) { Text($0) }
                    }
                    .font(.subheadline)
                    .opacity(0.82)
                }
                .padding(.top, 4)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var done: Bool { item.isDone && item.runFact != nil }

    /// The figure and the lines beside it.
    private var figure: (value: String, side: [String])? {
        if done, let fact = item.runFact {
            if let km = fact.km {
                let time = fact.durationMin.map { "\($0) min" + (fact.pace.map { " · \($0)/km" } ?? "") }
                return (Numbers.text(km), ["km"] + [time].compactMap { $0 })
            }
            return fact.durationMin.map { ("\($0)′", []) }
        }
        let targets = item.targets
        var result: (value: String, side: [String])?
        if let aim = targets.distanceTargetKm {
            result = (Numbers.text(aim), ["km visés"] + [targets.distanceCapKm.map { "\(Numbers.text($0)) au plus" }].compactMap { $0 })
        } else if let cap = targets.distanceCapKm {
            result = (Numbers.text(cap), ["km au plus"])
        } else if let minutes = targets.durationCapMin {
            result = ("\(minutes)′", ["au plus"])
        }
        if let range = targets.rpeRange, result != nil { result!.side.append("RPE \(range)") }
        return result
    }
}
