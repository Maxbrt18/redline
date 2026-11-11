import RedlineKit
import SwiftUI

/// A tab of the notebook: its view loaded on the track, refreshed by pulling the page, and
/// what to say while there is nothing yet, nothing at all, or only the kept answer.
struct NotebookScreen<Value: Decodable & Sendable, Hero: View, Content: View>: View {
    let title: LocalizedStringKey
    let session: NotebookSession
    @State var model: ScreenModel<Value>
    @ViewBuilder var hero: (Value) -> Hero
    @ViewBuilder var content: (Value) -> Content

    var body: some View {
        TrackScreen(title: title) {
            if let value = model.value {
                hero(value)
            } else if model.failure == nil {
                Text("Mercredi 11 novembre").font(.footnote.weight(.semibold)).redacted(reason: .placeholder)
            }
        } content: {
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
        .refreshable { await model.load(with: session) }
        .task { await model.load(with: session) }
    }
}
