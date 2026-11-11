import CoreText
import SwiftUI

/// Barlow Condensed, the figures' face, shared with the cards: the app bundles the same
/// WOFF2 files (`src/redline/cards/fonts/`, linked into `Fonts/`), which Core Text reads as
/// they are. Text keeps the system font.
enum Fonts {
    static let bold = "BarlowCondensed-Bold"
    static let semibold = "BarlowCondensed-SemiBold"

    /// Registers the bundled faces for this process; called once, at launch.
    static func register() {
        for url in Bundle.main.urls(forResourcesWithExtension: "woff2", subdirectory: nil) ?? [] {
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
    }
}

extension Font {
    /// A figure in Barlow Condensed, which grows with the reader's text size like `style`.
    static func figure(_ size: CGFloat, weight: Font.Weight = .bold, relativeTo style: Font.TextStyle = .body) -> Font {
        .custom(weight == .bold ? Fonts.bold : Fonts.semibold, size: size, relativeTo: style)
    }
}
