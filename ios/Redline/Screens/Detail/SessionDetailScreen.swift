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
                    .padding(.vertical, Space.m)
                }
            }
            .padding(.bottom, Space.l)
        }
    }
}

/// The zone of a heart rate on the instance's bounds (1 to 5).
func heartZone(_ heart: Double, bounds: [Int]) -> Int {
    1 + bounds.filter { heart >= Double($0) }.count
}

/// `Z3` on its zone's colour.
struct ZoneChip: View {
    let zone: Int

    var body: some View {
        Text("Z\(zone)")
            .font(.caption2.weight(.bold))
            .foregroundStyle(ZoneShade.isDark(zone) ? Palette.bg : Palette.ink)
            .padding(.horizontal, Space.xxs)
            .padding(.vertical, 1)
            .background(ZoneShade.color(zone), in: .rect(cornerRadius: Radius.mark))
    }
}

/// A section's head: its title, a few words at its right while they fit, under it otherwise.
struct DetailHead: View {
    let title: String
    var aside: String?

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                heading
                Spacer(minLength: Space.xs)
                words
            }
            VStack(alignment: .leading, spacing: Space.xxs) {
                heading
                words
            }
        }
        .padding(.top, Space.xl)
        .padding(.bottom, Space.xs)
    }

    private var heading: some View {
        Text(title).textRole(.heading).accessibilityAddTraits(.isHeader)
    }

    @ViewBuilder private var words: some View {
        if let aside { Text(aside).textRole(.caption).foregroundStyle(Palette.muted) }
    }
}

/// Tiles side by side, one under the other at the accessibility sizes.
struct DetailTiles<Content: View>: View {
    @ViewBuilder var content: Content
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 0))
            : AnyLayout(HStackLayout(alignment: .top, spacing: Space.s))
        layout { content }
    }
}

/// A figure with its label above and a line under it; a figure still to come is planned.
struct DetailTile<Sub: View>: View {
    let label: String
    var symbol: String?
    let value: String
    var suffix: String?
    var pending = false
    @ViewBuilder var sub: Sub

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            HStack(spacing: Space.xxs) {
                if let symbol { Image(systemName: symbol) }
                Text(label)
            }
            .textRole(.caption)
            .foregroundStyle(Palette.muted)
            HStack(alignment: .firstTextBaseline, spacing: Space.xxs) {
                Text(value).figure(.medium, pending ? .planned : .done).foregroundStyle(pending ? Palette.muted : Palette.ink)
                if let suffix { Text(suffix).textRole(.caption).foregroundStyle(Palette.muted) }
            }
            sub.textRole(.caption).foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, Space.s)
    }
}

extension DetailTile where Sub == EmptyView {
    init(label: String, symbol: String? = nil, value: String, suffix: String? = nil, pending: Bool = false) {
        self.init(label: label, symbol: symbol, value: value, suffix: suffix, pending: pending) { EmptyView() }
    }
}

/// The head: the session's family, its distance as large as it gets, its time and pace
/// side by side while they fit, then its course as a strip (phases, or one block per
/// kilometre in its zone).
private struct DetailHero: View {
    let detail: Notebook.SessionView

    var body: some View {
        let fact = detail.fact
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack(spacing: Space.xs) {
                FamilyBadge(family: Family(kind: detail.kind, workout: detail.workout), small: true)
                Text(family).font(TextRole.caption.font.weight(.semibold)).foregroundStyle(Palette.muted)
            }
            if let km = fact.km {
                HStack(alignment: .lastTextBaseline, spacing: Space.xs) {
                    Text(Numbers.text(km, digits: 2)).figure(.hero).foregroundStyle(Palette.ink)
                    Text("km").textRole(.heading).foregroundStyle(Palette.muted)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: Space.xl) { pairs }
                VStack(alignment: .leading, spacing: Space.s) { pairs }
            }
            CourseStrip(detail: detail)
        }
    }

    @ViewBuilder private var pairs: some View {
        let fact = detail.fact
        if let moving = fact.movingS {
            pair(Numbers.duration(moving), "temps")
        }
        if let km = fact.km, km > 0, let moving = fact.movingS {
            pair(Numbers.pace(moving / km), "allure moyenne", suffix: "/km")
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
                Text(value).figure(.large).foregroundStyle(Palette.ink)
                if let suffix { Text(suffix).textRole(.caption).foregroundStyle(Palette.muted) }
            }
            Text(label).textRole(.caption).foregroundStyle(Palette.muted)
        }
    }
}

