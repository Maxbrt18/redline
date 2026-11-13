import Foundation
#if canImport(ActivityKit) && os(iOS)
import ActivityKit
#endif

/// What the Live Activity of a strength session shows, built from the run at each change
/// and never per second: the system animates a countdown from its dates on its own.
public struct StrengthLiveState: Codable, Hashable, Sendable {
    public enum Phase: String, Codable, Sendable {
        /// A set to do, without a timer.
        case ready
        /// The seconds before a timed set.
        case prepare
        /// A timed set.
        case work
        case rest
        /// Every exercise is behind: the review is on screen.
        case done
    }

    public var phase: Phase
    /// When the countdown reaches zero; nil when nothing counts down.
    public var endsAt: Date?
    /// When a count-up started.
    public var startedAt: Date?
    /// The countdown's full length, in seconds.
    public var total: Int?
    /// The seconds left on a frozen countdown.
    public var pausedLeft: Double?
    /// The timer ran out, or froze, while the app was away: the app asks what was done.
    public var interrupted = false
    /// The figure when nothing counts: « 10–12 », « 20 », « 38 ».
    public var figure: String
    /// Beside the figure: « Repos », « Droite », « min ».
    public var label: String
    /// Under the label: « sur 1:00 », « répétitions ».
    public var detail: String
    /// Before a rest's exercise: « Ensuite ».
    public var lead: String?
    public var title: String
    public var subtitle: String
    /// The side's initial, for the compact island: « D », « G ».
    public var side: String?
    /// Per exercise, per set: how much of it is done, from 0 to 1.
    public var sets: [[Double]]
    /// The set in progress, by exercise and by set.
    public var exercise: Int?
    public var set: Int?

    /// A countdown the system moves on by itself.
    public var counts: Bool { endsAt != nil && pausedLeft == nil && !interrupted }

    /// Nothing moves a countdown on once it ends with the app away: the activity is stale.
    public var staleDate: Date? { counts ? endsAt : nil }

    /// When the countdown began, for the line that empties.
    public var countdownStart: Date? { endsAt.map { $0.addingTimeInterval(-Double(total ?? 0)) } }

    /// Nil before the first gesture, a set done or a timer started: a link often waits on
    /// the phone before the session.
    public init?(run: StrengthRun, at now: Date) {
        let progress = run.progress
        guard progress.startedAt != nil || progress.timer != nil else { return nil }
        sets = run.exercises.indices.map { Self.fills(run, $0) }

        if progress.review {
            let done = run.exercises.indices.filter { !run.step($0).slots.isEmpty }.count
            phase = .done
            figure = "\(progress.minutes ?? 0)"
            label = "min"
            detail = done == 1 ? "1 exercice" : "\(done) exercices"
            title = "Séance terminée"
            subtitle = progress.delivery == .composing ? "Le bilan t’attend dans l’app" : "Bilan envoyé au coach"
            return
        }

        let index = progress.current
        let exercise = run.exercises[index]
        let measure = exercise.dose.measure
        title = exercise.name
        guard let slot = run.currentSlot else {
            let finished = run.exercises.indices.filter(run.isDone).count
            phase = .ready
            figure = "\(finished)/\(run.exercises.count)"
            label = "Exercices"
            detail = "faits"
            subtitle = "Reviens à la séance"
            return
        }
        side = StrengthWords.side(slot.s).map { String($0.prefix(1)).uppercased() }
        self.exercise = index
        set = Self.numbers(exercise).firstIndex(of: slot.n)
        let load = exercise.load.map(StrengthWords.load)

        guard let timer = progress.timer else {
            phase = .ready
            figure = Self.figure(exercise, slot)
            label = StrengthWords.side(slot.s)?.capitalizedFirst ?? StrengthWords.unit(measure)
            detail = Self.unit(measure)
            subtitle = [StrengthWords.position(slot, measure: measure) ?? "Une seule série", load]
                .compactMap { $0 }.joined(separator: " · ")
            return
        }
        endsAt = timer.endsAt
        startedAt = timer.startedAt
        total = timer.total
        interrupted = timer.confirm
        pausedLeft = timer.paused ?? (timer.confirm && timer.isCountdown ? max(0, timer.seconds(at: now)) : nil)
        figure = Self.timer(pausedLeft ?? Double(timer.total ?? 0))
        detail = timer.total.map { "sur " + StrengthWords.duration($0) } ?? ""
        let position = [StrengthWords.position(slot, measure: measure) ?? "Une seule série",
                        StrengthWords.side(slot.s)].compactMap { $0 }.joined(separator: " · ")
        switch timer.kind {
        case .rest:
            phase = .rest
            label = pausedLeft == nil ? "Repos" : "En pause"
            lead = "Ensuite"
            subtitle = [StrengthWords.slot(slot, measure: measure), Self.amount(exercise, slot), load]
                .compactMap { $0 }.joined(separator: " · ")
        case .work:
            phase = .work
            label = timer.confirm ? "À confirmer" : pausedLeft == nil ? "À tenir" : "En pause"
            subtitle = position
        case .prepare:
            phase = .prepare
            label = timer.confirm ? "À reprendre" : "Prépare-toi"
            detail = timer.workSeconds.map { "puis \(StrengthWords.duration($0)) à tenir" } ?? ""
            subtitle = position
        }
    }

