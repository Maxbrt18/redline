import SwiftUI

/// The colour of a heart-rate zone: an ink ramp from what was planned to what was done for
/// zones 1 to 4, and the red line for zone 5. A zone is never told by its shade alone: its
/// `Z1`–`Z5` label goes with it.
enum ZoneShade {
    /// Zone 1 to 5; values outside are clamped.
    static func color(_ zone: Int) -> Color {
        let zone = min(max(zone, 1), 5)
        if zone == 5 { return Palette.accent }
        return Palette.ghost.mix(with: Palette.data, by: Double(zone - 1) / 3)
    }

    /// Whether a label set on the zone's shade reads in the paper colour rather than in ink.
    static func isDark(_ zone: Int) -> Bool { zone >= 3 }
}
