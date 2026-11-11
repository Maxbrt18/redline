import SwiftUI

/// The notebook before this device is connected: how to get a link, and where to paste it.
struct ConnectView: View {
    let session: NotebookSession

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                ContentUnavailableView {
                    Label("Ton carnet privé", systemImage: "lock")
                } description: {
                    Text("Ta séance du jour, ta semaine et ta course, sans remonter la conversation.\n\nEnvoie **/carnet** dans Messenger, copie le lien du bouton reçu, puis colle-le ici.")
                }
                .frame(maxHeight: 360)

                PasteButton(payloadType: String.self) { strings in
                    guard let text = strings.first else { return }
                    Task { await session.connect(with: text) }
                }
                .buttonBorderShape(.capsule)
                .controlSize(.large)
                .disabled(session.connecting)

                if session.connecting {
                    ProgressView("Connexion…")
                } else if let failure = session.failure {
                    Text(failure)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 32)
                }
                Spacer()
            }
            .padding(.top, 40)
        }
    }
}
