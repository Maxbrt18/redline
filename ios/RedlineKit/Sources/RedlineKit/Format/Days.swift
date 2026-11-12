import Foundation

/// A calendar day as the API writes it (`YYYY-MM-DD`), with no time and no time zone: the
/// athlete's day, wherever the phone is.
public struct Day: Sendable, Hashable, Comparable {
    public let year: Int
    public let month: Int
    public let day: Int

    public init?(_ text: String) {
        guard text.wholeMatch(of: /\d{4}-\d{2}-\d{2}/) != nil else { return nil }
        let parts = text.split(separator: "-").compactMap { Int($0) }
        guard let date = Self.calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])),
              Self.calendar.component(.day, from: date) == parts[2]
        else { return nil }
        year = parts[0]
        month = parts[1]
        day = parts[2]
    }

    /// The day `date` falls on, on the phone's calendar.
    public init(_ date: Date, calendar: Calendar = .current) {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        year = parts.year!
        month = parts.month!
        day = parts.day!
    }

    /// Noon of the day in UTC: formatted in UTC, it never slips to a neighbour.
    public var date: Date {
        Self.calendar.date(from: DateComponents(year: year, month: month, day: day, hour: 12))!
    }

    /// The ISO week the day belongs to, as the API names it (`YYYY-Www`).
    public var isoWeek: String {
        var iso = Calendar(identifier: .iso8601)
        iso.timeZone = Self.calendar.timeZone
        let parts = iso.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        return String(format: "%04d-W%02d", parts.yearForWeekOfYear!, parts.weekOfYear!)
    }

    /// The day in words, in `locale`: « mercredi 11 novembre ».
    public func spelled(locale: Locale = Locale(identifier: "fr_FR"), weekday: Bool = true) -> String {
        var style = Date.FormatStyle(timeZone: Self.calendar.timeZone).locale(locale).day().month(.wide)
        if weekday { style = style.weekday(.wide) }
        return date.formatted(style)
    }

    /// The day `count` days later (earlier when negative).
    public func adding(_ count: Int) -> Day {
        Day(Self.calendar.date(byAdding: .day, value: count, to: date)!, calendar: Self.calendar)
    }

    /// The ISO-format text of the day, as the API writes it.
    public var text: String { String(format: "%04d-%02d-%02d", year, month, day) }

    public static func < (lhs: Day, rhs: Day) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }

    private static let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }()
}
