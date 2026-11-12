import Foundation

/// The race plan timed at any pace. The service times the passages at one reference pace;
/// running time is proportional to the pace and the stops are not, so any pace retimes
/// them on the device, without a request. Exploring a pace records nothing.
public extension Notebook.RacePlan {
    /// An aid station or the finish, timed at one pace, with the model's bands.
    struct TimedPassage: Hashable, Sendable {
        public let km: Double
        /// The station's name; « Arrivée » at the finish.
        public let name: String
        public let finish: Bool
        /// Hours since the start, the stops already made included.
        public let hours: Double
        /// The band of the median error, then nine times out of ten.
        public let likely: ClosedRange<Double>
        public let nineInTen: ClosedRange<Double>
        /// The carbohydrates of the stretch that ends here, in grams.
        public let carbs: Double?
    }

    func passages(pace seconds: Double) -> [TimedPassage] {
        let scale = seconds / referencePaceS, stop = stopMinutes / 60
        let median = modelError.first ?? 0, nine = modelError.dropFirst().first ?? median
        return passages.enumerated().map { index, item in
            // The service's hours include one stop per aid station already passed.
            let hours = (item.hours - Double(index) * stop) * scale + Double(index) * stop
            return TimedPassage(
                km: item.km, name: item.name.isEmpty ? "Arrivée" : item.name, finish: item.name.isEmpty, hours: hours,
                likely: hours * (1 - median)...hours * (1 + median),
                nineInTen: hours * (1 - nine)...hours * (1 + nine),
                carbs: carbsGH.map { item.runningHours * scale * Double($0) })
        }
    }

    /// When the foot of a climb comes at this pace: its running time scales, its stops do not.
    func foot(of climb: Notebook.RouteClimb, pace seconds: Double) -> Double {
        climb.runningHours * seconds / referencePaceS + climb.hours - climb.runningHours
    }

    /// The climbs a runner would name; the others are bumps.
    var mainClimbs: [Notebook.RouteClimb] { climbs.filter(\.main) }

    /// The altitude at any kilometre, between the profile's points.
    func height(atKm km: Double) -> Double {
        guard let after = profile.firstIndex(where: { $0[0] >= km }) else { return profile.last?[1] ?? 0 }
        guard after > 0 else { return profile[0][1] }
        let (k0, m0, k1, m1) = (profile[after - 1][0], profile[after - 1][1], profile[after][0], profile[after][1])
        return m0 + (m1 - m0) * (km - k0) / (k1 - k0)
    }

    /// The local time `hours` after the start, to the minute, as `YYYY-MM-DDTHH:MM`; nil
    /// without a start time. The start is a wall-clock time at the race's place.
    func wallClock(after hours: Double) -> String? {
        guard let start, let date = RaceClock.parse(start) else { return nil }
        let minutes = (hours * 60).rounded()
        return RaceClock.text(date.addingTimeInterval(minutes * 60))
    }

    /// The forecast hour of a passage: its local time to the nearest hour, `YYYY-MM-DDTHH:00`.
    func forecastHour(after hours: Double) -> String? {
        guard let start, let date = RaceClock.parse(start) else { return nil }
        let minutes = (hours * 60).rounded() + 30
        return String(RaceClock.text(date.addingTimeInterval(minutes * 60)).prefix(13)) + ":00"
    }
}

/// Local wall-clock times with no zone: read and written in GMT so that no offset or
/// daylight change moves them.
private enum RaceClock {
    static func parse(_ text: String) -> Date? {
        try? Date.ISO8601FormatStyle(timeZone: .gmt).year().month().day().time(includingFractionalSeconds: false)
            .parse(text.count == 16 ? text + ":00" : text)
    }

    static func text(_ date: Date) -> String {
        let parts = Calendar(identifier: .gregorian).dateComponents(in: .gmt, from: date)
        return String(format: "%04d-%02d-%02dT%02d:%02d", parts.year!, parts.month!, parts.day!, parts.hour!, parts.minute!)
    }
}
