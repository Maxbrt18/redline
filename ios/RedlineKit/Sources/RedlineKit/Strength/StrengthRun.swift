import Foundation

/// A strength session being run: the session from its link, the zones to ask about, and
/// what has been done. Every change goes through a method taking the current time, so
/// the whole walk is testable without a screen or a clock.
///
/// Nothing is required: any exercise can be skipped, any figure left empty.
public struct StrengthRun: Sendable {
    public typealias Session = Notebook.StrengthSession
    public typealias Exercise = Notebook.StrengthExercise
    public typealias Slot = Notebook.StrengthSlot

    /// The seconds between "Démarrer" and the start of a timed set.
    public static let preparationSeconds = 5
    /// A timed set's length when its slot names none.
    public static let defaultSetSeconds = 60
    public static let maxRepetitions = 999
    public static let maxMinutes = 300
    public static let maxNote = 300
    public static let scores = 0...10

    public let session: Session
    public let zones: [Notebook.WatchedZone]
    public private(set) var progress: StrengthProgress
    private let calendar: Calendar

    /// A session opened, or reopened with what was kept of it. A timer other than a rest
    /// asks for confirmation on reopening: the set may or may not have been done.
    public init(session: Session, zones: [Notebook.WatchedZone], saved: StrengthProgress? = nil,
                calendar: Calendar = .current) {
        self.session = session
        self.zones = zones
        self.calendar = calendar
        var progress = saved ?? StrengthProgress()
        progress.current = min(max(progress.current, 0), max(session.exercises.count - 1, 0))
        if let timer = progress.timer {
            if timer.index != progress.current {
                progress.timer = nil
            } else if timer.kind != .rest {
                progress.timer?.confirm = true
            }
        }
        if progress.delivery != .composing {
            progress.review = true
        }
        self.progress = progress
        if !needsOpening {
            settleGate()
        }
    }

    // MARK: - Reading

    public var exercises: [Exercise] { session.exercises }

    public func slots(_ index: Int) -> [Slot] { exercises[index].dose.slots }

    public func step(_ index: Int) -> StepProgress { progress.steps[index] ?? StepProgress() }

    public func isDone(_ index: Int) -> Bool {
        let step = step(index)
        return step.skipped || step.gated || step.stopped || step.slots.count >= slots(index).count
    }

    public var firstUnfinished: Int? { exercises.indices.first { !isDone($0) } }

    /// The slot the current exercise waits for; nil once it is finished, skipped or taken out.
    public var currentSlot: Slot? {
        let step = step(progress.current)
        guard !step.skipped, !step.stopped, !step.gated else { return nil }
        let slots = slots(progress.current)
        return step.slots.count < slots.count ? slots[step.slots.count] : nil
    }

    /// Started and not sent: leaving asks for confirmation.
    public var isUnfinished: Bool { progress.startedAt != nil && progress.delivery == .composing }

    /// Sent, or waiting for the network: nothing changes any more.
    public var isLocked: Bool { progress.delivery != .composing }

    public func pain(_ zone: String, _ moment: PainMoment) -> Int? {
        let readings = progress.pains[zone]
        return moment == .before ? readings?.before : readings?.after
    }

    // MARK: - The opening and the guardrail

    /// The opening pain reading comes first when the session has a guardrail: until it is
    /// answered or skipped there is nothing to run, since it is what arms the guardrail.
    public var needsOpening: Bool { session.gate != nil && !progress.opened }

    /// The zones the guardrail looks at: its own if it names one, otherwise every one.
    public var gateZones: [Notebook.WatchedZone] {
        guard let gate = session.gate else { return [] }
        return zones.filter { gate.zone == nil || $0.zone == gate.zone }
    }

    /// One watched zone above the coach's threshold is enough.
    public var gateFired: Bool {
        guard let gate = session.gate else { return false }
        return gateZones.contains { (pain($0.zone, .before) ?? -1) > gate.painMax }
    }

    /// A reading before the session; the opening closes once every guarded zone has one.
    public mutating func answerOpening(zone: String, pain: Int?) {
        setPain(zone, .before, pain)
        progress.opened = gateZones.allSatisfy { self.pain($0.zone, .before) != nil }
        if progress.opened {
            settleGate()
        }
    }

    public mutating func skipOpening() {
        for zone in gateZones {
            setPain(zone.zone, .before, nil)
        }
        progress.opened = true
        settleGate()
    }

