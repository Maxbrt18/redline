import SwiftUI

/// The family of a session, a landmark or a capability. Its badge names it and judges
/// nothing: never on a screen or section title, nor on a milestone.
enum Family: CaseIterable {
    case run, longRun, strength, rest, otherSport, pain, race

    var tint: Color {
        switch self {
        case .run, .longRun: Palette.hRun
        case .strength: Palette.hStr
        case .rest, .otherSport: Palette.hRest
        case .pain: Palette.hPain
        case .race: Palette.hRace
        }
    }

    var symbol: String {
        switch self {
        case .run: "figure.run"
        case .longRun: "point.topleft.down.to.point.bottomright.curvepath"
        case .strength: "figure.strengthtraining.traditional"
        case .rest: "bed.double"
        case .otherSport: "figure.mixed.cardio"
        case .pain: "bandage"
        case .race: "flag.checkered"
        }
    }
}

/// A family's tinted disc. Two sessions on one day overlap, outlined by the paper.
struct FamilyBadge: View {
    let family: Family
    var small = false
    @Environment(\.colorScheme) private var scheme
    @Environment(\.onTrack) private var onTrack

    var body: some View {
        let side: CGFloat = small ? 24 : 30
        Image(systemName: family.symbol)
            .font(.system(size: small ? 12 : 15, weight: .medium))
            .foregroundStyle(onTrack ? Palette.onTrack : family.tint)
            .frame(width: side, height: side)
            .background(onTrack ? Color.white.opacity(0.2) : family.tint.opacity(scheme == .dark ? 0.24 : 0.2), in: .circle)
            .background(onTrack ? Color.clear : Palette.bg, in: .circle)
            .accessibilityHidden(true)
    }
}

/// The badges of one day's sessions, overlapping.
struct FamilyBadges: View {
    let families: [Family]
    var small = false

    var body: some View {
        HStack(spacing: small ? -6 : -7) {
            ForEach(Array(families.enumerated()), id: \.offset) { _, family in
                FamilyBadge(family: family, small: small)
                    .padding(2)
                    .background(Palette.bg, in: .circle)
                    .padding(-2)
            }
        }
    }
}
