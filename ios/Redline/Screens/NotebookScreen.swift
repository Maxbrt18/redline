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
    /// The head is the day's card, which draws its own surface (`CardScreen`).
    var card = false
    /// The title the view itself gives once loaded, for a screen opened by its id alone.
    var heading: ((Value) -> String)?
    @ViewBuilder var hero: (Value) -> Hero
    @ViewBuilder var content: (Value) -> Content
    /// What the last answer did, said at the top of the page until the next one.
    @State private var notice: String?

    var body: some View {
        // The actions wrap the whole screen, head included: the day's card asks its
        // questions in the head, the other screens in the page.
        surface
            .refreshable { await load() }
            .task { await load() }
            .environment(\.reloadScreen, ReloadAction { await load() })
            .environment(\.answered, AnswerAction { alert in await answered(alert: alert) })
    }

    @ViewBuilder private var surface: some View {
        let title = model.value.flatMap { value in heading.map { LocalizedStringKey($0(value)) } } ?? title
        if card {
            CardScreen(title: title) { head } content: { page }
        } else if onTrack {
            TrackScreen(title: title) { head } content: { page }
        } else {
            PaperScreen(title: title) { head } content: { page }
        }
    }

    private func load() async {
        await model.load(with: session)
    }

    /// Says the answer is kept, then loads the screen, which moves on to the next question.
    private func answered(alert: Bool) async {
        let words = alert ? "Noté. Le coach te répond dans Messenger." : "Noté."
        withAnimation { notice = words }
        AccessibilityNotification.Announcement(words).post()
        await load()
    }

    @ViewBuilder private var head: some View {
        if let value = model.value {
            hero(value)
        } else if model.failure == nil {
            if card {
                DayCardSkeleton()
            } else {
                Text("Mercredi 11 novembre").font(.footnote.weight(.semibold)).redacted(reason: .placeholder)
            }
        }
    }

    private var page: some View {
            VStack(alignment: .leading, spacing: 0) {
                if let notice {
                    Label(notice, systemImage: "checkmark.circle.fill")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Palette.ink)
                        .padding(.bottom, 12)
                        .transition(.opacity)
                }
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
            .onAppear {
                if let words = session.notice {
                    session.notice = nil
                    withAnimation { notice = words }
                }
            }
    }
}
