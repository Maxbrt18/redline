import Foundation
import Testing
@testable import RedlineKit

struct RaceTimingTests {
    let plan: Notebook.RacePlan = {
        let url = Bundle.module.url(forResource: "race-plan", withExtension: "json", subdirectory: "Fixtures")!
        return try! JSONDecoder().decode(Notebook.RacePlanView.self, from: Data(contentsOf: url)).plan
    }()

    @Test func atTheReferencePaceThePassagesAreTheServices() {
        let passages = plan.passages(pace: plan.referencePaceS)
        #expect(passages.map(\.hours) == plan.passages.map(\.hours))
        #expect(passages.last?.name == "Arrivée" && passages.last?.finish == true)
        #expect(passages[0].carbs == 1.42 * 60)
    }

    @Test func aSlowerPaceStretchesTheRunningButNotTheStops() throws {
        let slower = plan.passages(pace: plan.referencePaceS * 1.1)
        let stop = plan.stopMinutes / 60
        #expect(abs(slower[1].hours - ((2.85 - stop) * 1.1 + stop)) < 1e-9)
        let likely = slower[1].likely
        #expect(abs(likely.upperBound / slower[1].hours - 1.04) < 1e-9)
    }

    @Test func aPassageIsAWallClockTimeAndAForecastHour() {
        #expect(plan.wallClock(after: 4.21) == "2026-11-26T13:13")
        #expect(plan.forecastHour(after: 4.21) == "2026-11-26T13:00")
        #expect(plan.forecastHour(after: 4.6) == "2026-11-26T14:00")
    }

    @Test func theAltitudeIsReadBetweenTheProfilesPoints() {
        #expect(plan.height(atKm: 2.5) == 365)
        #expect(plan.height(atKm: 0) == 320)
    }

    @Test func aClimbsFootScalesItsRunningTimeOnly() {
        let climb = plan.climbs[1]
        #expect(abs(plan.foot(of: climb, pace: plan.referencePaceS * 2) - (climb.runningHours * 2 + climb.hours - climb.runningHours)) < 1e-9)
    }
}

struct SkyTests {
    @Test func theCodeGivesTheLookAndTheWord() {
        #expect(Sky(code: 2, day: true, rain: 0).word == "éclaircies")
        #expect(Sky(code: 0, day: false, rain: 0).look == .moon)
        #expect(Sky(code: 63, day: true, rain: 80).wet)
        #expect(Sky(code: nil, day: true, rain: 60).word == "pluie probable")
    }

    @Test func nightIsStatedAgainstTheSession() {
        let forecast = Notebook.Forecast(place: "Paris", date: "2026-10-09", hours: [], sunrise: "07:55", sunset: "19:05")
        #expect(forecast.night(start: "17:00", minutes: 60)?.text == "Nuit à 19\u{a0}h\u{a0}05 : tu rentres plus d’une heure avant.")
        #expect(forecast.night(start: "18:30", minutes: 45)?.text == "Nuit à 19\u{a0}h\u{a0}05 : elle tombe pendant ta séance.")
        #expect(forecast.night(start: "06:30", minutes: nil)?.sunrise == true)
        #expect(forecast.night(start: nil, minutes: nil)?.text == "Nuit à 19\u{a0}h\u{a0}05.")
    }
}
