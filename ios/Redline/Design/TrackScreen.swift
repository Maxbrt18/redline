import SwiftUI

/// A tab's screen: the track (red, grained, three lanes) under the bar, then the page on
/// paper. The bar stays transparent so that the red runs under it and the status bar. The
/// system's large title cannot be tinted, so the track carries the title in the same face,
/// in white; the bar's compact title appears once the track has scrolled away.
struct TrackScreen<Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    @ViewBuilder var hero: Hero
    @ViewBuilder var content: Content
    @State private var onTrack = true
    @Environment(\.openSettings) private var openSettings
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    PageTitle(title: title)
                    hero
                }
                .foregroundStyle(Palette.onTrack)
                .tint(Palette.onTrack)
                .environment(\.onTrack, true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.gutter)
                .padding(.top, Space.xxs)
                .padding(.bottom, 64)
                // The surface reaches far above the hero, under the bar and into the
                // bounce when the page is pulled down.
                .background(alignment: .bottom) { TrackSurface().padding(.top, -800) }
                content
                    .padding(.horizontal, Space.gutter)
                    .padding(.top, Space.pageTop)
            }
        }
        .background(Palette.bg)
        .compactTitle(title, inView: $onTrack)
        .toolbar {
            if let openSettings {
                ToolbarItem(placement: .topBarTrailing) {
                    // A bar button draws its glyph in the tint.
                    Button("Réglages", systemImage: "gearshape", action: openSettings)
                        .tint(onTrack ? Palette.onTrack : Palette.ink)
                }
            }
        }
        // Always a visible background, so that the bar honours the scheme it is given:
        // clear over the track (the red shows through, and the bar is dark, so its title,
        // controls and status bar are white), then the paper once the track has gone.
        .toolbarBackground(onTrack ? Color.clear : Palette.bg, for: .navigationBar)
        .toolbarBackgroundVisibility(.visible, for: .navigationBar)
        .toolbarColorScheme(onTrack ? .dark : scheme, for: .navigationBar)
    }
}

extension EnvironmentValues {
    /// True inside the track's hero: components take their white-on-red form.
    @Entry var onTrack = false
    /// Opens the settings sheet; the tabs' bars carry its button.
    @Entry var openSettings: (() -> Void)?
    /// The connected notebook, for what a screen loads on its own (the weather).
    @Entry var notebook: NotebookSession?
}

/// The red surface: the track's red, the same in both modes, a fine rubber grain that fades
/// in below the bar, and the three lanes at its foot.
struct TrackSurface: View {
    var body: some View {
        Palette.track
            .overlay {
                Grain()
                    .mask(LinearGradient(stops: [.init(color: .clear, location: 0.6), .init(color: .black, location: 1)],
                                         startPoint: .top, endPoint: .bottom))
            }
            .overlay(alignment: .bottom) { Lanes() }
            .accessibilityHidden(true)
    }
}

/// Three lanes between four lines, as on a straight seen from the infield: lane 1 is the
/// inner one, at the top. Each number is painted in its lane before a finish line on the
/// gutter, turned to be read by a runner coming from the left. They are decoration: the
/// numbers stop growing with the text size so that they always fit their lane.
private struct Lanes: View {
    var body: some View {
        VStack(spacing: 0) {
            line
            ForEach(1...3, id: \.self) { lane in
                Text("\(lane)")
                    .font(.figure(LaneMetrics.number))
                    .foregroundStyle(Palette.onTrack)
                    .fixedSize()
                    .rotationEffect(.degrees(90))
                    .frame(width: LaneMetrics.lane, height: LaneMetrics.lane)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .padding(.trailing, Space.gutter + LaneMetrics.line + Space.xxs)
                line
            }
        }
        .overlay(alignment: .trailing) {
            Rectangle()
                .fill(LaneMetrics.paint)
                .frame(width: LaneMetrics.line)
                .padding(.trailing, Space.gutter)
        }
        .padding(.bottom, LaneMetrics.rim)
        .dynamicTypeSize(...DynamicTypeSize.large)
    }

    private var line: some View {
        Rectangle().fill(LaneMetrics.paint).frame(height: LaneMetrics.line)
    }
}

/// The lanes fit in the track's foot, under the hero: four lines and three lanes plus the
/// rim stay within the hero's bottom padding.
private enum LaneMetrics {
    static let lane: CGFloat = 13
    static let line: CGFloat = 2
    /// The tartan left below the outer line, before the paper.
    static let rim: CGFloat = 6
    static let number: CGFloat = 11
    static let paint = Palette.onTrack.opacity(0.85)
}

/// A fixed rubber grain: fine granules, two darker than the red for one lighter, at a low
/// contrast. The same noise on every launch, so the track never flickers.
private struct Grain: View {
    var body: some View {
        Canvas { context, size in
            var generator = SplitMix(seed: 0x5EED)
            let count = Int(size.width * size.height / GrainMetrics.areaPerGranule)
            for _ in 0..<count {
                let x = generator.unit() * size.width
                let y = generator.unit() * size.height
                let dark = generator.next() % 3 != 0
                let alpha = GrainMetrics.alpha.lowerBound
                    + generator.unit() * (GrainMetrics.alpha.upperBound - GrainMetrics.alpha.lowerBound)
                let granule = CGRect(x: x, y: y, width: GrainMetrics.granule, height: GrainMetrics.granule)
                context.fill(Path(granule), with: .color(dark ? .black.opacity(alpha) : .white.opacity(alpha * 0.7)))
            }
        }
        .drawingGroup()
    }
}

private enum GrainMetrics {
    /// A granule's side: under a point, so it reads as texture rather than as dots.
    static let granule: CGFloat = 0.6
    /// The surface each granule covers on average, in square points.
    static let areaPerGranule: CGFloat = 5
    static let alpha: ClosedRange<Double> = 0.08...0.28
}

/// A small deterministic generator; `SystemRandomNumberGenerator` cannot be seeded.
private struct SplitMix {
    var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    /// A value in 0..<1.
    mutating func unit() -> Double {
        Double(next() % 10_000) / 10_000
    }
}
