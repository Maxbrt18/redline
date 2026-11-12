import Charts
import RedlineKit
import SwiftUI

/// A watched zone, opened from Today's landmarks.
struct PainLink: Hashable {
    let zone: String
    let label: String
}

/// A watched zone's pain, on paper: the week's peak against the ceiling, one dot per rated
/// session and each wake-up reading on one scale, the athlete's own scale, the peak per
/// week, the wake-up readings left without a figure. Facts, no verdict.
struct PainScreen: View {
    let session: NotebookSession
    let link: PainLink
    @State private var zone: String?
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let shown = zone ?? link.zone
        NotebookScreen(title: LocalizedStringKey(link.label), session: session, model: ScreenModel(.injury(zone: shown)),
                       onTrack: false) { view in
            PainHero(view: view)
        } content: { view in
            VStack(alignment: .leading, spacing: 0) {
                if view.zones.count > 1 {
                    // Segments freeze at the accessibility sizes; a menu keeps the zone's whole name.
                    let picker = Picker("Zone", selection: Binding(get: { view.zone }, set: { zone = $0 })) {
                        ForEach(view.zones, id: \.zone) { Text($0.label).tag($0.zone) }
                    }
                    Group {
                        if typeSize.isAccessibilitySize {
                            picker.pickerStyle(.menu)
                        } else {
                            picker.pickerStyle(.segmented)
                        }
                    }
                    .padding(.bottom, Space.m)
                }
                PainContent(view: view)
            }
        }
        // Another zone is another screen's worth of data: it loads afresh.
        .id(shown)
    }
}

/// A peak above the ceiling, said in the red line and in words beside the figure.
private func crossed(_ peak: Double?, cap: Double) -> Bool { (peak ?? 0) > cap }

private struct PainHero: View {
    let view: Notebook.InjuryView

    var body: some View {
        let peak = view.weeks.last?.peak
        let over = crossed(peak, cap: view.cap)
        // The figure beside its words while both fit, above them otherwise; it never breaks.
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: Space.m) { figure(peak, over: over); words(over: over) }
            VStack(alignment: .leading, spacing: Space.xs) { figure(peak, over: over); words(over: over) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(peak.map {
            "Pic cette semaine : \(Numbers.text($0)), plafond \(Numbers.text(view.cap))" + (over ? ", au-dessus du plafond" : "")
        } ?? "Pas encore de douleur notée cette semaine, plafond \(Numbers.text(view.cap))")
    }

    private func figure(_ peak: Double?, over: Bool) -> some View {
        Text(peak.map { Numbers.text($0) } ?? "—")
            .figure(.hero, peak == nil ? .planned : .done)
            .foregroundStyle(peak == nil ? Palette.muted : over ? Palette.accent : Palette.ink)
    }

    private func words(over: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("pic cette semaine")
            Text("plafond \(Text(Numbers.text(view.cap)).foregroundStyle(Palette.ink).bold())")
            if over {
                Label("au-dessus du plafond", systemImage: "arrow.up.right")
                    .fontWeight(.semibold)
                    .foregroundStyle(Palette.accent)
            }
        }
        .textRole(.secondary)
        .foregroundStyle(Palette.muted)
    }
}

private struct PainContent: View {
    let view: Notebook.InjuryView

    var body: some View {
        VStack(alignment: .leading, spacing: Space.section) {
            VStack(alignment: .leading, spacing: 0) {
                PainChart(view: view)
                if !view.wakes.isEmpty {
                    Text("Le réveil d’un jour est placé juste avant la séance de ce jour.")
                        .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
                }
            }
            PainScale(steps: view.scale, cap: view.cap)
            PainWeeks(view: view)
            if !view.missing.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    SectionTitle(text: "À renseigner")
                    ForEach(view.missing, id: \.self) { date in
                        let day = Text("Réveil du \(Day(date)?.spelled(weekday: false) ?? date)").textRole(.secondary)
                        let note = Text("sans note chiffrée").textRole(.caption).foregroundStyle(Palette.muted)
                        ViewThatFits(in: .horizontal) {
                            HStack(alignment: .firstTextBaseline) { day; Spacer(minLength: Space.xs); note }
                            VStack(alignment: .leading, spacing: Space.xxs) { day; note }
                        }
                        .padding(.vertical, Space.s)
                    }
                }
            }
        }
        .padding(.bottom, Space.l)
    }
}

/// One dot per rated session (a long run hollow and larger), each wake-up reading as a
/// square just before its day's session, on a 0–3 scale cut by the ceiling. Past the
/// ceiling a dot becomes a red triangle pointing up, so its shape says it as well as its
/// colour; what is still to come is greyed. Named in place, no legend. A chart, it follows
/// the text size up to a cap so that its names never cover its marks.
private struct PainChart: View {
    let view: Notebook.InjuryView