    public mutating func setPain(_ zone: String, _ moment: PainMoment, _ value: Int?) {
        guard !isLocked else { return }
        var readings = progress.pains[zone] ?? PainReadings()
        let clamped = value.map { min(max($0, Self.scores.lowerBound), Self.scores.upperBound) }
        switch moment {
        case .before: readings.before = clamped
        case .after: readings.after = clamped
        }
        progress.pains[zone] = readings
    }

    /// Takes out the exercises the guardrail drops, unless already begun, and moves off
    /// the current one if it is among them.
    private mutating func settleGate() {
        if gateFired, let drop = session.gate?.drop {
            for (index, exercise) in exercises.enumerated() where drop.contains(exercise.slug) {
                var step = step(index)
                if step.slots.isEmpty {
                    step.gated = true
                    progress.steps[index] = step
                }
            }
        }
        if step(progress.current).gated && !progress.review && !progress.viewingRetired {
            moveOn(at: nil)
        }
    }

    // MARK: - The exercises

    /// Banks the current slot (`value` is the count, nil for a set done without one), runs
    /// the rest if the dose has one, and lands on what comes next.
    ///
    /// Rest separates sets and blocks, not sides: a per-leg set is "right then left", and
    /// rest comes once both are done. None follows the session's last slot.
    @discardableResult
    public mutating func validate(_ value: Int?, at now: Date) -> [StrengthSignal] {
        let index = progress.current
        guard !isLocked, currentSlot != nil else { return [] }
        markStarted(at: now)
        remember(index)
        var step = step(index)
        step.slots.append(value.map { min(max($0, 0), Self.maxRepetitions) })
        step.count = 0
        progress.steps[index] = step
        progress.timer = nil

        let slots = slots(index)
        let finished = step.slots.count >= slots.count
        let justDone = slots[step.slots.count - 1]
        let next = finished ? nil : slots[step.slots.count]
        let sameSet = next?.n == justDone.n
        let autoNext = progress.chaining == index && !finished
        let rest = exercises[index].dose.restS ?? 0
        var signals: [StrengthSignal] = []
        if rest > 0 && !sameSet && !(finished && index == exercises.count - 1) {
            startTimer(.rest, seconds: rest, at: now)
            progress.timer?.autoNext = autoNext
        }
        if autoNext, progress.timer == nil, let next {
            signals += prepareWork(seconds: next.t, changingSide: sameSet, at: now)
        }
        if finished {
            moveOn(at: now)
            progress.chaining = nil
            progress.feedback = index
        }
        if progress.timer != nil {
            progress.timer?.index = progress.current
        }
        return signals
    }

    /// Stops the current exercise: kept as partial if some sets are done, skipped otherwise.
    public mutating func skip(at now: Date) {
        guard !isLocked else { return }
        let index = progress.current
        progress.chaining = nil
        remember(index)
        var step = step(index)
        if step.slots.isEmpty {
            step.skipped = true
        } else {
            step.stopped = true
        }
        progress.steps[index] = step
        progress.timer = nil
        moveOn(at: now)
        if step.stopped {
            progress.feedback = index
        }
    }

    /// Goes to the first unfinished exercise, or to the review once there is none.
    public mutating func moveOn(at now: Date?) {
        progress.viewingRetired = false
        if let next = firstUnfinished {
            progress.current = next
        } else {
            progress.review = true
            progress.timer = nil
            if progress.minutes == nil, let startedAt = progress.startedAt, let now {
                let minutes = Int((now.timeIntervalSince(startedAt) / 60).rounded())
                progress.minutes = min(max(1, minutes), Self.maxMinutes)
            }
        }
    }

    /// Puts an exercise on screen, finished or not.
    public mutating func focus(_ index: Int) {
        guard !isLocked, exercises.indices.contains(index) else { return }
        progress.current = index
        progress.chaining = nil
        progress.viewingRetired = step(index).gated
        progress.review = false
        progress.timer = nil
    }

    /// Wipes a finished exercise to do it again; one the guardrail took out stays out.
    public mutating func redo(_ index: Int) {
        guard !isLocked, exercises.indices.contains(index), !step(index).gated else { return }
        remember(index)
        progress.steps[index] = StepProgress()
        progress.review = false
    }

    /// Takes up a stopped exercise where it was left.
    public mutating func resumeStopped(_ index: Int) {
        guard !isLocked, var step = progress.steps[index], step.stopped else { return }
        step.stopped = false
        progress.steps[index] = step
    }

    public var canUndo: Bool { progress.undo != nil && !isLocked }

    /// Takes back the last validation, skip or redo.
    public mutating func undo() {
        guard canUndo, let undo = progress.undo else { return }
        progress.timer = nil
        progress.chaining = nil
        progress.steps[undo.index] = undo.step
        progress.current = undo.index
        progress.review = false
        progress.minutes = undo.minutes
        progress.undo = nil
        progress.feedback = nil
    }

