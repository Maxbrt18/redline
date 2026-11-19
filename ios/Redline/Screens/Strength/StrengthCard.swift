import RedlineKit
import SwiftUI

/// Today's strength session in its surface: what it is and where it stands, the progress of
/// a session this phone started, and the one button that opens it or resumes it. A done
/// session keeps its line with a check; a closed one says why.
struct StrengthCard: View {
    let item: Notebook.SessionItem
    let offer: Notebook.StrengthOffer
    @Environment(\.notebook) private var session
    @Environment(\.reloadScreen) private var reload
    @State private var launching = false
    @State private var failure: String?

    var body: some View {
        let ready: Notebook.StrengthReady? = if case .ready(let ready) = offer, !item.isDone { ready } else { nil }
        let resume = ready.flatMap { StrengthCard.resume(of: $0, in: session) }
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.s) {
                IconTile(symbol: Family.strength.symbol, done: item.isDone)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title).textRole(.body).fontWeight(.semibold)
                    if let line = line(ready: ready, resume: resume) {
                        Text(line).textRole(.secondary).foregroundStyle(Palette.muted)
                    }
                }
                .accessibilityElement(children: .combine)
            }
            if let resume {
                SegmentedProgress(done: resume.done, total: resume.total)
            }
            if let ready {
                Button {
                    Task { await launch(ready) }
                } label: {
                    Group {
                        if launching {
                            ProgressView()
                        } else {
                            Label(resume == nil ? "Commencer la séance" : "Reprendre la séance", systemImage: "play.fill")
                                .font(.body.weight(.semibold))
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .foregroundStyle(Palette.ink)
                    .background(Palette.panel, in: .capsule)
                    .contentShape(.capsule)
                }
                .buttonStyle(.plain)
                .disabled(launching)
                if let failure {
                    Label(failure, systemImage: "exclamationmark.circle").textRole(.caption).foregroundStyle(Palette.accent)
                }
                if resume == nil {
                    ExerciseList(session: ready.session)
                }
            }
        }
        .padding(Space.m)
        .surface()
        .task {
            // A report left waiting for the network goes out when Today shows again.
            if let session, case .signedIn(let client) = session.state {
                await StrengthOutbox.flush(client: client, store: session.strengthStore)
            }
        }
    }

    /// Where the session stands, in a line: the exercise to resume, the session's size, its
    /// duration once done, or why it is closed.
    private func line(ready: Notebook.StrengthReady?, resume: StrengthResume?) -> String? {
        if item.isDone {
            return item.strengthFact?.durationMin.map { "Faite en \($0) minutes" } ?? "Faite"
        }
        if let ready, let resume {
            let exercises = ready.session.exercises
            let next = resume.done < exercises.count ? exercises[resume.done].name : nil
            return "Exercice \(min(resume.done + 1, resume.total)) sur \(resume.total)" + (next.map { " : \($0)" } ?? "")
        }
        if let ready {
            let count = ready.session.exercises.count
            return "\(count) exercice\(count > 1 ? "s" : ""), séance guidée"
        }
        if case .closed(let closed) = offer, !closed.message.isEmpty { return closed.message }
        return item.note
    }

    /// How far this phone went in today's session, if it started it and has not sent it.
    static func resume(of ready: Notebook.StrengthReady, in session: NotebookSession?) -> StrengthResume? {
        guard let session else { return nil }
        return StrengthResume(session: ready.session, progress: session.strengthStore.load(sid: ready.session.sid))
    }

    /// Asks the server for the link of the session shown; offline, reopens the one kept.
    private func launch(_ ready: Notebook.StrengthReady) async {
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
            await reload()
            return
        }
        session.strength = StrengthLaunch(token: token, sid: sid, onClose: reload)
    }
}

/// A session's exercises as segments, the done ones in ink.
private struct SegmentedProgress: View {
    let done: Int
    let total: Int

    var body: some View {
        HStack(spacing: Space.xxs) {
            ForEach(0..<max(total, 1), id: \.self) { index in
                Capsule().fill(index < done ? Palette.ink : Palette.ghost).frame(height: 4)
            }
        }
        .accessibilityElement()
        .accessibilityLabel("\(done) exercice\(done > 1 ? "s" : "") sur \(total)")
    }
}
