import SwiftUI

/// The family of a session, a landmark or a capability. Its badge names it and judges
/// nothing: never on a screen or section title, nor on a milestone.
enum Family: CaseIterable {
    case run, longRun, strength, rest, otherSport, pain, race

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

/// A family's bare symbol, in the second rank of ink. The frame is a stable size that follows the text size,
/// so rows of badges stay aligned.
struct FamilyBadge: View {
    let family: Family
    var small = false
    /// A finer glyph than the family's.
    var symbol: String?
    @Environment(\.onTrack) private var onTrack
    @ScaledMetric(relativeTo: .body) private var regularSide: CGFloat = 30
    @ScaledMetric(relativeTo: .footnote) private var smallSide: CGFloat = 24

    var body: some View {
        let side = small ? smallSide : regularSide
        Image(systemName: symbol ?? family.symbol)
            .font((small ? Font.footnote : Font.body).weight(.medium))
            .foregroundStyle(onTrack ? Palette.onTrack : Palette.muted)
            .frame(width: side, height: side)
            .accessibilityHidden(true)
    }
}

/// The badges of one day's sessions, side by side.
struct FamilyBadges: View {
    let families: [Family]
    var small = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Array(families.enumerated()), id: \.offset) { _, family in
                FamilyBadge(family: family, small: small)
            }
        }
    }
}