    /// Counts contacts of the current block, from 0 to 999.
    public mutating func count(by delta: Int, at now: Date) {
        guard !isLocked, currentSlot != nil else { return }
        markStarted(at: now)
        var step = step(progress.current)
        step.count = min(max(step.count + delta, 0), Self.maxRepetitions)
        progress.steps[progress.current] = step
    }

    public mutating func setChainsSets(_ chains: Bool) {
        progress.chainsSets = chains
    }

    public mutating func setShowsCues(_ shows: Bool) {
        progress.showsCues = shows
    }

    private mutating func markStarted(at now: Date) {
        guard progress.startedAt == nil else { return }
        progress.startedAt = now
        progress.performedOn = localDate(now)
    }

    private mutating func remember(_ index: Int) {
        progress.undo = StrengthUndo(index: index, step: step(index), review: progress.review, minutes: progress.minutes)
    }

    // MARK: - Timers

    /// Starts a timed set: a few seconds to get ready, then the set.
    @discardableResult
    public mutating func startSet(at now: Date) -> [StrengthSignal] {
        guard !isLocked, let slot = currentSlot else { return [] }
        progress.chaining = progress.chainsSets ? progress.current : nil
        return prepareWork(seconds: slot.t ?? Self.defaultSetSeconds, changingSide: false, at: now)
    }

    /// Stops whatever runs: a preparation cancelled, a rest cut short.
    public mutating func stopTimer() {
        progress.timer = nil
    }

    public mutating func pause(at now: Date) {
        guard let timer = progress.timer, timer.isCountdown, timer.paused == nil else { return }
        progress.timer?.paused = timer.seconds(at: now)
    }

    public mutating func resume(at now: Date) {
        guard let paused = progress.timer?.paused else { return }
        progress.timer?.endsAt = now.addingTimeInterval(paused)
        progress.timer?.paused = nil
    }

    /// After an interruption, runs the timer on from what it showed.
    public mutating func continueAfterInterruption(at now: Date) {
        guard let timer = progress.timer, timer.confirm else { return }
        if timer.isCountdown {
            progress.timer?.endsAt = now.addingTimeInterval(max(1, timer.seconds(at: now)))
        }
        progress.timer?.confirm = false
        progress.timer?.paused = nil
    }

    /// Moves the timers on. A countdown that runs out acts on its own: a preparation starts
    /// the set, a set validates itself, a rest ends. In the background (`background`) a
    /// set or a preparation waits for confirmation instead; while the athlete types
    /// (`editing`) the next set is not prepared on its own.
    @discardableResult
    public mutating func tick(at now: Date, background: Bool = false, editing: Bool = false) -> [StrengthSignal] {
        guard let timer = progress.timer, !timer.confirm, timer.isCountdown, timer.paused == nil else { return [] }
        let left = timer.seconds(at: now)
        if left <= 0 {
            if background && timer.kind != .rest {
                progress.timer?.confirm = true
                return []
            }
            switch timer.kind {
            case .prepare:
                return beginWork(seconds: timer.workSeconds, at: now)
            case .work:
                return [.workEnd] + validate(nil, at: now)
            case .rest:
                let chains = timer.autoNext && progress.chaining == progress.current
                progress.timer = nil
                if chains && !background && !editing, let next = currentSlot {
                    return [.restEnd] + prepareWork(seconds: next.t, changingSide: false, at: now)
                }
                return [.restEnd]
            }
        }
        let second = Int(left.rounded(.up))
        guard second != timer.spoken else { return [] }
        progress.timer?.spoken = second
        return second <= 3 ? [.countdown(second)] : []
    }

    /// The app leaves the screen. A preparation freezes: starting a set nobody sees
    /// would count it without the athlete.
    public mutating func enterBackground(at now: Date) {
        progress.hiddenAt = now
        if let timer = progress.timer, timer.kind == .prepare {
            progress.timer?.paused = max(1, timer.seconds(at: now))
            progress.timer?.confirm = true
        }
    }

    /// The app is back. A set that ran out while away waits for confirmation; a rest that
    /// ran out does not chain into the next set.
    public mutating func enterForeground(at now: Date) {
        guard let timer = progress.timer, let endsAt = timer.endsAt, endsAt <= now else { return }
        switch timer.kind {
        case .work:
            if let hiddenAt = progress.hiddenAt, hiddenAt < endsAt {
                progress.timer?.confirm = true
            }
        case .rest:
            progress.timer?.autoNext = false
        case .prepare:
            break
        }
    }

