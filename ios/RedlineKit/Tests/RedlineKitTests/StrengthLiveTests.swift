import Foundation
import Testing
@testable import RedlineKit

/// The Live Activity's state, read from the fixture's session (`StrengthRunTests`).
struct StrengthLiveTests {
    let runs = StrengthRunTests()

    func at(_ seconds: Double) -> Date { runs.at(seconds) }

    @Test func nothingShowsBeforeTheFirstGesture() {
        #expect(StrengthLiveState(run: runs.ungated(), at: at(0)) == nil)
    }

    @Test func aSetToDoShowsItsSideAndItsRange() throws {
        var run = runs.ungated()
        run.validate(12, at: at(0))
        let state = try #require(StrengthLiveState(run: run, at: at(5)))
        #expect(state.phase == .ready)
        #expect(state.figure == "10–12")
        #expect(state.label == "Gauche")
        #expect(state.detail == "répétitions")
        #expect(state.title == "Mollets sur une jambe")
        #expect(state.subtitle == "Série 1 sur 2 · 1 × 8 kg")
        #expect(state.side == "G")
        #expect(state.sets[0] == [0.5, 0])
        #expect(state.exercise == 0 && state.set == 0)
        #expect(state.staleDate == nil)
    }

    @Test func aRestCountsDownToWhatComesNext() throws {
        var run = runs.ungated()
        run.validate(12, at: at(0))
        run.validate(11, at: at(30))
        let state = try #require(StrengthLiveState(run: run, at: at(31)))
        #expect(state.phase == .rest)
        #expect(state.endsAt == at(90))
        #expect(state.countdownStart == at(30))
        #expect(state.staleDate == at(90))
        #expect(state.label == "Repos")
        #expect(state.detail == "sur 1:00")
        #expect(state.lead == "Ensuite")
        #expect(state.subtitle == "Série 2 · droite · 10–12 rép. · 1 × 8 kg")
        #expect(state.sets[0] == [1, 0])
        #expect(state.set == 1)
    }

    @Test func aPausedCountdownIsNeverStale() throws {
        var run = runs.ungated()
        run.validate(12, at: at(0))
        run.validate(11, at: at(30))
        run.pause(at: at(50))
        let state = try #require(StrengthLiveState(run: run, at: at(51)))
        #expect(state.pausedLeft == 40)
        #expect(state.label == "En pause")
        #expect(state.figure == "0:40")
        #expect(!state.counts)
        #expect(state.staleDate == nil)
    }

    @Test func aTimedSetHoldsItsLength() throws {
        var run = runs.ungated()
        run.skip(at: at(0))
        run.startSet(at: at(10))
        let preparing = try #require(StrengthLiveState(run: run, at: at(11)))
        #expect(preparing.phase == .prepare)
        #expect(preparing.label == "Prépare-toi")
        #expect(preparing.detail == "puis 45 s à tenir")
        run.tick(at: at(15))
        let holding = try #require(StrengthLiveState(run: run, at: at(16)))
        #expect(holding.phase == .work)
        #expect(holding.label == "À tenir")
        #expect(holding.detail == "sur 45 s")
        #expect(holding.title == "Gainage ventral")
        #expect(holding.subtitle == "Série 1 sur 2")
        #expect(holding.endsAt == at(60))
    }

    @Test func aSetThatRanOutAwayWaitsForTheAthlete() throws {
        var run = runs.ungated()
        run.skip(at: at(0))
        run.startSet(at: at(10))
        run.tick(at: at(15))
        run.tick(at: at(70), background: true)
        let state = try #require(StrengthLiveState(run: run, at: at(71)))
        #expect(state.interrupted)
        #expect(state.label == "À confirmer")
        #expect(state.staleDate == nil)
    }

    @Test func theReviewShowsTheSessionDone() throws {
        var run = runs.ungated()
        run.validate(12, at: at(0))
        for _ in 0..<4 { run.skip(at: at(600)) }
        let state = try #require(StrengthLiveState(run: run, at: at(601)))
        #expect(state.phase == .done)
        #expect(state.figure == "10")
        #expect(state.label == "min")
        #expect(state.detail == "1 exercice")
        #expect(state.title == "Séance terminée")
        #expect(state.exercise == nil)
    }

    @Test func theStateStaysSmall() throws {
        var run = runs.ungated()
        run.validate(12, at: at(0))
        run.validate(11, at: at(30))
        let state = try #require(StrengthLiveState(run: run, at: at(31)))
        // ActivityKit refuses more than 4 KB of attributes and state together.
        #expect(try JSONEncoder().encode(state).count < 1_024)
    }
}
