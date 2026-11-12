import Foundation
import Testing
@testable import RedlineKit

struct StatedDayTests {
    private func json(_ value: some Encodable) throws -> [String: JSONValue] {
        try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(value))
    }

    @Test func onlyTheDetailsOfItsKindTravel() throws {
        var day = StatedDay.fresh(date: "2030-01-07", kind: .withOthers)
        day.sport = "Volley"
        #expect(try json(day) == ["date": .string("2030-01-07"), "kind": .string("with_others"), "minutes": .number(60),
                                  "pace": .string("easy"), "required": .bool(false)])
        day.kind = .off
        #expect(try json(day) == ["date": .string("2030-01-07"), "kind": .string("off")])
    }

    @Test func aDayReadBackKeepsItsDetails() throws {
        let state = Notebook.DayState(kind: .short, minutes: 30, habit: true)
        #expect(try json(StatedDay(date: "2030-01-08", state: state))
                == ["date": .string("2030-01-08"), "kind": .string("short"), "minutes": .number(30)])
    }

    @Test func aHabitNamesItsWeekdayInsteadOfItsDate() throws {
        let day = StatedDay.fresh(date: "2030-01-09", kind: .otherSport)
        #expect(try json(day.habit(weekday: 3)) == ["weekday": .number(3), "kind": .string("other_sport"),
                                                    "sport": .string(StatedDay.unnamedSport), "demanding": .bool(false),
                                                    "run_too": .bool(false)])
    }
}
