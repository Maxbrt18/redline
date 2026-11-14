import SwiftUI

/// The colour of the day's card, and so of the bar it folds into: red while a session waits
/// today, ink once it is done, paper on a day off.
enum CardTone: Equatable {
    case track, done, paper

    var surface: Color {
        switch self {
        case .track: Palette.track
        case .done: Palette.done
        case .paper: Palette.panel
        }
    }

    /// Text and lines on the surface.
    var ink: Color {
        switch self {
        case .track: Palette.onTrack
        case .done: Palette.onDone
        case .paper: Palette.ink
        }
    }

    /// Text of the second rank on the surface.
    var muted: Color {
        self == .paper ? Palette.muted : ink.opacity(0.8)
    }

    /// The red and the ink surfaces are dark in both modes: the bar over them is too, so that
    /// its controls and the status bar stay white.
    var darkBar: Bool { self != .paper }
}

/// What the bar says once the card has scrolled under it: the day's figure, a few words,
/// and what still waits in the card.
struct CardBar: Equatable {
    var tone: CardTone
    var figure: String?
    var title: String
    var badge: String?
}

private struct CardBarKey: PreferenceKey {
    static let defaultValue: CardBar? = nil

    static func reduce(value: inout CardBar?, nextValue: () -> CardBar?) {
        value = nextValue() ?? value
    }
}

extension View {
    /// Declares the bar that the card folds into.
    func cardBar(_ bar: CardBar) -> some View {
        preference(key: CardBarKey.self, value: bar)
    }
}

/// A tab whose head is the day's card. The card runs under the bar and the status bar, in
/// its tone; the bar stays clear over it, then takes the card's tone and its summary once
/// the card has scrolled away. A tap on that summary brings the card back.
struct CardScreen<Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    @ViewBuilder var hero: Hero
    @ViewBuilder var content: Content
    @State private var bar: CardBar?
    @State private var heroHeight: CGFloat = 0
    @State private var folded = false
    @Environment(\.openSettings) private var openSettings
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let tone = bar?.tone ?? .track
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    hero
                        .id(CardScreenMetrics.top)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { heroHeight = $0 }
                    content
                        .padding(.horizontal, Space.gutter)
                        .padding(.top, Space.l)
                }
            }
            // The bar takes over once the card's figure has gone under it: the bar has the card's
            // colour, so the two read as one surface, and a short page still folds.
            .onScrollGeometryChange(for: Bool.self) { geometry in
                geometry.contentOffset.y + geometry.contentInsets.top > min(CardScreenMetrics.foldAfter, heroHeight / 2)
            } action: { _, isFolded in
                withAnimation(.easeOut(duration: 0.2)) { folded = isFolded }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Always present, so that the bar never shows the screen's title over the card,
                // which already carries it.
                ToolbarItem(placement: .principal) {
                    FoldedSummary(bar: bar ?? CardBar(tone: tone, title: "")) {
                        withAnimation { proxy.scrollTo(CardScreenMetrics.top, anchor: .top) }
                    }
                    .opacity(folded ? 1 : 0)
                    .allowsHitTesting(folded)
                    .accessibilityHidden(!folded)
                }
                if let openSettings {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Réglages", systemImage: "gearshape", action: openSettings)
                            .tint(tone.ink)
                    }
                }
            }
        }
        .background(Palette.bg)
        .onPreferenceChange(CardBarKey.self) { bar = $0 }
        .toolbarBackground(folded ? tone.surface : Color.clear, for: .navigationBar)
        .toolbarBackgroundVisibility(.visible, for: .navigationBar)
        .toolbarColorScheme(tone.darkBar ? .dark : scheme, for: .navigationBar)
    }
}

/// The card in a line, in the bar: its figure, its words, and a pill for what waits.
private struct FoldedSummary: View {
    let bar: CardBar
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.xs) {
                if let figure = bar.figure {
                    Text(figure).font(.figure(22, weight: .bold, relativeTo: .headline)).monospacedDigit()
                }
                Text(bar.title).font(.headline).lineLimit(1)
                if let badge = bar.badge {
                    Text(badge)
                        .font(.caption.weight(.bold))
                        .padding(.horizontal, Space.xs)
                        .padding(.vertical, Space.xxs)
                        .foregroundStyle(bar.tone == .paper ? Palette.bg : bar.tone.surface)
                        .background(bar.tone == .paper ? Palette.accent : bar.tone.ink, in: .capsule)
                }
            }
            .foregroundStyle(bar.tone.ink)
            .dynamicTypeSize(...DynamicTypeSize.xxLarge)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Revient en haut de la page")
    }
}

private enum CardScreenMetrics {
    static let top = "card-top"
    /// How far the page scrolls before the card's title and figure have gone under the bar.
    static let foldAfter: CGFloat = 260
}
