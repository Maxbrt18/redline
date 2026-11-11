import Foundation
import Observation
import RedlineKit
import UIKit

/// A strength session asked to open: its link, and what to do once it is closed.
struct StrengthLaunch: Identifiable {
    let token: StrengthToken
    /// The session the link was asked for, to reopen it offline from what the device kept.
    let sid: String?
    var onClose: (() async -> Void)?

    var id: String { token.value }
}

/// The strength session on screen: it opens the link, runs `StrengthRun`, keeps every change
/// on the device at once, moves the timers on and sends the report until the server has it.
@MainActor
@Observable
final class StrengthModel {
    enum Phase {
        case opening
        /// The link did not open; a title and why, in the athlete's words.
        case failed(String, String)
        case running
    }

    private(set) var phase: Phase = .opening
    private(set) var run: StrengthRun?
    /// The movement cues, by exercise slug.
    private(set) var cues: [String: [String]] = [:]
    private(set) var previous: [String: JSONValue]?
    /// What became of the last attempt to send the report, in the athlete's words.
    private(set) var status: String?
    private(set) var sending = false
    /// The kept progress could not be written: the athlete keeps the screen open until sent.
    private(set) var unsaved = false
    /// The last signal the session gave on its own, for the screen's haptics.
    private(set) var signal: (StrengthSignal, Int)?
    /// The athlete is typing: a rest does not chain into the next set on its own.
    var editing = false

    let launch: StrengthLaunch
    private let session: NotebookSession
    private var ticking: Task<Void, Never>?
    private var retrying: Task<Void, Never>?
    private var retry = StrengthRetry()
    private var signals = 0

    init(launch: StrengthLaunch, session: NotebookSession) {
        self.launch = launch
        self.session = session
    }

    private var store: any StrengthProgressStore { session.strengthStore }

    // MARK: - Opening

    func open() async {
        guard case .signedIn(let client) = session.state else { return }
        let opened: OpenedStrength
        do {
            opened = OpenedStrength(token: launch.token, view: try await client.strengthSession(launch.token))
            try? store.saveOpened(opened)
        } catch {
            let kept = launch.sid.flatMap(store.loadOpened).flatMap { $0.token == launch.token ? $0 : nil }
            guard error == .unreachable, let kept else {
                phase = Self.failure(error)
                return
            }
            opened = kept
        }
        let sessionView = opened.view.session
        if let expiresAt = sessionView.expiresAt, Date(timeIntervalSince1970: TimeInterval(expiresAt)) < .now {
            phase = .failed("Lien expiré", "Cette séance a plus de sept jours. Redemande-la au coach.")
            return
        }
        guard !sessionView.exercises.isEmpty else {
            phase = .failed("Séance vide", "Ce lien ne porte aucun exercice. Redemande-le au coach.")
            return
        }
        cues = opened.view.cues
        previous = opened.view.previousSession
        run = StrengthRun(session: sessionView, zones: opened.view.zones, saved: store.load(sid: sessionView.sid))
        phase = .running
        startTicking()
        if case .pending? = run?.progress.delivery {
            await send()
        }
    }

    private static func failure(_ error: StrengthLinkError) -> Phase {
        switch error {
        case .expired: .failed("Lien expiré", "Cette séance a expiré ou a été révoquée. Redemande-la au coach.")
        case .forbidden: .failed("Lien invalide", "Cette séance est inconnue. Redemande-la au coach.")
        case .unreachable: .failed("Réseau indisponible",
                                   "Cette séance n’a pas encore été ouverte sur ce téléphone. Reviens avec du réseau pour la charger une première fois.")
        case .unreadable: .failed("Lien incomplet", "Le serveur a répondu de travers. Redemande la séance au coach.")
        }
    }

    // MARK: - Changing the session

    /// Every change goes through here: applied, kept on the device, its signals played.
    func change(_ body: (inout StrengthRun) -> [StrengthSignal]) {
        guard var run else { return }
        let signals = body(&run)
        self.run = run
        save()
        if let last = signals.last {
            self.signals += 1
            signal = (last, self.signals)
        }
    }

    func update(_ body: (inout StrengthRun) -> Void) {
        change { run -> [StrengthSignal] in
            body(&run)
            return []
        }
    }

    private func save() {
        guard let run else { return }
        do {
            try store.save(run.progress, sid: run.session.sid)
            unsaved = false
        } catch {
            unsaved = true
        }
    }

    // MARK: - Timers and the background

    /// Four ticks a second while the screen is open; every reading derives from timestamps.
    private func startTicking() {
        ticking?.cancel()
        ticking = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(250))
                guard let self, var run = self.run, run.progress.timer != nil else { continue }
                // Most ticks change nothing: the screen redraws its figure on its own.
                let signals = run.tick(at: .now, editing: self.editing)
                if run.progress != self.run?.progress {
                    self.change { $0 = run; return signals }
                }
            }
        }
    }

    func enterBackground() {
        update { $0.enterBackground(at: .now) }
    }

    func enterForeground() async {
        update { $0.enterForeground(at: .now) }
        if case .pending? = run?.progress.delivery {
            await send()
        }
    }

    /// The screen closes: the timers stop, the progress stays on the device.
    func close() {
        ticking?.cancel()
        retrying?.cancel()
        save()
    }

    // MARK: - The report

    /// Sends the report; without a network, tries again later and later while the screen
    /// is open, and on every return to the app.
    func send() async {
        guard !sending, case .signedIn(let client) = session.state, var run,
              let report = run.reportToSend(at: .now) else { return }
        self.run = run
        save()
        sending = true
        status = "Envoi…"
        let outcome = await client.sendStrengthReport(report, token: launch.token)
        sending = false
        update { $0.apply(outcome) }
        status = Self.words(for: outcome, kept: !unsaved)
        if outcome.retries {
            scheduleRetry()
        } else {
            retry.reset()
        }
    }

    private func scheduleRetry() {
        guard retrying == nil else { return }
        let wait = retry.next()
        retrying = Task { [weak self] in
            try? await Task.sleep(for: wait)
            guard !Task.isCancelled, let self else { return }
            self.retrying = nil
            await self.send()
        }
    }

    private static func words(for outcome: StrengthReportOutcome, kept: Bool) -> String {
        switch outcome {
        case .accepted: "Envoyé. Le coach a la séance."
        case .duplicate: "Déjà reçu pour ce lien. Aucun nouvel enregistrement."
        case .expired: "Ce lien a expiré. Redemande la séance au coach."
        case .quota: "Envoi refusé : limite de sécurité atteinte. Préviens le coach."
        case .forbidden: "Ce lien est invalide. Redemande la séance au coach."
        case .invalid: "Compte rendu refusé par le serveur. Préviens le coach."
        case .unreachable: kept ? "Pas de réseau pour l’instant. C’est gardé, ça repartira tout seul."
            : "Envoi impossible. Garde cette séance ouverte : elle va réessayer toute seule."
        }
    }

    /// What the session sent, said again when it reopens.
    var sentStatus: String? {
        guard let run else { return nil }
        switch run.progress.delivery {
        case .sent(let duplicate): return duplicate ? Self.words(for: .duplicate, kept: true) : Self.words(for: .accepted, kept: true)
        case .pending: return status ?? Self.words(for: .unreachable, kept: true)
        case .composing: return status
        }
    }
}
