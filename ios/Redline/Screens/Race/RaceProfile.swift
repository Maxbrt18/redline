import RedlineKit
import SwiftUI

/// The course against its altitude, the aid stations named on it. In the track's head it
/// is small and white; full size, on paper, it carries an altitude scale, the main climbs
/// numbered at their summit, and under it the kilometre and the climb of each stretch.
struct RaceProfile: View {
    let plan: Notebook.RacePlan
    var full = false
    @Environment(\.onTrack) private var onTrack

    var body: some View {
        let height: CGFloat = full ? 214 : 116
        GeometryReader { geometry in
            let frame = ProfileFrame(plan: plan, width: geometry.size.width, height: height, full: full)
            ZStack(alignment: .topLeading) {
                ForEach(frame.scale, id: \.self) { metres in
                    frame.rule(at: frame.y(metres)).stroke(grid, lineWidth: 0.5)
                }
                DotPattern(color: onTrack ? Palette.onTrack.opacity(0.75) : Palette.red.opacity(0.7), spacing: 4, radius: 1.05)
                    .mask(frame.area)
                if full {
                    ForEach(Array(plan.mainClimbs.enumerated()), id: \.offset) { _, climb in
                        frame.climb(climb).fill(Palette.data.opacity(0.26))
                    }
                }
                frame.line.stroke(ink, style: StrokeStyle(lineWidth: 1.6, lineJoin: .round))
                ForEach(frame.scale, id: \.self) { metres in
                    anchored("\(Int(metres)) m", x: frame.width, y: frame.y(metres) - 8, edge: .trailing)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(faint)
                }
                ForEach(Array(frame.aids.enumerated()), id: \.offset) { _, aid in
                    Path { $0.move(to: CGPoint(x: aid.x, y: aid.labelY + 6)); $0.addLine(to: CGPoint(x: aid.x, y: frame.base)) }
                        .stroke(onTrack ? Palette.onTrack.opacity(0.7) : Palette.faint, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    anchored(aid.name, x: aid.x, y: aid.labelY, edge: aid.edge)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(ink)
                }
                frame.rule(at: frame.base).stroke(grid, lineWidth: 0.5)
                if full { fullMarks(frame) }
            }
        }
        .frame(height: height)
        .accessibilityElement()
        .accessibilityLabel("Profil du parcours : \(Numbers.text(plan.lengthKm)) km, \(Int(plan.dplusM)) m de dénivelé positif, \(plan.passages.count - 1) ravitaillements")
    }

    @ViewBuilder private func fullMarks(_ frame: ProfileFrame) -> some View {
        ForEach(Array(frame.summits.enumerated()), id: \.offset) { index, summit in
            Text("\(index + 1)")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(Palette.hRace)
                .padding(.horizontal, 3)
                .background(Palette.bg.opacity(0.85), in: .capsule)
                .position(summit)
        }
        let marks = [0] + plan.stretches.map(\.toKm)
        ForEach(Array(marks.enumerated()), id: \.offset) { index, km in
            anchored("km \(Numbers.text(km, digits: 0))", x: frame.x(km), y: frame.base + 10,
                     edge: index == 0 ? .leading : index == marks.count - 1 ? .trailing : .center)
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(faint)
        }
        ForEach(Array(plan.stretches.enumerated()), id: \.offset) { index, stretch in
            anchored("+\(Int(stretch.dplusM)) −\(Int(stretch.dminusM))", x: (frame.x(marks[index]) + frame.x(stretch.toKm)) / 2,
                     y: frame.base + 27, edge: .center)
                .font(.system(size: 10.5, weight: .semibold))
                .foregroundStyle(Palette.muted)
        }
        if plan.highest.count == 2 {
            let point = CGPoint(x: frame.x(plan.highest[1]), y: frame.y(plan.highest[0]))
            Circle().fill(ink).frame(width: 6, height: 6).position(point)
            anchored("\(Int(plan.highest[0])) m", x: point.x + 6, y: point.y + 1, edge: .leading)
                .font(.system(size: 11, weight: .bold))
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
    private var faint: Color { onTrack ? Palette.onTrack.opacity(0.8) : Palette.faint }
    private var grid: Color { onTrack ? Palette.onTrack.opacity(0.3) : Palette.grid }
}

/// The profile's geometry: kilometres across, metres up, rounded to the hundred below and
/// above.
private struct ProfileFrame {
    let plan: Notebook.RacePlan
    let width: CGFloat
    let floor: Double, ceiling: Double
    let top: CGFloat, base: CGFloat
    let full: Bool

    init(plan: Notebook.RacePlan, width: CGFloat, height: CGFloat, full: Bool) {
        self.plan = plan
        self.full = full
        self.width = width
        let heights = plan.profile.map { $0[1] }
        floor = ((heights.min() ?? 0) / 100).rounded(.down) * 100
        ceiling = ((heights.max() ?? 100) / 100).rounded(.up) * 100 + 50
        top = full ? 48 : 36
        base = height - (full ? 42 : 20)
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
            var at = CGPoint(x: x(top[0]), y: y(top[1]) - 10)
            if at.x - previous.x < 16 { at.y = min(at.y, previous.y - 12) }
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
            row = at - last < 70 ? 1 - row : 0
            last = at
            let edge: HorizontalAlignment = at < 30 ? .leading : at > width - 30 ? .trailing : .center
            return (passage.name, at, top - 20 - CGFloat(row) * 14, edge)
        }
    }
}
