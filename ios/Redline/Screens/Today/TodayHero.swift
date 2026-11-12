import RedlineKit
import SwiftUI

/// The head of Today always carries one session and its figure: the run still to do, solid
/// and lighter; else the run done today, solid and full; else the next run. The figure and
/// its words sit side by side while they fit, one under the other otherwise.
struct TodayHero: View {
    let kicker: String
    let item: Notebook.SessionItem

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Kicker(text: [kicker, item.startTime.flatMap(Numbers.clock)].compactMap { $0 }.joined(separator: " · "))
            HStack(spacing: Space.xs) {
                FamilyBadge(family: item.family)
                Text(item.title).textRole(.heading).fixedSize(horizontal: false, vertical: true)
            }
            if let figure {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .lastTextBaseline, spacing: Space.s) { figureAndWords(figure) }
                    VStack(alignment: .leading, spacing: Space.xxs) { figureAndWords(figure) }
                }
                .padding(.top, Space.xxs)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var done: Bool { item.isDone && item.runFact != nil }

    @ViewBuilder private func figureAndWords(_ figure: (value: String, side: [String])) -> some View {
        Text(figure.value)
            .figure(.hero, done ? .done : .planned)
            .opacity(done ? 1 : 0.72)
        VStack(alignment: .leading, spacing: 2) {
            ForEach(figure.side, id: \.self) { Text($0) }
        }
        .textRole(.secondary)
        .opacity(0.82)
    }

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
