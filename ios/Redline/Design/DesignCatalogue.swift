#if DEBUG
import SwiftUI

/// Every Redline component on one screen, light and dark, to judge them side by side
/// before they reach a real screen. Debug builds only, from the settings.
struct DesignCatalogue: View {
    @State private var pain = 1.0
    @State private var theme = 0

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
                    Text("5:12").font(.figure(22, weight: .semibold)).monospacedDigit()
                    OutlinedFigure(text: "5:00", size: 22)
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
                DisclosureGroup("Préparer la séance") {
                    Text("Échauffement, 10 min").foregroundStyle(Palette.muted)
                }
            }

            Section("États") {
                ContentUnavailableView("Rien de prévu", systemImage: "calendar",
                                       description: Text("Le coach n’a pas encore écrit cette semaine."))
            }
        }
        .navigationTitle("Catalogue")
    }

    private static let swatches: [(String, Color)] = [
        ("bg", Palette.bg), ("panel", Palette.panel), ("grid", Palette.grid), ("ink", Palette.ink),
        ("muted", Palette.muted), ("faint", Palette.faint), ("data", Palette.data), ("ghost", Palette.ghost),
        ("red", Palette.red), ("over", Palette.over), ("tartan", Palette.tartan), ("track", Palette.trackFlat),
        ("run", Palette.hRun), ("str", Palette.hStr), ("rest", Palette.hRest), ("pain", Palette.hPain),
        ("race", Palette.hRace), ("z1", Palette.z1), ("z2", Palette.z2), ("z3", Palette.z3),
        ("z4", Palette.z4), ("z5", Palette.z5),
    ]
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
                    Divider()
                }
            }
        }
    }
}

#Preview {
    NavigationStack { DesignCatalogue() }
}
#endif
