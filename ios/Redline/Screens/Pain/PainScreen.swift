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

    var body: some View {
        let shown = zone ?? link.zone
        NotebookScreen(title: LocalizedStringKey(link.label), session: session, model: ScreenModel(.injury(zone: shown)),
                       onTrack: false) { view in
            PainHero(view: view)
        } content: { view in
            VStack(alignment: .leading, spacing: 0) {
                if view.zones.count > 1 {
                    Picker("Zone", selection: Binding(get: { view.zone }, set: { zone = $0 })) {
                        ForEach(view.zones, id: \.zone) { Text($0.label).tag($0.zone) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.bottom, 18)
                }
                PainContent(view: view)
            }
        }
        // Another zone is another screen's worth of data: it loads afresh.
        .id(shown)
    }
}

private struct PainHero: View {
    let view: Notebook.InjuryView

    var body: some View {
        let peak = view.weeks.last?.peak
        HStack(alignment: .center, spacing: 14) {
            Text(peak.map { Numbers.text($0) } ?? "—")
                .font(.figure(96))
                .foregroundStyle((peak ?? 0) > view.cap ? Palette.over : Palette.ink)
            VStack(alignment: .leading, spacing: 2) {
                Text("pic cette semaine")
                Text("plafond ") + Text(Numbers.text(view.cap)).foregroundStyle(Palette.ink).bold()
            }
            .font(.subheadline)
            .foregroundStyle(Palette.muted)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(peak.map { "Pic cette semaine : \(Numbers.text($0)), plafond \(Numbers.text(view.cap))" }
            ?? "Pas encore de douleur notée cette semaine, plafond \(Numbers.text(view.cap))")
    }
}

private struct PainContent: View {
    let view: Notebook.InjuryView

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PainChart(view: view)
            if !view.wakes.isEmpty {
                Text("Le réveil d’un jour est placé juste avant la séance de ce jour.")
                    .font(.footnote).foregroundStyle(Palette.muted).padding(.top, 8)
            }
            PainScale(steps: view.scale, cap: view.cap)
            PainWeeks(view: view)
            if !view.missing.isEmpty {
                SectionTitle(text: "À renseigner")
                ForEach(view.missing, id: \.self) { date in
                    HStack {
                        Text("Réveil du \(Day(date)?.spelled(weekday: false) ?? date)")
                        Spacer()
                        Text("sans note chiffrée").font(.footnote).foregroundStyle(Palette.muted)
                    }
                    .font(.subheadline)
                    .padding(.vertical, 12)
                }
            }
        }
        .padding(.bottom, 24)
    }
}

/// One dot per rated session (a long run hollow and larger), each wake-up reading as a
/// square just before its day's session, on a 0–3 scale cut by the ceiling. Past the
/// ceiling a dot turns red; what is still to come is greyed. Named in place, no legend.
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
                .foregroundStyle(Palette.red)
                .lineStyle(StrokeStyle(lineWidth: 2))
                .annotation(position: .top, alignment: .trailing, spacing: 2) {
                    Text("plafond \(Numbers.text(view.cap))").font(.caption2.weight(.semibold)).foregroundStyle(Palette.red)
                }
            ForEach(Array(view.points.enumerated()), id: \.offset) { _, point in
                let over = point.pain > view.cap
                PointMark(x: .value("jour", offset(point.date)), y: .value("douleur", min(point.pain, 3)))
                    .symbol {
                        if point.long {
                            Circle().strokeBorder(over ? Palette.over : Palette.data, lineWidth: 2.4)
                                .background(Palette.bg, in: .circle)
                                .frame(width: 12, height: 12)
                        } else {
                            Circle().fill(over ? Palette.over : Palette.data).frame(width: 8.5, height: 8.5)
                        }
                    }
            }
            ForEach(Array(view.wakes.enumerated()), id: \.offset) { _, wake in
                PointMark(x: .value("jour", offset(wake.date) - 0.35), y: .value("douleur", min(wake.pain, 3)))
                    .symbol {
                        RoundedRectangle(cornerRadius: 1.5).strokeBorder(Palette.muted, lineWidth: 1.6)
                            .background(Palette.bg, in: .rect(cornerRadius: 1.5))
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
                AxisValueLabel()
            }
        }
        .chartXAxis {
            AxisMarks(values: view.weeks.indices.map { Double(7 * $0) + 3 }) { value in
                AxisValueLabel {
                    if let middle = value.as(Double.self) {
                        Text("W\(view.weeks[Int(middle) / 7].week.suffix(2))")
                    }
                }
            }
        }
        .frame(height: 170)
        .padding(.top, 6)
        .accessibilityLabel("Douleur au pic de chaque séance sur \(view.weeks.count) semaines, plafond \(Numbers.text(view.cap))")
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

/// The athlete's scale in their own words, cut by the ceiling's red stroke: what lies
/// beyond it turns red.
private struct PainScale: View {
    let steps: [Notebook.ScaleStep]
    let cap: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Ton échelle")
            ForEach(steps, id: \.value) { step in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(Numbers.text(step.value))
                        .font(.figure(18))
                        .foregroundStyle(step.value > cap ? Palette.over : Palette.ink)
                        .frame(width: 22, alignment: .leading)
                    Text(step.text).font(.subheadline).foregroundStyle(Palette.muted)
                }
                if step.value == cap {
                    HStack(spacing: 8) {
                        Capsule().fill(Palette.red).frame(height: 2)
                        Text("plafond").font(.caption.weight(.semibold)).foregroundStyle(Palette.red)
                    }
                }
            }
        }
    }
}

/// The peak, the rated sessions and the wake-up reading of each week, latest first.
private struct PainWeeks: View {
    let view: Notebook.InjuryView

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle(text: "Par semaine")
            table
        }
    }

    private var table: some View {
        Grid(alignment: .trailing, horizontalSpacing: 12, verticalSpacing: 0) {
            GridRow {
                Text("Semaine").gridColumnAlignment(.leading)
                Text("Pic")
                Text("Séances")
                Text("Réveil")
            }
            .font(.caption)
            .foregroundStyle(Palette.faint)
            .padding(.bottom, 6)
            ForEach(view.weeks.reversed(), id: \.week) { week in
                GridRow(alignment: .firstTextBaseline) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("\(Int(week.week.suffix(2)) ?? 0)").font(.figure(18))
                        if week.week == view.week { Text("en cours").font(.caption).foregroundStyle(Palette.muted) }
                    }
                    Text(week.peak.map { Numbers.text($0) } ?? "—")
                        .font(.figure(18))
                        .foregroundStyle((week.peak ?? 0) > view.cap ? Palette.over : Palette.ink)
                    Text("\(week.n)")
                    Text(week.wake.map { Numbers.text($0) } ?? "—")
                }
                .font(.subheadline)
                .padding(.vertical, 12)
                .accessibilityElement(children: .combine)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