    var body: some View {
        let days = Double(7 * view.weeks.count - 1)
        let today = offset(view.today)
        let lastLong = view.points.last(where: \.long)
        let lastWake = view.wakes.last
        Chart {
            if today + 0.5 < days + 0.5 {
                RectangleMark(xStart: .value("aujourd’hui", today + 0.5), xEnd: .value("fin", days + 0.5),
                              yStart: .value("bas", 0), yEnd: .value("haut", 3))
                    .foregroundStyle(Palette.ghost.opacity(0.45))
            }
            ForEach(1..<max(view.weeks.count, 1), id: \.self) { week in
                RuleMark(x: .value("semaine", Double(7 * week) - 0.5))
                    .foregroundStyle(Palette.grid)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 3]))
            }
            RuleMark(y: .value("plafond", min(view.cap, 3)))
                .foregroundStyle(Palette.accent)
                .lineStyle(StrokeStyle(lineWidth: 2))
                .annotation(position: .top, alignment: .trailing, spacing: 2) {
                    Text("plafond \(Numbers.text(view.cap))").font(.caption2.weight(.semibold)).foregroundStyle(Palette.accent)
                }
            ForEach(Array(view.points.enumerated()), id: \.offset) { _, point in
                let over = point.pain > view.cap
                PointMark(x: .value("jour", offset(point.date)), y: .value("douleur", min(point.pain, 3)))
                    .symbol {
                        switch (point.long, over) {
                        case (true, true):
                            UpTriangle().stroke(Palette.accent, style: StrokeStyle(lineWidth: 2.2, lineJoin: .round))
                                .background(UpTriangle().fill(Palette.bg))
                                .frame(width: 14, height: 13)
                        case (true, false):
                            Circle().strokeBorder(Palette.data, lineWidth: 2.4)
                                .background(Palette.bg, in: .circle)
                                .frame(width: 12, height: 12)
                        case (false, true):
                            UpTriangle().fill(Palette.accent).frame(width: 11, height: 10)
                        case (false, false):
                            Circle().fill(Palette.data).frame(width: 8.5, height: 8.5)
                        }
                    }
            }
            ForEach(Array(view.wakes.enumerated()), id: \.offset) { _, wake in
                PointMark(x: .value("jour", offset(wake.date) - 0.35), y: .value("douleur", min(wake.pain, 3)))
                    .symbol {
                        Rectangle().strokeBorder(Palette.muted, lineWidth: 1.6)
                            .background(Palette.bg)
                            .frame(width: 7, height: 7)
                    }
            }
        }
        // Named in place, beside their mark: the last long run and the last wake-up reading.
        .chartOverlay { proxy in
            GeometryReader { geometry in
                if let plot = proxy.plotFrame.map({ geometry[$0] }) {
                    if let long = lastLong, let at = proxy.position(for: (offset(long.date), min(long.pain, 3))) {
                        name("sortie longue", at: CGPoint(x: plot.minX + at.x + 10, y: plot.minY + at.y), edge: .leading)
                    }
                    if let wake = lastWake, let at = proxy.position(for: (offset(wake.date) - 0.35, min(wake.pain, 3))) {
                        name("réveil", at: CGPoint(x: plot.minX + at.x - 9, y: plot.minY + at.y), edge: .trailing)
                    }
                }
            }
        }
        .chartXScale(domain: -0.5...(days + 0.5))
        .chartYScale(domain: 0...3)
        .chartYAxis {
            AxisMarks(position: .leading, values: [0, 1, 2, 3]) { value in
                if let level = value.as(Double.self), level == 1 || level == 3 {
                    AxisGridLine().foregroundStyle(Palette.grid)
                }
                AxisValueLabel().foregroundStyle(Palette.muted)
            }
        }
        .chartXAxis {
            AxisMarks(values: view.weeks.indices.map { Double(7 * $0) + 3 }) { value in
                AxisValueLabel {
                    if let middle = value.as(Double.self) {
                        Text("W\(view.weeks[Int(middle) / 7].week.suffix(2))").foregroundStyle(Palette.muted)
                    }
                }
            }
        }
        .frame(height: 170)
        .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        .padding(.top, Space.xs)
        .accessibilityLabel("Douleur au pic de chaque séance sur \(view.weeks.count) semaines, plafond \(Numbers.text(view.cap))"
            + (view.points.contains { $0.pain > view.cap } ? ", dépassé au moins une fois" : ""))
    }

    private func name(_ text: String, at point: CGPoint, edge: HorizontalAlignment) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(Palette.muted)
            .fixedSize()
            .frame(width: 0, alignment: edge == .leading ? .leading : .trailing)
            .position(point)
    }

    /// Days since the first Monday shown.
    private func offset(_ date: String) -> Double {
        guard let start = Day(view.start), let day = Day(date) else { return 0 }
        return day.date.timeIntervalSince(start.date) / 86_400
    }
}

