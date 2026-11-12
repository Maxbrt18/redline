import SwiftUI

/// Planned and done, one line per measure. Every limit sits at the same place on its rail,
/// so the limits read as one red stroke down the block; what passes it spills in red dots
/// and its figure turns to the red line. A target that is not a limit is an ink tick. At the
/// accessibility sizes the label and the figure go above the rail, which takes the full width.
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
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .subheadline) private var labelWidth: CGFloat = 70
    @ScaledMetric(relativeTo: .title3) private var valueWidth: CGFloat = 78

    var body: some View {
        let stacked = typeSize.isAccessibilitySize
        VStack(alignment: .leading, spacing: stacked ? Space.s : 0) {
            if rows.contains(where: { $0.cap != nil }), stacked {
                // Stacked rows put words between the rails: the legend goes above them all.
                Text("le trait rouge marque la limite")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Palette.accent)
            } else if rows.contains(where: { $0.cap != nil }) {
                GeometryReader { geometry in
                    Text("limite")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Palette.accent)
                        .fixedSize()
                        .offset(x: geometry.size.width * Self.markAt - 16)
                }
                .frame(height: 14)
                .padding(.leading, labelWidth + Space.s)
                .padding(.trailing, valueWidth + Space.s)
            }
            ForEach(rows) { row in
                Group {
                    if stacked {
                        VStack(alignment: .leading, spacing: Space.xs) {
                            HStack(alignment: .firstTextBaseline) {
                                label(row)
                                Spacer(minLength: Space.xs)
                                figure(row, alignment: .trailing)
                            }
                            rail(row).frame(height: 8).padding(.vertical, Space.s)
                        }
                    } else {
                        HStack(spacing: Space.s) {
                            label(row).frame(width: labelWidth, alignment: .leading)
                            rail(row).frame(height: 8)
                            figure(row, alignment: .trailing).frame(width: valueWidth, alignment: .trailing)
                        }
                        .frame(minHeight: 46)
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
    }

    private func label(_ row: Row) -> some View {
        Text(row.label).textRole(.secondary).foregroundStyle(Palette.muted)
    }

    private func figure(_ row: Row, alignment: HorizontalAlignment) -> some View {
        VStack(alignment: alignment, spacing: 2) {
            // A crossed limit shows as a symbol before the figure, not by the red alone.
            HStack(alignment: .firstTextBaseline, spacing: Space.xxs) {
                if row.crossed {
                    Image(systemName: "arrow.up.right").font(.caption.weight(.bold))
                        .accessibilityLabel("au-delà de la limite")
                }
                Text(row.value).figure(.medium)
            }
            .foregroundStyle(row.crossed ? Palette.accent : Palette.ink)
            Text(row.sub).font(.caption2).foregroundStyle(Palette.muted).lineLimit(1)
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
                    .fill(row.cap != nil ? Palette.accent : Palette.ink)
                    .frame(width: 2, height: row.cap != nil && !typeSize.isAccessibilitySize ? 46 : 20)
                    .offset(x: width * Self.markAt - 1)
            }
            .frame(width: width, height: 8)
        }
    }
}
