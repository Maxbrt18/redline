import RedlineKit
import SwiftUI

/// The course against its altitude, the aid stations named on it. In the track's head it
/// is small and white; full size, on paper, it carries an altitude scale, the main climbs
/// numbered at their summit, and under it the kilometre and the climb of each stretch.
/// Its labels follow the text size up to a cap, and the room kept for them grows with them;
/// the curve keeps its height.
struct RaceProfile: View {
    let plan: Notebook.RacePlan
    var full = false

    var body: some View {
        ProfileDrawing(plan: plan, full: full)
            .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
    }
}

/// The drawing itself, read under the text size cap so that its room and its labels grow
/// together.
private struct ProfileDrawing: View {
    let plan: Notebook.RacePlan
    let full: Bool
    @Environment(\.onTrack) private var onTrack
    /// How much the labels have grown from the default text size.
    @ScaledMetric(relativeTo: .caption2) private var grow: CGFloat = 1

    var body: some View {
        let layout = { (width: CGFloat) in ProfileFrame(plan: plan, width: width, full: full, grow: grow) }
        GeometryReader { geometry in
            let frame = layout(geometry.size.width)
            ZStack(alignment: .topLeading) {
                ForEach(frame.scale, id: \.self) { metres in
                    frame.rule(at: frame.y(metres)).stroke(grid, lineWidth: 0.5)
                }
                DotPattern(color: onTrack ? Palette.onTrack.opacity(0.75) : Palette.accent.opacity(0.7), spacing: 4, radius: 1.05)
                    .mask(frame.area)
                if full {
                    ForEach(Array(plan.mainClimbs.enumerated()), id: \.offset) { _, climb in
                        frame.climb(climb).fill(Palette.data.opacity(0.26))
                    }
                }
                frame.line.stroke(ink, style: StrokeStyle(lineWidth: 1.6, lineJoin: .round))
                ForEach(frame.scale, id: \.self) { metres in
                    anchored("\(Int(metres)) m", x: frame.width, y: frame.y(metres) - 8 * grow, edge: .trailing)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(quiet)
                }
                ForEach(Array(frame.aids.enumerated()), id: \.offset) { _, aid in
                    Path { $0.move(to: CGPoint(x: aid.x, y: aid.labelY + 6 * grow)); $0.addLine(to: CGPoint(x: aid.x, y: frame.base)) }
                        .stroke(onTrack ? Palette.onTrack.opacity(0.7) : Palette.faint, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    anchored(aid.name, x: aid.x, y: aid.labelY, edge: aid.edge)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(ink)
                }
                frame.rule(at: frame.base).stroke(grid, lineWidth: 0.5)
                if full { fullMarks(frame) }
            }
        }
        .frame(height: layout(0).height)
        .accessibilityElement()
        .accessibilityLabel("Profil du parcours : \(Numbers.text(plan.lengthKm)) km, \(Int(plan.dplusM)) m de dénivelé positif, \(plan.passages.count - 1) ravitaillements")
    }

    @ViewBuilder private func fullMarks(_ frame: ProfileFrame) -> some View {
        ForEach(Array(frame.summits.enumerated()), id: \.offset) { index, summit in
            Text("\(index + 1)")
                .font(.caption2.weight(.bold))
                .monospacedDigit()
                .foregroundStyle(ink)
                .padding(.horizontal, 3)
                .background(Palette.bg.opacity(0.85), in: .capsule)
                .position(summit)
        }
        let marks = [0] + plan.stretches.map(\.toKm)
        ForEach(Array(marks.enumerated()), id: \.offset) { index, km in
            anchored("km \(Numbers.text(km, digits: 0))", x: frame.x(km), y: frame.base + 10 * grow,
                     edge: index == 0 ? .leading : index == marks.count - 1 ? .trailing : .center)
                .font(.caption2.weight(.medium))
                .foregroundStyle(quiet)
        }
        ForEach(Array(plan.stretches.enumerated()), id: \.offset) { index, stretch in
            anchored("+\(Int(stretch.dplusM)) −\(Int(stretch.dminusM))", x: (frame.x(marks[index]) + frame.x(stretch.toKm)) / 2,
                     y: frame.base + 27 * grow, edge: .center)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Palette.muted)
        }
        if plan.highest.count == 2 {
            let point = CGPoint(x: frame.x(plan.highest[1]), y: frame.y(plan.highest[0]))
            Circle().fill(ink).frame(width: 6, height: 6).position(point)
            anchored("\(Int(plan.highest[0])) m", x: point.x + 6, y: point.y + 1, edge: .leading)
                .font(.caption2.weight(.bold))
                .foregroundStyle(ink)
        }
    }

