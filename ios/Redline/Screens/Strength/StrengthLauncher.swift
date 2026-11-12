import RedlineKit
import SwiftUI

/// Today's strength session: the button that opens it (or resumes it where this phone left
/// it), and the exercises folded under their count.
struct StrengthLauncher: View {
    let ready: Notebook.StrengthReady
    @Environment(\.notebook) private var session
    @Environment(\.reloadScreen) private var reload
    @State private var launching = false
    @State private var failure: String?

    var body: some View {
        let resume = StrengthLauncher.resume(of: ready, in: session)
        VStack(alignment: .leading, spacing: 10) {
            if let resume {
                Text("\(resume.done) exercice\(resume.done > 1 ? "s" : "") sur \(resume.total)")
                    .font(.subheadline).foregroundStyle(Palette.muted)
            }
            Button {
                Task { await launch() }
            } label: {
                Group {
                    if launching { ProgressView() } else {
                        Text(resume == nil ? "Séance guidée" : "Reprendre la séance").font(.body.weight(.semibold))
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.bg)
            .disabled(launching)
            if let failure {
                Label(failure, systemImage: "exclamationmark.circle").textRole(.caption).foregroundStyle(Palette.accent)
            }
            ExerciseList(session: ready.session)
        }
        .task {
            // A report left waiting for the network goes out when Today shows again.
            if let session, case .signedIn(let client) = session.state {
                await StrengthOutbox.flush(client: client, store: session.strengthStore)
            }
        }
    }

    /// How far this phone went in today's session, if it started it and has not sent it.
    static func resume(of ready: Notebook.StrengthReady, in session: NotebookSession?) -> StrengthResume? {
        guard let session else { return nil }
        return StrengthResume(session: ready.session, progress: session.strengthStore.load(sid: ready.session.sid))
    }

    /// Asks the server for the link of the session shown; offline, reopens the one kept.
    private func launch() async {
        guard let session, case .signedIn(let client) = session.state else { return }
        launching = true
        failure = nil
        defer { launching = false }
        let sid = ready.session.sid
        let token: StrengthToken
        do {
            token = try await client.launchStrength(signature: ready.signature)
        } catch .unreachable {
            guard let kept = session.strengthStore.loadOpened(sid: sid) else {
                failure = NotebookSession.words(for: .unreachable)
                return
            }
            token = kept.token
        } catch .signedOut(let message) {
            session.forget(saying: message.isEmpty ? nil : message)
            return
        } catch {
            failure = NotebookSession.words(for: error)
            await reload?()
            return
        }
        session.strength = StrengthLaunch(token: token, sid: sid, onClose: reload)
    }
}
