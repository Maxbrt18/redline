import Foundation
import Testing
@testable import RedlineKit

struct DayTests {
    @Test func theAPIsDayIsReadAsWritten() throws {
        let day = try #require(Day("2026-11-11"))
        #expect((day.year, day.month, day.day) == (2026, 11, 11))
    }

    @Test(arguments: ["2026-02-30", "2026-11", "11/11/2026", "2026-1-01", ""])
    func whatIsNotADayIsRefused(text: String) {
        #expect(Day(text) == nil)
    }

    @Test(arguments: [("2026-11-11", "2026-W46"), ("2026-12-31", "2026-W53"), ("2027-01-04", "2027-W01"),
                      ("2021-01-03", "2020-W53")])
    func aDayKnowsItsISOWeek(text: String, week: String) throws {
        #expect(try #require(Day(text)).isoWeek == week)
    }

    @Test func aDayIsSpelledInFrench() throws {
        let day = try #require(Day("2026-11-11"))
        #expect(day.spelled() == "mercredi 11 novembre")
        #expect(day.spelled(weekday: false) == "11 novembre")
    }

    @Test func thePhonesDateGivesItsOwnDay() throws {
        var paris = Calendar(identifier: .gregorian)
        paris.timeZone = TimeZone(identifier: "Europe/Paris")!
        let lateEvening = try #require(paris.date(from: DateComponents(year: 2026, month: 11, day: 11, hour: 23, minute: 30)))
        #expect(Day(lateEvening, calendar: paris) == Day("2026-11-11"))
    }

    @Test func aDayMovesAcrossMonthsAndYears() throws {
        #expect(try #require(Day("2026-10-31")).adding(1).text == "2026-11-01")
        #expect(try #require(Day("2027-01-01")).adding(-1).text == "2026-12-31")
    }
}
