import SwiftUI

/// A screen opened from a tab: the same head as the track's, in ink on paper. The title is
/// drawn on the gutter, as on the track, and the bar's compact title takes over once it
/// has scrolled away.
struct PaperScreen<Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    @ViewBuilder var hero: Hero
    @ViewBuilder var content: Content
    @State private var titleInView = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    PageTitle(title: title)
                    hero
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.gutter)
                .padding(.bottom, Space.pageTop)
                content
                    .padding(.horizontal, Space.gutter)
            }
        }
        .background(Palette.bg)
        .compactTitle(title, inView: $titleInView)
    }
}
