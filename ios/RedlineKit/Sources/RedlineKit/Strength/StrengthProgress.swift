import Foundation

/// What a strength session has done so far on this device. Every change is kept on the
/// device at once (`StrengthProgressStore`): the screen locks between two sets, and the
/// app may be closed and reopened in the middle of a session.
public struct StrengthProgress: Codable, Hashable, Sendable {
    /// The first real gesture, never the opening of the session: a link often waits on
    /// the phone for an hour or two before the first exercise.
    public var startedAt: Date?
    /// The local calendar date of `startedAt`, not the day an offline report finally leaves.
    public var performedOn: String?
    /// Per exercise, by its index in the session.
    public var steps: [Int: StepProgress] = [:]
    /// The exercise on screen.
    public var current = 0
    /// The end-of-session review is on screen.
    public var review = false
    /// The opening pain reading is answered or skipped: `false` with every reading nil is
    /// "not asked yet", not "no pain".
    public var opened = false
    /// An exercise the guardrail took out is on screen because the athlete tapped it.
    public var viewingRetired = false
    /// Per watched zone, one reading before the session and one after.
    public var pains: [String: PainReadings] = [:]
    /// What the athlete says to the coach.
    public var note = ""
    /// The session's duration, suggested on reaching the review and editable there.
    public var minutes: Int?
    public var timer: StrengthTimer?
    /// The state before the last validation, skip or redo, to take it back.
    public var undo: StrengthUndo?
    /// The exercise just finished, offered for its difficulty and remark.
    public var feedback: Int?
    /// The athlete wants the sets of a timed exercise chained without a tap.
    public var chainsSets = false
    /// The exercise whose sets are being chained.
    public var chaining: Int?
    /// The movement cues are unfolded, on every exercise.
    public var showsCues = false
    /// When the app last went to the background.
    public var hiddenAt: Date?
    public var delivery: StrengthDelivery = .composing

    public init() {}
}

/// What one exercise has done so far.
public struct StepProgress: Codable, Hashable, Sendable {
    /// One entry per slot validated, in the order of `dose.slots`; nil for a set done
    /// without a count. Positional: the position tells which side a count belongs to.
    public var slots: [Int?] = []
    /// Not done, by the athlete's choice.
    public var skipped = false
    /// Taken out by the pain guardrail: the coach's rule, never the athlete's choice.
    public var gated = false
    /// Stopped after some sets, which are kept.
    public var stopped = false
    /// The contacts of the current block, counted one tap at a time.
    public var count = 0
    /// How hard the exercise was to do properly, from 0 to 10.
    public var score: Int?
    public var note = ""

    public init() {}

    /// Nothing done, nothing decided.
    var isUntouched: Bool { slots.isEmpty && !skipped && !gated && !stopped }
}

public struct PainReadings: Codable, Hashable, Sendable {
    public var before: Int?
    public var after: Int?

    public init(before: Int? = nil, after: Int? = nil) {
        self.before = before
        self.after = after
    }
}

public enum PainMoment: Sendable {
    case before, after
}

public struct StrengthUndo: Codable, Hashable, Sendable {
    public let index: Int
    public let step: StepProgress
    public let review: Bool
    public let minutes: Int?
}

/// Where the report stands. A pending report is frozen as it was first sent and goes
/// out again unchanged until the server answers: the route accepts one report per link.
public enum StrengthDelivery: Codable, Hashable, Sendable {
    case composing
    case pending(Notebook.StrengthReportPosted)
    case sent(duplicate: Bool)
}

/// The countdown or count-up running on the current exercise. Every reading derives from
/// a timestamp, never from ticks: nothing ticks while the app is in the background.
public struct StrengthTimer: Codable, Hashable, Sendable {
    public enum Kind: String, Codable, Sendable {
        /// The seconds before a timed set starts.
        case prepare
        case work
        case rest
    }

    public var kind: Kind
    public var index: Int
    /// When a countdown reaches zero; nil for a count-up.
    public var endsAt: Date?
    /// When a count-up started; nil for a countdown.
    public var startedAt: Date?
    /// The countdown's full length, in seconds.
    public var total: Int?
    /// The seconds left, frozen while paused.
    public var paused: Double?
    /// The timer ran out, or was frozen, while the app was away: what was done is
    /// confirmed before going on.
    public var confirm = false
    /// At the end of this rest, the next set is prepared without a tap.
    public var autoNext = false
    /// For a preparation: the seconds of the set it leads to.
    public var workSeconds: Int?
    /// The last countdown second signalled, so that each one sounds once.
    var spoken: Int?

    /// Seconds left on a countdown (negative once past zero), seconds elapsed on a count-up.
    public func seconds(at now: Date) -> Double {
        if let startedAt { return now.timeIntervalSince(startedAt) }
        if let paused { return paused }
        return endsAt.map { $0.timeIntervalSince(now) } ?? 0
    }

    public var isCountdown: Bool { endsAt != nil }
}

/// What the screen plays or says when the session moves on its own.
public enum StrengthSignal: Hashable, Sendable {
    /// One of the last three seconds of a countdown.
    case countdown(Int)
    /// A timed set starts; `side` is the leg of a per-leg dose.
    case workStart(side: Notebook.StrengthSlot.S?)
    case workEnd
    case restEnd
    /// The next set is the other leg.
    case changeSide
}
