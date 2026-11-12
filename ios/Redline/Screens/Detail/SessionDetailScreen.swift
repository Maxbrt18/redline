import RedlineKit
import SwiftUI

/// A session to open, from Today or Week, by its observation.
struct SessionLink: Hashable {
    let id: String
    let title: String
}

/// A run read in full, on paper: what it was, how it felt, planned against done, its
/// curves and splits, its zones, the previous one of its kind, its conditions and its week.
/// Everything is computed by the service; here it is only laid out.
struct SessionDetailScreen: View {
    let session: NotebookSession
    let link: SessionLink

    var body: some View {
        NotebookScreen(title: LocalizedStringKey(link.title), session: session, model: ScreenModel(.session(link.id)),
                       onTrack: false) { detail in
            DetailHero(detail: detail)
        } content: { detail in
            VStack(alignment: .leading, spacing: 0) {
                DetailFacts(detail: detail)
                DetailFeelings(detail: detail)
                DetailPlanSection(detail: detail)
                DetailWork(detail: detail)
                DetailZones(detail: detail)
                DetailPrevious(detail: detail)
                DetailConditions(detail: detail)
                DetailWeek(detail: detail)
                if let id = detail.stravaId, let url = URL(string: "https://www.strava.com/activities/\(id)") {
                    Link(destination: url) {
                        Label("Ouvrir dans Strava", systemImage: "arrow.up.right.square")
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.vertical, 16)
                }
            }
            .padding(.bottom, 24)
        }
    }
}

/// The zone of a heart rate on the instance's bounds (1 to 5).
func heartZone(_ heart: Double, bounds: [Int]) -> Int {
    1 + bounds.filter { heart >= Double($0) }.count
}

func zoneColor(_ zone: Int) -> Color {
    [Palette.z1, Palette.z2, Palette.z3, Palette.z4, Palette.z5][min(max(zone, 1), 5) - 1]
}

/// `Z3` on its zone's colour.
struct ZoneChip: View {
    let zone: Int

    var body: some View {
        Text("Z\(zone)")
            .font(.caption2.weight(.bold))
            .foregroundStyle(zone >= 4 ? Color.white : Palette.ink)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(zoneColor(zone), in: .rect(cornerRadius: 4))
    }
}

/// A section's head: its title on a rule, a few words at its right.
struct DetailHead: View {
    let title: String
    var aside: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.title3.bold()).accessibilityAddTraits(.isHeader)
                Spacer()
                if let aside { Text(aside).font(.caption).foregroundStyle(Palette.muted) }
            }
        }
        .padding(.top, 30)
        .padding(.bottom, 10)
    }
}

/// A figure with its label above and a line under it.
struct DetailTile<Sub: View>: View {
    let label: String
    var symbol: String?
    let value: String
    var suffix: String?
    var pending = false
    @ViewBuilder var sub: Sub

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                if let symbol { Image(systemName: symbol).font(.caption2) }
                Text(label)
            }
            .font(.caption)
            .foregroundStyle(Palette.muted)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value).font(.figure(26)).foregroundStyle(pending ? Palette.faint : Palette.ink)
                if let suffix { Text(suffix).font(.caption).foregroundStyle(Palette.muted) }
            }
            sub.font(.caption).foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 12)
    }
}

extension DetailTile where Sub == EmptyView {
    init(label: String, symbol: String? = nil, value: String, suffix: String? = nil, pending: Bool = false) {
        self.init(label: label, symbol: symbol, value: value, suffix: suffix, pending: pending) { EmptyView() }
    }
}

/// The head: the session's family, its distance as large as it gets, its time and pace,
/// then its course as a strip (phases, or one block per kilometre in its zone).
private struct DetailHero: View {
    let detail: Notebook.SessionView

