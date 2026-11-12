import Charts
import RedlineKit
import SwiftUI

/// Every closed week, from the Week tab.
struct AllWeeks: Hashable {}

/// The last closed weeks: a chart of the kilometres done in front of the target, then one
/// line per week.
struct PastWeeks: View {
    let session: NotebookSession
    @State private var model = ScreenModel(.weeks)

    var body: some View {
        let weeks = model.value?.weeks ?? []
        // A stack that always exists: a `.task` on an empty group would never start.
        VStack(alignment: .leading, spacing: 0) {
            if !weeks.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    SectionTitle(text: "Semaines passées")
                    PastWeeksChart(weeks: Array(weeks.prefix(5).reversed())).padding(.vertical, Space.s)
                    ForEach(weeks.prefix(4), id: \.week) { week in
                        NavigationLink(value: WeekLink(label: week.week)) { PastWeekLine(week: week) }
                            .buttonStyle(.plain)
                    }
                    if weeks.count > 4 {
                        NavigationLink(value: AllWeeks()) {
                            WeekLine(title: "Toutes les semaines", detail: "\(weeks.count) semaines au registre", figure: "")
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .task { await model.load(with: session) }
    }
}

struct PastWeekLine: View {
    let week: Notebook.PastWeek

    var body: some View {
        WeekLine(title: "Semaine \(Int(week.week.suffix(2)) ?? 0)", detail: weekSpan(monday: week.monday),
                 figure: Numbers.text(week.kmDone) + (week.kmTarget.map { " / \(Numbers.text($0))" } ?? "") + " km",
                 progress: week.kmTarget.map { week.kmDone / $0 })
    }
}

/// Kilometres done, solid, in front of the target, wide and pale: one pair per week,
/// overlaid rather than stacked.
private struct PastWeeksChart: View {
    let weeks: [Notebook.PastWeek]

    var body: some View {
        Chart(weeks, id: \.week) { week in
            if let target = week.kmTarget {
                BarMark(x: .value("Semaine", "W\(week.week.suffix(2))"), y: .value("Visé", target), width: 32,
                        stacking: .unstacked)
                    .foregroundStyle(Palette.ghost)
                    .cornerRadius(Radius.mark)
            }
            BarMark(x: .value("Semaine", "W\(week.week.suffix(2))"), y: .value("Fait", week.kmDone), width: 14,
                    stacking: .unstacked)
                .foregroundStyle(Palette.data)
                .cornerRadius(Radius.mark / 2)
                .annotation(position: .top, spacing: 4) {
                    Text(Numbers.text(week.kmDone)).figure(.inline).foregroundStyle(Palette.ink)
                }
        }
        .chartYAxis(.hidden)
        .chartXAxis {
            AxisMarks { value in
                AxisValueLabel {
                    if let name = value.as(String.self) {
                        let target = weeks.first { "W\($0.week.suffix(2))" == name }?.kmTarget
                        VStack(spacing: 2) {
                            Text(target.map { "~\(Numbers.text($0))" } ?? " ").font(.caption2).foregroundStyle(Palette.muted)
                            Text(name).font(.caption2.weight(.semibold)).foregroundStyle(Palette.muted)
                        }
                    }
                }
            }
        }
        .frame(height: 150)
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
        .accessibilityLabel("Kilomètres faits devant les kilomètres visés, semaine par semaine")
    }
}

/// Every closed week, newest first.
struct AllWeeksScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: "Semaines passées", session: session, model: ScreenModel(.weeks), onTrack: false) { _ in
            EmptyView()
        } content: { weeks in
            if weeks.weeks.isEmpty {
                Text("Aucune semaine close au registre.").textRole(.secondary).foregroundStyle(Palette.muted)
            } else {
                VStack(spacing: 0) {
                    ForEach(weeks.weeks, id: \.week) { week in
                        NavigationLink(value: WeekLink(label: week.week)) { PastWeekLine(week: week) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}
