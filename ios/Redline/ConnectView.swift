import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// The notebook before this device is connected: how to get a link, and where to paste it.
struct ConnectView: View {
    let session: NotebookSession
    /// Whether the pasteboard holds text or a link: Messenger copies a button's link as a URL,
    /// not as text. Reading this does not ask the reader's permission; reading the content
    /// itself does, so only the system paste button reads it.
    @State private var canPaste = Self.pasteboardHasLink
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Space.l) {
                    ContentUnavailableView {
                        Label("Ton carnet privé", systemImage: "lock")
                    } description: {
                        Text("Ta séance du jour, ta semaine et ta course, sans remonter la conversation.\n\nEnvoie **/carnet** dans Messenger, copie le lien du bouton reçu, puis colle-le ici.")
                    }

                    paste

                    if session.connecting {
                        ProgressView("Connexion…")
                    } else if let failure = session.failure {
                        // In ink after a symbol in the red line: a refusal must not read as a hint.
                        Label {
                            // A word joiner keeps « /carnet » whole: the slash is a line-break opportunity.
                            Text(failure.replacingOccurrences(of: "/carnet", with: "/\u{2060}carnet"))
                                .foregroundStyle(Palette.ink)
                        } icon: {
                            Image(systemName: "exclamationmark.circle").foregroundStyle(Palette.accent)
                        }
                        .textRole(.body)
                        .padding(Space.m)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
                    }
                }
                .padding(.vertical, Space.xl)
                .padding(.horizontal, Space.m)
            }
            .background(Palette.bg)
        }
        .onReceive(NotificationCenter.default.publisher(for: UIPasteboard.changedNotification)) { _ in
            canPaste = Self.pasteboardHasLink
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { canPaste = Self.pasteboardHasLink }
        }
    }

    private static var pasteboardHasLink: Bool {
        UIPasteboard.general.hasStrings || UIPasteboard.general.hasURLs
    }

    /// The pasted link, whether it came as a URL or as text.
    private static func link(in providers: [NSItemProvider]) async -> String? {
        for provider in providers {
            if provider.canLoadObject(ofClass: URL.self) {
                let url: String? = await withCheckedContinuation { continuation in
                    _ = provider.loadObject(ofClass: URL.self) { url, _ in continuation.resume(returning: url?.absoluteString) }
                }
                if let url { return url }
            }
            if provider.canLoadObject(ofClass: String.self) {
                let text: String? = await withCheckedContinuation { continuation in
                    _ = provider.loadObject(ofClass: String.self) { text, _ in continuation.resume(returning: text) }
                }
                if let text { return text }
            }
        }
        return nil
    }

    /// The system paste button while there is text to paste; otherwise a button in the
    /// system's disabled state, with what to copy first.
    @ViewBuilder private var paste: some View {
        if canPaste {
            PasteButton(supportedContentTypes: [.url, .plainText]) { providers in
                Task {
                    // Nothing readable is still an answer: an empty text is refused as no link.
                    await session.connect(with: await Self.link(in: providers) ?? "")
                }
            }
            .buttonBorderShape(.capsule)
            .controlSize(.large)
            .disabled(session.connecting)
        } else {
            VStack(spacing: Space.xs) {
                Button("Coller", systemImage: "doc.on.clipboard") {}
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .controlSize(.large)
                    .disabled(true)
                Text("Copie d’abord le lien reçu dans Messenger.")
                    .textRole(.caption)
                    .foregroundStyle(Palette.muted)
                    .multilineTextAlignment(.center)
            }
        }
    }
}
