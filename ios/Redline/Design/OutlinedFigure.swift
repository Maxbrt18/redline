import CoreText
import SwiftUI

/// A planned figure: Barlow's glyphs in outline, where a done figure is solid. SwiftUI
/// cannot stroke text, so the outline is drawn from the glyphs' own paths.
struct OutlinedFigure: View {
    let text: String
    var size: CGFloat = 22
    var lineWidth: CGFloat = 1.4
    var color: Color = Palette.ink
    @ScaledMetric private var scale: CGFloat = 1

    var body: some View {
        let outline = GlyphOutline(text: text, fontName: Fonts.bold, size: size * scale)
        outline
            .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineJoin: .round))
            .frame(width: outline.bounds.width + lineWidth, height: outline.bounds.height + lineWidth)
            .accessibilityElement()
            .accessibilityLabel(text)
    }
}

/// The outline of a line of text in one font, with its origin at the top left of its ink.
nonisolated struct GlyphOutline: Shape {
    let outline: Path
    let bounds: CGRect

    init(text: String, fontName: String, size: CGFloat) {
        let font = CTFontCreateWithName(fontName as CFString, size, nil)
        let line = CTLineCreateWithAttributedString(
            NSAttributedString(string: text, attributes: [.init(kCTFontAttributeName as String): font]))
        let outline = CGMutablePath()
        for run in (CTLineGetGlyphRuns(line) as? [CTRun]) ?? [] {
            let count = CTRunGetGlyphCount(run)
            var glyphs = [CGGlyph](repeating: 0, count: count)
            var positions = [CGPoint](repeating: .zero, count: count)
            CTRunGetGlyphs(run, CFRange(), &glyphs)
            CTRunGetPositions(run, CFRange(), &positions)
            for (glyph, position) in zip(glyphs, positions) {
                guard let shape = CTFontCreatePathForGlyph(font, glyph, nil) else { continue }
                outline.addPath(shape, transform: CGAffineTransform(translationX: position.x, y: position.y))
            }
        }
        let ink = outline.boundingBoxOfPath
        let flipped = CGAffineTransform(scaleX: 1, y: -1).translatedBy(x: -ink.minX, y: -ink.maxY)
        self.outline = Path(outline.copy(using: [flipped]) ?? outline)
        bounds = CGRect(origin: .zero, size: ink.size)
    }

    func path(in rect: CGRect) -> Path {
        outline.offsetBy(dx: rect.minX + (rect.width - bounds.width) / 2,
                            dy: rect.minY + (rect.height - bounds.height) / 2)
    }
}