    private mutating func startTimer(_ kind: StrengthTimer.Kind, seconds: Int?, at now: Date) {
        if let seconds, seconds > 0 {
            progress.timer = StrengthTimer(kind: kind, index: progress.current, endsAt: now.addingTimeInterval(Double(seconds)),
                                           total: seconds)
        } else {
            progress.timer = StrengthTimer(kind: kind, index: progress.current, startedAt: now)
        }
    }

    private mutating func prepareWork(seconds: Int?, changingSide: Bool, at now: Date) -> [StrengthSignal] {
        startTimer(.prepare, seconds: Self.preparationSeconds, at: now)
        progress.timer?.workSeconds = seconds
        return changingSide ? [.changeSide] : []
    }

    private mutating func beginWork(seconds: Int?, at now: Date) -> [StrengthSignal] {
        markStarted(at: now)
        progress.feedback = nil
        startTimer(.work, seconds: seconds, at: now)
        return [.workStart(side: currentSlot?.s)]
    }

    // MARK: - The review and the report

    /// The exercises the review asks about: those with at least one set done.
    public var reviewed: [Int] {
        exercises.indices.filter { index in
            let step = step(index)
            return !step.skipped && !step.gated && !step.slots.isEmpty
        }
    }

    public mutating func setScore(_ index: Int, _ score: Int?) {
        guard !isLocked, exercises.indices.contains(index) else { return }
        var step = step(index)
        step.score = score.map { min(max($0, Self.scores.lowerBound), Self.scores.upperBound) }
        progress.steps[index] = step
    }

    public mutating func setExerciseNote(_ index: Int, _ note: String) {
        guard !isLocked, exercises.indices.contains(index) else { return }
        var step = step(index)
        step.note = String(note.prefix(Self.maxNote))
        progress.steps[index] = step
    }

    public mutating func setNote(_ note: String) {
        guard !isLocked else { return }
        progress.note = String(note.prefix(Self.maxNote))
    }

    public mutating func setMinutes(_ minutes: Int?) {
        guard !isLocked else { return }
        progress.minutes = minutes.map { min(max($0, 0), Self.maxMinutes) }
    }

    /// The report to post, frozen on the first attempt: a retry sends the same one.
    /// Nil once the server has it.
    public mutating func reportToSend(at now: Date) -> Notebook.StrengthReportPosted? {
        switch progress.delivery {
        case .sent: return nil
        case .pending(let report): return report
        case .composing: break
        }
        let performedOn = progress.performedOn ?? localDate(progress.startedAt ?? now)
        progress.performedOn = performedOn
        let report = Notebook.StrengthReportPosted(
            performedOn: performedOn,
            pains: progress.pains.filter { $0.value.before != nil || $0.value.after != nil }
                .mapValues { Notebook.StrengthPainPosted(before: $0.before, after: $0.after) },
            minutes: progress.minutes,
            note: String(progress.note.prefix(Self.maxNote)),
            exercises: exercises.indices.compactMap(reported))
        progress.delivery = .pending(report)
        progress.review = true
        progress.timer = nil
        return report
    }

    /// The server has the report; `duplicate` when it already had it for this link.
    public mutating func delivered(duplicate: Bool) {
        progress.delivery = .sent(duplicate: duplicate)
    }

    /// The server refused the report for good (expired link, quota, invalid): nothing
    /// is retried, and the review can be changed again.
    public mutating func refused() {
        progress.delivery = .composing
    }

    /// What the server answered: kept as sent, kept pending to retry, or back to the review.
    public mutating func apply(_ outcome: StrengthReportOutcome) {
        switch outcome {
        case .accepted: delivered(duplicate: false)
        case .duplicate: delivered(duplicate: true)
        case .unreachable: break
        case .expired, .quota, .forbidden, .invalid: refused()
        }
    }

    private func reported(_ index: Int) -> Notebook.StrengthReportExercise? {
        guard let step = progress.steps[index], !step.isUntouched else { return nil }
        let status: Notebook.StrengthReportExercise.Status =
            step.gated ? .gated
            : step.skipped ? .skipped
            : step.stopped || step.slots.count < slots(index).count ? .partial
            : .completed
        return Notebook.StrengthReportExercise(
            i: index, score: step.score, skipped: step.skipped || step.gated, gated: step.gated, status: status,
            note: step.note, reps: step.gated || step.skipped ? [] : step.slots)
    }

    private func localDate(_ date: Date) -> String {
        let day = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", day.year ?? 0, day.month ?? 0, day.day ?? 0)
    }
}