/// The course in one strip: warm-up, repetitions in the red line, recoveries, cool-down; or,
/// for a steady run, one block per kilometre in the shade of its zone. The words under the
/// strip sit on one line while they fit, one under the other otherwise.
private struct CourseStrip: View {
    let detail: Notebook.SessionView

    var body: some View {
        if let phases = detail.phases, !phases.isEmpty {
            let total = max(phases.reduce(0) { $0 + $1.timeS }, 1)
            let reps = detail.reps ?? []
            VStack(alignment: .leading, spacing: Space.xs) {
                GeometryReader { geometry in
                    HStack(spacing: 1) {
                        ForEach(Array(phases.enumerated()), id: \.offset) { _, phase in
                            Rectangle()
                                .fill(phase.kind == .rep ? Palette.accent : phase.kind == .recovery ? Palette.ghost : Palette.data.opacity(0.35))
                                .frame(width: max(geometry.size.width * phase.timeS / total - 1, 1))
                        }
                    }
                }
                .frame(height: 14)
                .clipShape(.rect(cornerRadius: Radius.mark))
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Space.xs) {
                        warm(phases)
                        Spacer(minLength: Space.xs)
                        repsLine(reps)
                        Spacer(minLength: Space.xs)
                        cool(phases)
                    }
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        warm(phases)
                        repsLine(reps)
                        cool(phases)
                    }
                }
                .textRole(.caption)
                .foregroundStyle(Palette.muted)
            }
            .padding(.top, Space.xs)
            .accessibilityElement(children: .combine)
        } else if let splits = detail.km, splits.contains(where: { $0.hr != nil }) {
            let bounds = detail.hrBounds ?? []
            let zones = splits.map { split in split.hr.map { heartZone(Double($0), bounds: bounds) } }
            let counts = Dictionary(grouping: zones.compactMap { $0 }, by: { $0 }).mapValues(\.count)
            let main = counts.max { $0.value < $1.value }
            VStack(alignment: .leading, spacing: Space.xs) {
                HStack(spacing: 2) {
                    ForEach(Array(zones.enumerated()), id: \.offset) { _, zone in
                        RoundedRectangle(cornerRadius: Radius.mark / 2).fill(zone.map(ZoneShade.color) ?? Palette.ghost).frame(height: 14)
                    }
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Space.xs) {
                        Text("1 bloc = 1 km, à la teinte de sa zone")
                        Spacer(minLength: Space.xs)
                        mainZone(main, of: splits.count)
                    }
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        Text("1 bloc = 1 km, à la teinte de sa zone")
                        mainZone(main, of: splits.count)
                    }
                }
                .textRole(.caption)
                .foregroundStyle(Palette.muted)
            }
            .padding(.top, Space.xs)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(splits.count) kilomètres" + (main.map { ", \($0.value) en Z\($0.key)" } ?? ""))
        }
    }
    @ViewBuilder private func warm(_ phases: [Notebook.Phase]) -> some View {
        if let warm = phases.first(where: { $0.kind == .warmup }) {
            Text("échauffement \(Numbers.minutes(warm.timeS))")
        }
    }

    @ViewBuilder private func repsLine(_ reps: [Notebook.Rep]) -> some View {
        if !reps.isEmpty {
            Text("\(reps.count) × \(Numbers.minutes(reps.reduce(0) { $0 + $1.timeS } / Double(reps.count)))")
                .fontWeight(.semibold)
                .foregroundStyle(Palette.ink)
        }
    }

    @ViewBuilder private func cool(_ phases: [Notebook.Phase]) -> some View {
        if let cool = phases.first(where: { $0.kind == .cooldown }) {
            Text("calme \(Numbers.minutes(cool.timeS))")
        }
    }

    /// The zone most kilometres ran in, by its label.
    @ViewBuilder private func mainZone(_ main: (key: Int, value: Int)?, of count: Int) -> some View {
        if let main {
            HStack(spacing: Space.xxs) {
                ZoneChip(zone: main.key)
                Text("\(main.value) sur \(count)")
            }
        }
    }
}
