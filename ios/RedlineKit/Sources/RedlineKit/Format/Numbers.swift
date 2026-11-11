import Foundation

/// Figures as the athlete reads them, in French: a decimal comma, a thin space between
/// thousands, and times that never break across a line.
public enum Numbers {
    private static let french = Locale(identifier: "fr_FR")

    /// `12,4`; `12` when the decimals are zero; at most `digits` decimals.
    public static func text(_ value: Double, digits: Int = 1) -> String {
        value.formatted(.number.precision(.fractionLength(0...digits)).locale(french))
    }

    /// A pace per kilometre from seconds: `5:07`.
    public static func pace(_ seconds: Double) -> String {
        let rounded = Int(seconds.rounded())
        return "\(rounded / 60):" + String(format: "%02d", rounded % 60)
    }

    /// A duration from seconds: `52:07`, `1:04:30`.
    public static func duration(_ seconds: Double) -> String {
        let total = Int(seconds.rounded()), hours = total / 3600, minutes = total % 3600 / 60
        let head = hours > 0 ? "\(hours):" + String(format: "%02d", minutes) : "\(minutes)"
        return head + ":" + String(format: "%02d", total % 60)
    }

    /// Whole minutes from seconds, with the prime: `12′`.
    public static func minutes(_ seconds: Double) -> String {
        "\(Int((seconds / 60).rounded()))′"
    }

    /// A wall-clock time from `HH:MM`, or from the time of an ISO date-time: `19 h 05`,
    /// `7 h`, with non-breaking spaces. Nil when the text holds no time.
    public static func clock(_ text: String) -> String? {
        guard let match = text.firstMatch(of: /(\d{2}):(\d{2})/), let hours = Int(match.1) else { return nil }
        let minutes = String(match.2)
        return "\(hours)\u{00a0}h" + (minutes == "00" ? "" : "\u{00a0}\(minutes)")
    }
}
