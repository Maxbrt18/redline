#if DEBUG
import RedlineKit
import SwiftUI

/// Every Redline component on one screen, light and dark, to judge them side by side
/// before they reach a real screen. Debug builds only, from the settings.
struct DesignCatalogue: View {
    @State private var pain = 1.0
    @State private var theme = 0
    /// The demo notebook the screens opened from here read.
    @State private var demo = DemoNotebook.session()

    var body: some View {
        List {
            Section("Couleurs") {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 6), spacing: 10) {
                    ForEach(Self.swatches, id: \.0) { name, color in
                        VStack(spacing: 4) {
                            RoundedRectangle(cornerRadius: 8).fill(color).frame(height: 36)
                                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.grid))
                            Text(name).font(.caption2).foregroundStyle(Palette.muted)
                        }
                    }
                }
                .padding(.vertical, 6)
            }

            Section("Chiffres") {
                HStack(alignment: .lastTextBaseline, spacing: 20) {
                    Text("12,4").font(.figure(56))
                    OutlinedFigure(text: "14", size: 56, lineWidth: 2.2)
                    VStack(alignment: .leading) {
                        Text("fait en plein").font(.caption)
                        Text("prévu en contour").font(.caption)
                    }
                    .foregroundStyle(Palette.muted)
                }
                HStack(spacing: 16) {
                    Text("5:12").font(.figure(22)).monospacedDigit().foregroundStyle(Palette.ink)
                    Text("5:00").font(.figure(22, weight: .semibold)).monospacedDigit().foregroundStyle(Palette.muted)
                    Text("Texte en police du système").font(.body)
                }
            }

            Section("Pastilles de famille") {
                HStack(spacing: 14) {
                    ForEach(Family.allCases, id: \.self) { FamilyBadge(family: $0) }
                }
                HStack(spacing: 14) {
                    FamilyBadges(families: [.run, .strength])
                    FamilyBadges(families: [.longRun, .pain], small: true)
                }
            }

            Section("Curseur à ligne rouge") {
                RedLineCursor(label: "Volume de la semaine", value: 38, unit: "km", scale: 0...60,
                              band: 35...45, target: 42, targetLabel: "cible 42")
                RedLineCursor(label: "Sortie longue", value: 24, unit: "km", scale: 0...30,
                              limit: 21, limitLabel: "plafond 21")
                RedLineCursor(label: "Douleur au genou", value: 1.5, scale: 0...3,
                              limit: 2, limitLabel: "plafond 2", target: 1.5, targetLabel: "porte 1,5")
            }

            Section("Trame de points") {
                HStack(spacing: 12) {
                    DotPattern().frame(height: 40)
                    DotPattern.planned.frame(height: 40)
                    DotPattern.over.frame(height: 40)
                }
            }

            Section("Contrôles natifs") {
                Picker("Douleur", selection: $pain) {
                    ForEach([0, 0.5, 1, 1.5, 2, 2.5, 3], id: \.self) { value in
                        Text(value.formatted(.number.precision(.fractionLength(0...1)))).tag(value)
                    }
                }
                .pickerStyle(.segmented)
                Picker("Thème", selection: $theme) {
                    Text("Auto").tag(0)
                    Text("Clair").tag(1)
                    Text("Sombre").tag(2)
                }
                Button("Commencer la séance") {}
                    .buttonStyle(.glassProminent)
                    .controlSize(.large)
                    .frame(maxWidth: .infinity)
                NavigationLink("L’en-tête tartan") { TrackPreview() }
                NavigationLink("Réorganiser (démo)", value: ReorganizeLink(week: DemoNotebook.reorganizedWeek))
                DisclosureGroup("Préparer la séance") {
                    Text("Échauffement, 10 min").foregroundStyle(Palette.muted)
                }
            }

            Section("Questions") {
                MorningCard(wakes: [Notebook.WakeAsk(id: "wake:sample:knee", type: .wake, zone: "knee", label: "Genou",
                                                     date: "2030-01-01", afterEffort: true, levels: [
                                                         .init(slug: "none", label: "rien"), .init(slug: "trace", label: "trace"),
                                                         .init(slug: "stiff", label: "raide"), .init(slug: "pain", label: "douleur")])],
                            daily: Notebook.DailyAsk(id: "daily:sample", type: .daily, date: "2030-01-01", fields: ["sleep", "life_load"]))
                HeartCard(ask: Notebook.HrAsk(id: "hr:sample", type: .hr, date: "2030-01-01", hr: 152, reference: 144, n: 6, paceS: 342))
                DebriefCard(ask: Notebook.DetailAsk(id: "cell:sample", fields: ["fuel_formats", "fuel_mix"], labels: [:], total: 4,
                                                    durationMin: 95, painValues: [0, 0.5, 1, 1.5, 2, 2.5, 3]))
                DebriefCard(ask: Notebook.DetailAsk(id: "cell:sample", fields: ["gut_onset_min"], labels: [:], total: 6,
                                                    durationMin: 95, painValues: []))
            }
            .listRowBackground(Palette.bg)

            Section("Courbe de fréquence cardiaque") {
                HeartChart(series: Self.sampleSeries, bounds: [128, 142, 156, 170], dplus: 64)
            }

            Section("États") {
                ContentUnavailableView("Rien de prévu", systemImage: "calendar",
                                       description: Text("Le coach n’a pas encore écrit cette semaine."))
            }
        }
        .navigationTitle("Catalogue")
        .navigationDestination(for: ReorganizeLink.self) { link in
            ReorganizeScreen(session: demo, week: link.week).environment(\.notebook, demo)
        }
    }

    /// Forty minutes of a session with three repetitions, a stop and a small hill.
    private static let sampleSeries: Notebook.Series = {
        let times = Array(stride(from: 0, through: 2400, by: 30))
        let hearts: [Int?] = times.map { time in
            if (1290...1320).contains(time) { return nil }
            let rep = [(600, 900), (1080, 1380), (1560, 1860)].contains { $0.0...$0.1 ~= time }
            return rep ? 162 + time % 7 : 132 + time % 9
        }
        let altitudes: [Double?] = times.map { 210 + 30 * sin(Double($0) / 2400 * .pi) }
        return Notebook.Series(t: times, duration: 2400, hr: hearts, alt: altitudes,
                               bands: [[600, 900], [1080, 1380], [1560, 1860]], stops: [[1290, 1320]])
    }()

    private static let swatches: [(String, Color)] = [
        ("bg", Palette.bg), ("panel", Palette.panel), ("grid", Palette.grid), ("ink", Palette.ink),
        ("muted", Palette.muted), ("faint", Palette.faint), ("data", Palette.data), ("ghost", Palette.ghost),
        ("accent", Palette.accent), ("track", Palette.track), ("on-track", Palette.onTrack),
    ] + (1...5).map { ("z\($0)", ZoneShade.color($0)) }
}

/// The track under a large title, as the three tabs will show it.
struct TrackPreview: View {
    var body: some View {
        TrackScreen(title: "Aujourd’hui") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Mardi 11 novembre").font(.footnote.weight(.semibold)).opacity(0.8)
                HStack(spacing: 10) {
                    FamilyBadge(family: .run)
                    Text("Endurance fondamentale").font(.title3.weight(.semibold))
                }
                HStack(alignment: .lastTextBaseline, spacing: 10) {
                    OutlinedFigure(text: "10", size: 96, lineWidth: 2.2, color: Palette.onTrack)
                    Text("km prévus, à 5:40").font(.subheadline).opacity(0.8)
                }
            }
        } content: {
            VStack(alignment: .leading, spacing: 24) {
                RedLineCursor(label: "Volume de la semaine", value: 38, unit: "km", scale: 0...60,
                              band: 35...45, target: 42, targetLabel: "cible 42")
                Button("Commencer la séance") {}
                    .buttonStyle(.glassProminent)
                    .controlSize(.large)
                    .frame(maxWidth: .infinity)
                ForEach(0..<12) { index in
                    Text("Ligne \(index + 1)").frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
}

#Preview {
    NavigationStack { DesignCatalogue() }
}
#endif
