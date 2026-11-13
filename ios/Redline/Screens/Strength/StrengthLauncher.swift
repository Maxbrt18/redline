import RedlineKit
import SwiftUI

/// Today's strength session as a card a finger presses: its title, how far this phone went
/// in it, the button that opens it (or resumes it where this phone left it), and the
/// exercises folded under their count.
struct StrengthLauncher: View {
    let ready: Notebook.StrengthReady
    let title: String
    var note: String?
    @Environment(\.notebook) private var session
    @Environment(\.reloadScreen) private var reload
    @State private var launching = false
    @State private var failure: String?

    var body: some View {
        let resume = StrengthLauncher.resume(of: ready, in: session)
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(spacing: Space.xs) {
                FamilyBadge(family: .strength, small: true)
                Text(title).font(TextRole.body.font.weight(.semibold))
                Spacer(minLength: Space.xs)
                if let resume {
                    Text("depuis \(resume.startedAt.formatted(date: .omitted, time: .shortened))")
                        .textRole(.caption).foregroundStyle(Palette.muted)
                }
            }
            if let note { Text(note).textRole(.secondary).foregroundStyle(Palette.muted) }
            if let resume {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    HStack(spacing: Space.xxs) {
                        ForEach(0..<resume.total, id: \.self) { index in
                            Capsule().fill(index < resume.done ? Palette.ink : Palette.faint.opacity(0.4)).frame(height: 4)
                        }
                    }
                    .accessibilityHidden(true)
                    let plural = resume.done > 1 ? "s" : ""
                    Text("\(resume.done) exercice\(plural) fait\(plural) sur \(resume.total)")
                        .textRole(.caption).foregroundStyle(Palette.muted)
                }
            }
            Button {
                Task { await launch() }
            } label: {
                Group {
                    if launching { ProgressView() } else {
                        Text(resume == nil ? "Séance guidée" : "Reprendre").font(.body.weight(.semibold))
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
        .padding(Space.m)
        .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
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