    var body: some View {
        let fact = detail.fact
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                FamilyBadge(family: Family(kind: detail.kind, workout: detail.workout), small: true)
                Text(family).font(.footnote.weight(.semibold)).foregroundStyle(Palette.muted)
            }
            if let km = fact.km {
                HStack(alignment: .lastTextBaseline, spacing: 6) {
                    Text(Numbers.text(km, digits: 2)).font(.figure(80))
                    Text("km").font(.title3).foregroundStyle(Palette.muted)
                }
            }
            HStack(spacing: 28) {
                if let moving = fact.movingS {
                    pair(Numbers.duration(moving), "temps")
                }
                if let km = fact.km, km > 0, let moving = fact.movingS {
                    pair(Numbers.pace(moving / km), "allure moyenne", suffix: "/km")
                }
            }
            CourseStrip(detail: detail)
        }
    }

    private var family: String {
        if detail.quality { return "Course · séance de qualité" }
        if detail.kind == "long" || detail.workout == "long" { return "Course · sortie longue" }
        if detail.kind == "easy" { return "Course · endurance fondamentale" }
        return Notebook.kindName(detail.kind)
    }

    private func pair(_ value: String, _ label: String, suffix: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value).font(.figure(28)).monospacedDigit()
                if let suffix { Text(suffix).font(.caption).foregroundStyle(Palette.muted) }
            }
            Text(label).font(.caption).foregroundStyle(Palette.muted)
        }
    }
}

/// The course in one strip: warm-up, repetitions in red, recoveries, cool-down; or, for a
/// steady run, one block per kilometre in the colour of its zone.
private struct CourseStrip: View {
    let detail: Notebook.SessionView

    var body: some View {
        if let phases = detail.phases, !phases.isEmpty {
            let total = max(phases.reduce(0) { $0 + $1.timeS }, 1)
            let reps = detail.reps ?? []
            VStack(alignment: .leading, spacing: 6) {
                GeometryReader { geometry in
                    HStack(spacing: 1) {
                        ForEach(Array(phases.enumerated()), id: \.offset) { _, phase in
                            Rectangle()
                                .fill(phase.kind == .rep ? Palette.red : phase.kind == .recovery ? Palette.ghost : Palette.data.opacity(0.35))
                                .frame(width: max(geometry.size.width * phase.timeS / total - 1, 1))
                        }
                    }
                }
                .frame(height: 14)
                .clipShape(.rect(cornerRadius: 3))
                HStack {
                    if let warm = phases.first(where: { $0.kind == .warmup }) {
                        Text("échauffement \(Numbers.minutes(warm.timeS))")
                    }
                    Spacer()
                    if !reps.isEmpty {
                        Text("\(reps.count) × \(Numbers.minutes(reps.reduce(0) { $0 + $1.timeS } / Double(reps.count)))")
                            .foregroundStyle(Palette.red)
                    }
                    Spacer()
                    if let cool = phases.first(where: { $0.kind == .cooldown }) {
                        Text("calme \(Numbers.minutes(cool.timeS))")
                    }
                }
                .font(.caption2)
                .foregroundStyle(Palette.muted)
            }
            .padding(.top, 6)
            .accessibilityElement(children: .combine)
        } else if let splits = detail.km, splits.contains(where: { $0.hr != nil }) {
            let bounds = detail.hrBounds ?? []
            let zones = splits.map { split in split.hr.map { heartZone(Double($0), bounds: bounds) } }
            let counts = Dictionary(grouping: zones.compactMap { $0 }, by: { $0 }).mapValues(\.count)
            let main = counts.max { $0.value < $1.value }
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 2) {
                    ForEach(Array(zones.enumerated()), id: \.offset) { _, zone in
                        RoundedRectangle(cornerRadius: 2).fill(zone.map(zoneColor) ?? Palette.ghost).frame(height: 14)
                    }
                }
                HStack {
                    Text("1 bloc = 1 km, à la couleur de sa zone")
                    Spacer()
                    if let main {
                        ZoneChip(zone: main.key)
                        Text("\(main.value) sur \(splits.count)")
                    }
                }
                .font(.caption2)
                .foregroundStyle(Palette.muted)
            }
            .padding(.top, 6)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(splits.count) kilomètres" + (main.map { ", \($0.value) en Z\($0.key)" } ?? ""))
        }
    }
}