/// A triangle pointing up: the mark of a reading above the ceiling.
private nonisolated struct UpTriangle: Shape {
    func path(in rect: CGRect) -> Path {
        Path { path in
            path.move(to: CGPoint(x: rect.midX, y: rect.minY))
            path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
            path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
            path.closeSubpath()
        }
    }
}

/// The athlete's scale in their own words, cut by the ceiling's red stroke and its word:
/// what lies beyond it turns red.
private struct PainScale: View {
    let steps: [Notebook.ScaleStep]
    let cap: Double
    /// The figures' column follows the text size.
    @ScaledMetric(relativeTo: .body) private var column: CGFloat = 24

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionTitle(text: "Ton échelle")
            ForEach(steps, id: \.value) { step in
                HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                    Text(Numbers.text(step.value))
                        .figure(.inline)
                        .foregroundStyle(step.value > cap ? Palette.accent : Palette.ink)
                        .frame(minWidth: column, alignment: .leading)
                    Text(step.text).textRole(.secondary).foregroundStyle(Palette.muted)
                }
                if step.value == cap {
                    HStack(spacing: Space.xs) {
                        Capsule().fill(Palette.accent).frame(height: 2)
                        Text("plafond").font(TextRole.caption.font.weight(.semibold)).foregroundStyle(Palette.accent)
                    }
                }
            }
        }
    }
}

/// The peak, the rated sessions and the wake-up reading of each week, latest first: a
/// table while the text is small, one block per week at the accessibility sizes. A peak
/// above the ceiling carries an arrow beside its red figure.
private struct PainWeeks: View {
    let view: Notebook.InjuryView
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionTitle(text: "Par semaine")
            if typeSize.isAccessibilitySize { blocks } else { table }
        }
    }

    private var table: some View {
        Grid(alignment: .trailing, horizontalSpacing: Space.s, verticalSpacing: 0) {
            GridRow {
                Text("Semaine").gridColumnAlignment(.leading)
                Text("Pic")
                Text("Séances")
                Text("Réveil")
            }
            .textRole(.caption)
            .foregroundStyle(Palette.muted)
            .padding(.bottom, Space.xs)
            ForEach(view.weeks.reversed(), id: \.week) { week in
                GridRow(alignment: .firstTextBaseline) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Text("\(Int(week.week.suffix(2)) ?? 0)").figure(.inline)
                        if week.week == view.week { Text("en cours").textRole(.caption).foregroundStyle(Palette.muted) }
                    }
                    peak(week)
                    Text("\(week.n)")
                    Text(week.wake.map { Numbers.text($0) } ?? "—")
                }
                .textRole(.secondary)
                .padding(.vertical, Space.s)
                .accessibilityElement(children: .combine)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var blocks: some View {
        ForEach(view.weeks.reversed(), id: \.week) { week in
            VStack(alignment: .leading, spacing: Space.xxs) {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text("Semaine").textRole(.secondary).foregroundStyle(Palette.muted)
                    Text("\(Int(week.week.suffix(2)) ?? 0)").figure(.inline)
                    if week.week == view.week { Text("en cours").textRole(.caption).foregroundStyle(Palette.muted) }
                }
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text("Pic").foregroundStyle(Palette.muted)
                    peak(week)
                }
                Text("\(week.n) séance\(week.n > 1 ? "s" : "") · réveil \(week.wake.map { Numbers.text($0) } ?? "—")")
                    .foregroundStyle(Palette.muted)
            }
            .textRole(.secondary)
            .padding(.vertical, Space.s)
            .accessibilityElement(children: .combine)
        }
    }

    private func peak(_ week: Notebook.InjuryWeek) -> some View {
        let over = crossed(week.peak, cap: view.cap)
        return HStack(alignment: .firstTextBaseline, spacing: 2) {
            if over {
                Image(systemName: "arrow.up.right")
                    .font(TextRole.caption.font.weight(.bold))
                    .accessibilityLabel("au-dessus du plafond")
            }
            Text(week.peak.map { Numbers.text($0) } ?? "—").figure(.inline)
        }
        .foregroundStyle(over ? Palette.accent : Palette.ink)
    }
}
