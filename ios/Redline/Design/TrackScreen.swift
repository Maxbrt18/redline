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
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.largeTitle.bold())
                        .accessibilityAddTraits(.isHeader)
                    hero
                }
                .foregroundStyle(Palette.onTrack)
                .tint(Palette.onTrack)
                .environment(\.onTrack, true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.top, 4)
                .padding(.bottom, 64)
                // The surface reaches far above the hero, under the bar and into the
                // bounce when the page is pulled down.
                .background(alignment: .bottom) { TrackSurface().padding(.top, -800) }
                content
                    .padding(.horizontal, 20)
                    .padding(.top, 8)
            }
        }
        .background(Palette.bg)
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top < 44
        } action: { _, isOnTrack in
            withAnimation(.easeOut(duration: 0.15)) { onTrack = isOnTrack }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(title).font(.headline).opacity(onTrack ? 0 : 1)
            }
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

/// The red surface: the track's red, the same in both modes, a rubber grain that fades in
/// below the bar, and the three lanes at its foot.
struct TrackSurface: View {
    var body: some View {
        Palette.track
            .overlay {
                Grain()
                    .blendMode(.softLight)
                    .opacity(0.55)
                    .mask(LinearGradient(stops: [.init(color: .clear, location: 0.72), .init(color: .black, location: 1)],
                                         startPoint: .top, endPoint: .bottom))
            }
            .overlay(alignment: .bottom) { Lanes() }
            .accessibilityHidden(true)
    }
}

/// Three lane lines, numbered on the right. They are decoration: their numbers stop
/// growing with the text size so the lanes never overlap.
private struct Lanes: View {
    var body: some View {
        VStack(spacing: 13) {
            ForEach(1...3, id: \.self) { lane in
                Rectangle()
                    .fill(Palette.onTrack.opacity(0.85))
                    .frame(height: 2)
                    .overlay(alignment: .topTrailing) {
                        Text("\(lane)")
                            .font(.figure(11))
                            .foregroundStyle(Palette.onTrack)
                            .padding(.trailing, 24)
                            .offset(y: 3)
                    }
            }
        }
        .padding(.bottom, 14)
        .dynamicTypeSize(...DynamicTypeSize.large)
    }
}

/// A fixed rubber grain: the same noise on every launch, so the track never flickers.
private struct Grain: View {
    var body: some View {
        Canvas { context, size in
            var generator = SplitMix(seed: 0x5EED)
            let count = Int(size.width * size.height / 10)
            for _ in 0..<count {
                let x = Double(generator.next() % 10_000) / 10_000 * size.width
                let y = Double(generator.next() % 10_000) / 10_000 * size.height
                let light = 0.25 + Double(generator.next() % 1000) / 2000
                context.fill(Path(CGRect(x: x, y: y, width: 1, height: 1)),
                             with: .color(Color(white: light)))
            }
        }
        .drawingGroup()
    }
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
}
