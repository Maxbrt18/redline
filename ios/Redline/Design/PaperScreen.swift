import SwiftUI

/// A screen opened from a tab: the same head as the track's, in ink on paper, under the
/// system's own large title.
struct PaperScreen<Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    @ViewBuilder var hero: Hero
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                hero
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
                    .padding(.bottom, Space.l)
                content
                    .padding(.horizontal, 20)
            }
        }
        .background(Palette.bg)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.large)
    }
}
