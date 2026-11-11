import SwiftUI

/// The notebook's settings, in a sheet from any tab.
struct SettingsView: View {
    let session: NotebookSession
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingSignOut = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button("Déconnecter cet appareil", role: .destructive) {
                        confirmingSignOut = true
                    }
                } footer: {
                    Text("Pour revenir, envoie /carnet dans Messenger et colle le nouveau lien.")
                }

                Section {
                    LabeledContent("Serveur", value: session.server.baseURL.host() ?? "")
                    LabeledContent("Version", value: Self.version)
                }

                #if DEBUG
                Section {
                    NavigationLink("Catalogue des composants") { DesignCatalogue() }
                }
                #endif
            }
            .navigationTitle("Réglages")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Fermer", systemImage: "xmark") { dismiss() }
                }
            }
            .confirmationDialog("Déconnecter cet appareil ?", isPresented: $confirmingSignOut,
                                titleVisibility: .visible) {
                Button("Déconnecter", role: .destructive) {
                    Task {
                        await session.signOut()
                        dismiss()
                    }
                }
            }
        }
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let marketing = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        return "\(marketing) (\(build))"
    }
}
