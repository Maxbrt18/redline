import SwiftUI

/// A value on a rail, against what bounds it. A limit is a red stroke carrying its figure,
/// and what runs past it turns to red dots; a target that is not a limit is an ink mark.
/// It replaces every gauge of the page.
struct RedLineCursor: View {
    let label: String
    let value: Double
    var unit: String = ""
    /// The rail's ends.
    let scale: ClosedRange<Double>
    /// What is planned or allowed, drawn as a dotted band.
    var band: ClosedRange<Double>?
    var limit: Double?
    var limitLabel: String?
    var target: Double?
    var targetLabel: String?
    var format: (Double) -> String = { $0.formatted(.number.precision(.fractionLength(0...1))) }

    private var crossed: Bool { limit.map { value > $0 } ?? false }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(label)
                    .font(.subheadline)
                    .foregroundStyle(Palette.muted)
                Spacer()
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(format(value))
                        .font(.figure(22))
                        .monospacedDigit()
                        .foregroundStyle(crossed ? Palette.over : Palette.ink)
                    if !unit.isEmpty {
                        Text(unit).font(.caption.weight(.semibold)).foregroundStyle(Palette.muted)
                    }
                }
            }
            rail
                .frame(height: 8)
                .padding(.top, 24)
                .padding(.bottom, limit != nil && target != nil && targetLabel != nil ? 22 : 6)
            HStack {
                Text(format(scale.lowerBound))
                Spacer()
                Text(format(scale.upperBound))
            }
            .font(.caption2)
            .monospacedDigit()
            .foregroundStyle(Palette.faint)
            .padding(.top, 6)
        }
        .accessibilityElement(children: .combine)
    }

    private var rail: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let x = { (value: Double) in width * (min(max(value, scale.lowerBound), scale.upperBound) - scale.lowerBound)
                / (scale.upperBound - scale.lowerBound) }
            let done = x(min(value, limit ?? value))
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.ghost).frame(width: width, height: 8)
                if let band {
                    DotPattern.planned
                        .frame(width: max(x(band.upperBound) - x(band.lowerBound), 0), height: 8)
                        .offset(x: x(band.lowerBound))
                }
                UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 4)
                    .fill(Palette.data)
                    .frame(width: done, height: 8)
                if crossed, let limit {
                    DotPattern.over
                        .frame(width: max(x(value) - x(limit), 0), height: 8)
                        .overlay(UnevenRoundedRectangle(bottomTrailingRadius: 4, topTrailingRadius: 4)
                            .strokeBorder(Palette.red.opacity(0.55), lineWidth: 1))
                        .clipShape(UnevenRoundedRectangle(bottomTrailingRadius: 4, topTrailingRadius: 4))
                        .offset(x: x(limit))
                }
                if let limit {
                    mark(at: x(limit), color: Palette.red, label: limitLabel, below: false)
                }
                if let target {
                    mark(at: x(target), color: Palette.ink, label: targetLabel, below: limit != nil)
                }
            }
            .frame(width: width, height: 8)
        }
    }

    private func mark(at x: CGFloat, color: Color, label: String?, below: Bool) -> some View {
        Rectangle()
            .fill(color)
            .frame(width: 2, height: 20)
            .overlay(alignment: below ? .bottom : .top) {
                if let label {
                    Text(label)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(color)
                        .fixedSize()
                        .offset(y: below ? 16 : -16)
                }
            }
            .offset(x: x - 1)
    }
}
