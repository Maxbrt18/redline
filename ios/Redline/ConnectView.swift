import SwiftUI
import UIKit

/// The notebook before this device is connected: how to get a link, and where to paste it.
struct ConnectView: View {
    let session: NotebookSession
    /// Whether the pasteboard holds text. Reading this does not ask the reader's permission;
    /// reading the text itself does, so only the system paste button reads it.
    @State private var canPaste = UIPasteboard.general.hasStrings
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
                        Text(failure)
                            .textRole(.body)
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, Space.xl)
                    }
                }
                .padding(.vertical, Space.xl)
                .padding(.horizontal, Space.m)
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIPasteboard.changedNotification)) { _ in
            canPaste = UIPasteboard.general.hasStrings
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { canPaste = UIPasteboard.general.hasStrings }
        }
    }

    /// The system paste button while there is text to paste; otherwise a button in the
    /// system's disabled state, with what to copy first.
    @ViewBuilder private var paste: some View {
        if canPaste {
            PasteButton(payloadType: String.self) { strings in
                guard let text = strings.first else { return }
                Task { await session.connect(with: text) }
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
