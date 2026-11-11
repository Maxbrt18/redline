import RedlineKit
import SwiftUI

/// A screen of the notebook: its view loaded on the track (a tab) or on paper (a screen
/// opened from one), refreshed by pulling the page, and what to say while there is
/// nothing yet, nothing at all, or only the kept answer.
struct NotebookScreen<Value: Decodable & Sendable, Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    let session: NotebookSession
    @State var model: ScreenModel<Value>
    var onTrack = true
    /// The title the view itself gives once loaded, for a screen opened by its id alone.
    var heading: ((Value) -> String)?
    @ViewBuilder var hero: (Value) -> Hero
    @ViewBuilder var content: (Value) -> Content

    var body: some View {
        let title = model.value.flatMap { value in heading.map { LocalizedStringKey($0(value)) } } ?? title
        if onTrack {
            TrackScreen(title: title) { head } content: { page }
                .refreshable { await model.load(with: session) }
                .task { await model.load(with: session) }
        } else {
            PaperScreen(title: title) { head } content: { page }
                .refreshable { await model.load(with: session) }
                .task { await model.load(with: session) }
        }
    }

    @ViewBuilder private var head: some View {
        if let value = model.value {
            hero(value)
        } else if model.failure == nil {
            Text("Mercredi 11 novembre").font(.footnote.weight(.semibold)).redacted(reason: .placeholder)
        }
    }

    private var page: some View {
            VStack(alignment: .leading, spacing: 0) {
                if model.stale, let savedAt = model.savedAt {
                    Label("Hors ligne · à jour \(savedAt, format: .relative(presentation: .named))",
                          systemImage: "icloud.slash")
                        .font(.footnote)
                        .foregroundStyle(Palette.muted)
                        .padding(.bottom, 12)
                }
                if let value = model.value {
                    content(value)
                } else if let failure = model.failure {
                    ContentUnavailableView {
                        Label("Carnet indisponible", systemImage: "exclamationmark.icloud")
                    } description: {
                        Text(failure)
                    } actions: {
                        Button("Réessayer") { Task { await model.load(with: session) } }
                            .buttonStyle(.glass)
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                }
            }
    }
}
