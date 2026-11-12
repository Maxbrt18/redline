import SwiftUI

/// The charter's dot screen: what is planned or allowed, what passes a limit, the race
/// itself. A fact stays solid; dots never appear elsewhere.
struct DotPattern: View {
    var color: Color = Palette.faint
    var spacing: CGFloat = 4.5
    var radius: CGFloat = 1.2

    var body: some View {
        Canvas { context, size in
            var dots = Path()
            var y = spacing / 2
            while y < size.height + radius {
                var x = spacing / 2
                while x < size.width + radius {
                    dots.addEllipse(in: CGRect(x: x - radius, y: y - radius, width: 2 * radius, height: 2 * radius))
                    x += spacing
                }
                y += spacing
            }
            context.fill(dots, with: .color(color))
        }
        .accessibilityHidden(true)
    }
}

extension DotPattern {
    /// Dense red dots: the part of a value past its limit.
    static var over: DotPattern { DotPattern(color: Palette.accent, spacing: 3.5, radius: 1.25) }
    /// What is planned or allowed, on the data's colour.
    static var planned: DotPattern { DotPattern(color: Palette.data.opacity(0.75), spacing: 4, radius: 1.05) }
}
