import RedlineKit
import SwiftUI

/// A section title on a rule, never a box; a few words may follow it on the right. The
/// space above it is the page's: sections are stacked `Space.section` apart, so a section
/// that opens a page sits right under its head.
struct SectionTitle: View {
    let text: String
    var meta: String?

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline) {
                title
                Spacer(minLength: Space.xs)
                if let meta { Text(meta).textRole(.caption).foregroundStyle(Palette.muted).multilineTextAlignment(.trailing) }
            }
            VStack(alignment: .leading, spacing: Space.xxs) {
                title
                if let meta { Text(meta).textRole(.caption).foregroundStyle(Palette.muted) }
            }
        }
        .padding(.bottom, Space.xs)
    }

    private var title: some View {
        Text(text).textRole(.heading).accessibilityAddTraits(.isHeader)
    }
}
