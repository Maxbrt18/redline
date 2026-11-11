/// How the athlete says a day presents itself, in words of what they can do. Only the
/// details of its kind travel (`Notebook.dayDetails`): a day read back from the server
/// carries the others as null, and the server refuses them.
public struct StatedDay: Hashable, Sendable, Encodable {
    public typealias Kind = Notebook.DayState.Kind
    public typealias Pace = Notebook.DayState.Pace

    public var date: String
    public var kind: Kind
    public var minutes: Int?
    public var pace: Pace?
    public var required: Bool?
    public var sport: String?
    public var demanding: Bool?
    public var runToo: Bool?

    public init(date: String, kind: Kind = .asPlanned, minutes: Int? = nil, pace: Pace? = nil, required: Bool? = nil,
                sport: String? = nil, demanding: Bool? = nil, runToo: Bool? = nil) {
        self.date = date
        self.kind = kind
        self.minutes = minutes
        self.pace = pace
        self.required = required
        self.sport = sport
        self.demanding = demanding
        self.runToo = runToo
    }

    /// The day as the server knows it: stated for that date, or given by its habit.
    public init(date: String, state: Notebook.DayState) {
        self.init(date: date, kind: state.kind, minutes: state.minutes, pace: state.pace, required: state.required,
                  sport: state.sport, demanding: state.demanding, runToo: state.runToo)
    }

    /// A kind chosen anew starts from its usual details.
    public static func fresh(date: String, kind: Kind) -> StatedDay {
        switch kind {
        case .short: StatedDay(date: date, kind: kind, minutes: 45)
        case .withOthers: StatedDay(date: date, kind: kind, minutes: 60, pace: .easy, required: false)
        case .otherSport: StatedDay(date: date, kind: kind, sport: unnamedSport, demanding: false, runToo: false)
        default: StatedDay(date: date, kind: kind)
        }
    }

    /// The name a sport keeps until the athlete types one; the engine never reads it.
    public static let unnamedSport = "Autre sport"

    private enum Keys: String, CodingKey {
        case date, kind, minutes, pace, required, sport, demanding, weekday
        case runToo = "run_too"
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: Keys.self)
        try container.encode(date, forKey: .date)
        try encodeState(into: &container)
    }

    /// The day as a weekly habit: its ISO weekday instead of its date.
    public func habit(weekday: Int) -> some Encodable & Sendable {
        Habit(day: self, weekday: weekday)
    }

    private struct Habit: Encodable, Sendable {
        let day: StatedDay
        let weekday: Int

        func encode(to encoder: any Encoder) throws {
            var container = encoder.container(keyedBy: Keys.self)
            try container.encode(weekday, forKey: .weekday)
            try day.encodeState(into: &container)
        }
    }

    private func encodeState(into container: inout KeyedEncodingContainer<Keys>) throws {
        try container.encode(kind.rawValue, forKey: .kind)
        for name in Notebook.dayDetails[kind.rawValue] ?? [] {
            switch name {
            case "minutes": try container.encodeIfPresent(minutes, forKey: .minutes)
            case "pace": try container.encodeIfPresent(pace?.rawValue, forKey: .pace)
            case "required": try container.encodeIfPresent(required, forKey: .required)
            case "sport": try container.encodeIfPresent(sport, forKey: .sport)
            case "demanding": try container.encodeIfPresent(demanding, forKey: .demanding)
            case "run_too": try container.encodeIfPresent(runToo, forKey: .runToo)
            default: break
            }
        }
    }
}