    /// The set numbers of an exercise, in order: a per-leg set is one set with two sides.
    static func numbers(_ exercise: Notebook.StrengthExercise) -> [Int] {
        Array(Set(exercise.dose.slots.map(\.n))).sorted()
    }

    /// One fill per set; a set with two sides fills by halves.
    static func fills(_ run: StrengthRun, _ index: Int) -> [Double] {
        let slots = run.slots(index)
        let banked = run.step(index).slots.count
        return numbers(run.exercises[index]).map { number in
            let positions = slots.indices.filter { slots[$0].n == number }
            guard !positions.isEmpty else { return 0 }
            return Double(positions.filter { $0 < banked }.count) / Double(positions.count)
        }
    }

    /// A frozen timer as the system draws a running one: « 0:22 », « 1:30 ».
    static func timer(_ seconds: Double) -> String {
        let whole = max(0, Int(seconds.rounded(.up)))
        return "\(whole / 60):" + String(format: "%02d", whole % 60)
    }

    /// The figure of a set waiting to be done.
    static func figure(_ exercise: Notebook.StrengthExercise, _ slot: Notebook.StrengthSlot) -> String {
        let dose = exercise.dose
        switch dose.measure {
        case .reps:
            guard let first = dose.counts?.first, let last = dose.counts?.last else { return "\(slot.n)/\(slot.of)" }
            return first == last ? "\(first)" : "\(first)–\(last)"
        case .time: return StrengthWords.clock(Double(slot.t ?? StrengthRun.defaultSetSeconds))
        case .contacts: return "\(slot.t ?? 0)"
        case .amrap: return "Max"
        case .unknown: return "\(slot.n)/\(slot.of)"
        }
    }

    /// What the figure counts.
    static func unit(_ measure: Notebook.StrengthDose.Measure) -> String {
        switch measure {
        case .reps: "répétitions"
        case .time: "secondes à tenir"
        case .contacts: "contacts"
        case .amrap: "jusqu’à l’échec"
        case .unknown: ""
        }
    }

    /// How much the next set asks, after a rest: « 10–12 rép. », « 45 s ».
    static func amount(_ exercise: Notebook.StrengthExercise, _ slot: Notebook.StrengthSlot) -> String? {
        switch exercise.dose.measure {
        case .reps: exercise.dose.counts?.isEmpty == false ? figure(exercise, slot) + " rép." : nil
        case .time: StrengthWords.duration(slot.t ?? StrengthRun.defaultSetSeconds)
        case .contacts: "\(slot.t ?? 0) contacts"
        case .amrap: "max"
        case .unknown: nil
        }
    }
}

#if canImport(ActivityKit) && os(iOS)
/// The strength session's Live Activity: the session it follows, and its live state.
public struct StrengthActivityAttributes: ActivityAttributes {
    public typealias ContentState = StrengthLiveState

    /// The session followed, to find its progress again on the device.
    public let sid: String

    public init(sid: String) {
        self.sid = sid
    }
}
#endif
