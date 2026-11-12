import SwiftUI

/// A screen's large title, drawn in the page on the gutter. The bar's own large title sits
/// on the bar's margin, which is narrower than the gutter on most iPhones.
struct PageTitle: View {
    let title: LocalizedStringKey

    var body: some View {
        Text(title)
            .font(.largeTitle.bold())
            .accessibilityAddTraits(.isHeader)
    }
}

extension View {
    /// The bar of a screen that draws its own `PageTitle`: empty while that title is in
    /// view, the compact title once it has scrolled under the bar. `inView` follows it.
    func compactTitle(_ title: LocalizedStringKey, inView: Binding<Bool>) -> some View {
        onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top < PageTitleMetrics.scrolledAway
        } action: { _, shown in
            withAnimation(.easeOut(duration: 0.15)) { inView.wrappedValue = shown }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(title).font(.headline).opacity(inView.wrappedValue ? 0 : 1)
            }
        }
    }
}

private enum PageTitleMetrics {
    /// How far the page scrolls before the drawn title has gone under the bar.
    static let scrolledAway: CGFloat = 44
}
