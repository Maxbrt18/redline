import SwiftUI

/// Planned and done, one line per measure. Every limit sits at the same place on its rail,
/// so the limits read as one red stroke down the block; what passes it spills in red dots
/// and its figure turns `over`. A target that is not a limit is an ink tick.
struct LimitRows: View {
    struct Row: Identifiable {
        let label: String
        /// The rail's start; the mark sits at three quarters of it.
        var low: Double = 0
        var cap: Double?
        var target: Double?
        /// Where the dotted band starts, up to the mark.
        var band: Double?
        var fill: Double?
        let value: String
        let sub: String
        var id: String { label }

        var mark: Double { cap ?? target ?? 0 }
        var crossed: Bool { cap.map { cap in fill.map { $0 > cap } ?? false } ?? false }
    }

    let rows: [Row]
    private static let markAt = 0.75

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if rows.contains(where: { $0.cap != nil }) {
                GeometryReader { geometry in
                    Text("limite")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Palette.red)
                        .fixedSize()
                        .offset(x: geometry.size.width * Self.markAt - 16)
                }
                .frame(height: 14)
                .padding(.leading, 82)
                .padding(.trailing, 90)
            }
            ForEach(rows) { row in
                HStack(spacing: 12) {
                    Text(row.label)
                        .font(.subheadline)
                        .foregroundStyle(Palette.muted)
                        .frame(width: 70, alignment: .leading)
                    rail(row).frame(height: 8)
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(row.value)
                            .font(.figure(22))
                            .monospacedDigit()
                            .foregroundStyle(row.crossed ? Palette.over : Palette.ink)
                        Text(row.sub).font(.caption2).foregroundStyle(Palette.muted).lineLimit(1)
                    }
                    .frame(width: 78, alignment: .trailing)
                }
                .frame(minHeight: 46)
                .accessibilityElement(children: .combine)
            }
        }
    }

    private func rail(_ row: Row) -> some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let high = row.low + (row.mark - row.low) / Self.markAt
            let x = { (value: Double) in width * min(max((value - row.low) / (high - row.low), 0), 1) }
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.ghost).frame(width: width, height: 8)
                if let band = row.band {
                    DotPattern.planned
                        .frame(width: max(width * Self.markAt - x(band), 0), height: 8)
                        .offset(x: x(band))
                }
                if let fill = row.fill {
                    UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 4)
                        .fill(Palette.data)
                        .frame(width: row.crossed ? width * Self.markAt : x(fill), height: 8)
                    if row.crossed {
                        DotPattern.over
                            .frame(width: max(x(fill) - width * Self.markAt, 0), height: 8)
                            .offset(x: width * Self.markAt)
                    }
                }
                Rectangle()
                    .fill(row.cap != nil ? Palette.red : Palette.ink)
                    .frame(width: 2, height: row.cap != nil ? 46 : 20)
                    .offset(x: width * Self.markAt - 1)
            }
            .frame(width: width, height: 8)
        }
    }
}