    /// A label placed by one edge (or its middle) at `x`, centred on `y`.
    private func anchored(_ text: String, x: CGFloat, y: CGFloat, edge: HorizontalAlignment) -> some View {
        Text(text)
            .fixedSize()
            .frame(width: 0, alignment: edge == .leading ? .leading : edge == .trailing ? .trailing : .center)
            .position(x: x, y: y)
    }

    private var ink: Color { onTrack ? Palette.onTrack : Palette.ink }
    /// The labels of the scale and the kilometres: second rank, still readable as text.
    private var quiet: Color { onTrack ? Palette.onTrack.opacity(0.8) : Palette.muted }
    private var grid: Color { onTrack ? Palette.onTrack.opacity(0.3) : Palette.grid }
}

/// The profile's geometry: kilometres across, metres up, rounded to the hundred below and
/// above. The curve keeps a fixed height; the room above it (aid stations) and under it
/// (kilometres, stretches) grows with the labels.
private struct ProfileFrame {
    let plan: Notebook.RacePlan
    let width: CGFloat
    let floor: Double, ceiling: Double
    let top: CGFloat, base: CGFloat, height: CGFloat
    let full: Bool
    let grow: CGFloat

    init(plan: Notebook.RacePlan, width: CGFloat, full: Bool, grow: CGFloat) {
        self.plan = plan
        self.full = full
        self.width = width
        self.grow = grow
        let heights = plan.profile.map { $0[1] }
        floor = ((heights.min() ?? 0) / 100).rounded(.down) * 100
        ceiling = ((heights.max() ?? 100) / 100).rounded(.up) * 100 + 50
        top = (full ? 48 : 36) * grow
        base = top + (full ? 124 : 60)
        height = base + (full ? 42 : 20) * grow
    }

    func x(_ km: Double) -> CGFloat { CGFloat(km / max(plan.lengthKm, 0.1)) * width }
    func y(_ metres: Double) -> CGFloat { base - (base - top) * CGFloat((metres - floor) / (ceiling - floor)) }

    /// The altitudes of the scale, full size only: every 100 m, or 300 m on a tall course.
    var scale: [Double] {
        guard full else { return [] }
        let step = ceiling - floor > 600 ? 300.0 : 100.0
        return stride(from: (floor / step).rounded(.up) * step, to: ceiling, by: step).filter { $0 != floor }
    }

    func rule(at y: CGFloat) -> Path {
        Path { $0.move(to: CGPoint(x: 0, y: y)); $0.addLine(to: CGPoint(x: width, y: y)) }
    }

    private func points(_ profile: [[Double]]) -> [CGPoint] {
        profile.map { CGPoint(x: x($0[0]), y: y($0[1])) }
    }

    var line: Path {
        Path { $0.addLines(points(plan.profile)) }
    }

    var area: Path {
        var path = line
        path.addLine(to: CGPoint(x: width, y: base))
        path.addLine(to: CGPoint(x: 0, y: base))
        path.closeSubpath()
        return path
    }

    private func inside(_ climb: Notebook.RouteClimb) -> [[Double]] {
        [[climb.startKm, plan.height(atKm: climb.startKm)]]
            + plan.profile.filter { $0[0] > climb.startKm && $0[0] < climb.endKm }
            + [[climb.endKm, plan.height(atKm: climb.endKm)]]
    }

    func climb(_ climb: Notebook.RouteClimb) -> Path {
        let shape = points(inside(climb))
        return Path { path in
            path.addLines(shape)
            path.addLine(to: CGPoint(x: shape.last!.x, y: base))
            path.addLine(to: CGPoint(x: shape.first!.x, y: base))
            path.closeSubpath()
        }
    }

    /// Where each main climb's number sits: above its summit; two close summits stack
    /// their numbers instead of overlapping.
    var summits: [CGPoint] {
        var previous = CGPoint(x: -1e9, y: 0)
        return plan.mainClimbs.map { climb in
            let top = inside(climb).max { $0[1] < $1[1] } ?? [climb.startKm, 0]
            var at = CGPoint(x: x(top[0]), y: y(top[1]) - 10 * grow)
            if at.x - previous.x < 16 * grow { at.y = min(at.y, previous.y - 12 * grow) }
            previous = at
            return at
        }
    }

    /// The aid stations: close ones take turns on two rows; the first and last keep inside
    /// the frame.
    var aids: [(name: String, x: CGFloat, labelY: CGFloat, edge: HorizontalAlignment)] {
        var last = -1e9 as CGFloat, row = 0
        return plan.passages.filter { !$0.name.isEmpty }.map { passage in
            let at = x(passage.km)
            row = at - last < 70 * grow ? 1 - row : 0
            last = at
            let edge: HorizontalAlignment = at < 30 * grow ? .leading : at > width - 30 * grow ? .trailing : .center
            return (passage.name, at, top - (20 + CGFloat(row) * 14) * grow, edge)
        }
    }
}
