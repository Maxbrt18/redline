import Testing
@testable import RedlineKit

struct NumbersTests {
    @Test(arguments: [(12.4, 1, "12,4"), (12.0, 1, "12"), (9.876, 2, "9,88"), (151.4, 0, "151")])
    func aFigureTakesTheFrenchComma(value: Double, digits: Int, text: String) {
        #expect(Numbers.text(value, digits: digits) == text)
    }

    @Test(arguments: [(307.0, "5:07"), (359.6, "6:00"), (60.0, "1:00")])
    func aPaceIsMinutesAndSeconds(seconds: Double, text: String) {
        #expect(Numbers.pace(seconds) == text)
    }

    @Test func aClockNeverBreaksAndDropsZeroMinutes() {
        #expect(Numbers.clock("19:05") == "19\u{00a0}h\u{00a0}05")
        #expect(Numbers.clock("07:00") == "7\u{00a0}h")
        #expect(Numbers.clock("2026-10-06T07:12:00") == "7\u{00a0}h\u{00a0}12")
        #expect(Numbers.clock("bientôt") == nil)
    }

    @Test func aFigureClockKeepsItsMinutes() {
        #expect(Numbers.figureClock("19:05") == "19:05")
        #expect(Numbers.figureClock("2026-10-06T07:00:00") == "7:00")
        #expect(Numbers.figureClock("bientôt") == nil)
    }

    @Test func aDurationShowsHoursOnlyWhenThereAreSome() {
        #expect(Numbers.duration(3127) == "52:07")
        #expect(Numbers.duration(3870) == "1:04:30")
        #expect(Numbers.minutes(719) == "12′")
    }

    @Test func aRaceTimeReadsInHoursAndMinutes() {
        #expect(Numbers.hours(4.21) == "4\u{00a0}h\u{00a0}13")
        #expect(Numbers.hours(0.99) == "0\u{00a0}h\u{00a0}59")
    }
}
