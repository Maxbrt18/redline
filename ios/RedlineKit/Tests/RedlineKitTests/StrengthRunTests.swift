import Foundation
import Testing
@testable import RedlineKit

/// The fixture's session: per-leg calf raises (2 sets, rest 60 s), a timed plank (2 × 45 s,
/// rest 30 s), jumps in two blocks of 10 contacts (rest 30 s, dropped by the knee
/// guardrail above 3), push-ups to failure.
struct StrengthRunTests {
    static let view: Notebook.StrengthSessionView = {
        let url = Bundle.module.url(forResource: "strength-session", withExtension: "json", subdirectory: "Fixtures")!
        return try! JSONDecoder().decode(Notebook.StrengthSessionView.self, from: Data(contentsOf: url))
    }()

    let start = Date(timeIntervalSince1970: 1_760_000_000)
    let paris: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Paris")!
        return calendar
    }()

    /// The session without its guardrail, so that it opens on the first exercise.
    func ungated(saved: StrengthProgress? = nil) -> StrengthRun {
        let session = Self.view.session
        let plain = Notebook.StrengthSession(
            sid: session.sid, session: session.session, title: session.title, rule: session.rule, date: session.date,
            durationMin: session.durationMin, note: session.note, exercises: session.exercises,
            expiresAt: session.expiresAt)
        return StrengthRun(session: plain, zones: Self.view.zones, saved: saved, calendar: paris)
    }

    func gated() -> StrengthRun {
        StrengthRun(session: Self.view.session, zones: Self.view.zones, calendar: paris)
    }

    func at(_ seconds: Double) -> Date { start.addingTimeInterval(seconds) }

    // MARK: - Sets, sides and rest

    @Test func openingTheSessionIsNotStartingIt() {
        var run = ungated()
        #expect(run.progress.startedAt == nil)
        #expect(!run.isUnfinished)
        run.validate(12, at: at(0))
        #expect(run.progress.startedAt == at(0))
        #expect(run.progress.performedOn == "2025-10-09")
        #expect(run.isUnfinished)
    }

    @Test func aPerLegSetRestsOnlyOnceBothSidesAreDone() {
        var run = ungated()
        #expect(run.currentSlot?.s == .d)
        run.validate(12, at: at(0))
        #expect(run.progress.timer == nil)
        #expect(run.currentSlot?.s == .g)
        run.validate(11, at: at(30))
        #expect(run.progress.timer?.kind == .rest)
        #expect(run.progress.timer?.endsAt == at(90))
        #expect(run.step(0).slots == [12, 11])
    }

    @Test func theLastSetOfAnExerciseMovesOnAndOffersItsFeedback() {
        var run = ungated()
        for count in [12, 12, 11, 10] {
            run.validate(count, at: at(0))
        }
        #expect(run.isDone(0))
        #expect(run.progress.current == 1)
        #expect(run.progress.feedback == 0)
        #expect(run.progress.timer?.kind == .rest)
        #expect(run.progress.timer?.index == 1)
    }

    @Test func aCountIsKeptBetweenZeroAnd999() {
        var run = ungated()
        run.validate(-3, at: at(0))
        run.validate(5000, at: at(0))
        #expect(run.step(0).slots == [0, 999])
    }

    @Test func aRestRunsOutOnItsOwn() {
        var run = ungated()
        run.validate(12, at: at(0))
        run.validate(12, at: at(0))
        #expect(run.tick(at: at(57)) == [.countdown(3)])
        #expect(run.tick(at: at(57.5)) == [])
        #expect(run.tick(at: at(60)) == [.restEnd])
        #expect(run.progress.timer == nil)
    }

    // MARK: - Timed sets

    @Test func aTimedSetIsPreparedThenRunsThenValidatesItself() {
        var run = ungated()
        run.focus(1)
        run.startSet(at: at(0))
        #expect(run.progress.timer?.kind == .prepare)
        #expect(run.progress.timer?.workSeconds == 45)
        #expect(run.tick(at: at(5)) == [.workStart(side: nil)])
        #expect(run.progress.timer?.kind == .work)
        #expect(run.progress.timer?.endsAt == at(50))
        #expect(run.tick(at: at(50)) == [.workEnd])
        #expect(run.step(1).slots == [nil])
        #expect(run.progress.timer?.kind == .rest)
    }

    @Test func aPausedSetKeepsItsSecondsLeft() {
        var run = ungated()
        run.focus(1)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        run.pause(at: at(20))
        #expect(run.tick(at: at(200)) == [])
        #expect(run.progress.timer?.seconds(at: at(200)) == 30)
        run.resume(at: at(300))
        #expect(run.progress.timer?.endsAt == at(330))
    }

    @Test func chainedSetsArePreparedAfterTheRest() {
        var run = ungated()
        run.focus(1)
        run.setChainsSets(true)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        run.tick(at: at(50))
        #expect(run.progress.timer?.autoNext == true)
        #expect(run.tick(at: at(80)) == [.restEnd])
        #expect(run.progress.timer?.kind == .prepare)
        #expect(run.progress.timer?.workSeconds == 45)
    }

    @Test func typingStopsTheChainAtTheEndOfTheRest() {
        var run = ungated()
        run.focus(1)
        run.setChainsSets(true)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        run.tick(at: at(50))
        #expect(run.tick(at: at(80), editing: true) == [.restEnd])
        #expect(run.progress.timer == nil)
    }

    // MARK: - The background

    @Test func aSetThatRanOutInTheBackgroundWaitsForConfirmation() {
        var run = ungated()
        run.focus(1)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        run.enterBackground(at: at(10))
        #expect(run.tick(at: at(60), background: true) == [])
        run.enterForeground(at: at(120))
        #expect(run.progress.timer?.confirm == true)
        #expect(run.step(1).slots.isEmpty)
        run.validate(nil, at: at(121))
        #expect(run.step(1).slots == [nil])
    }

    @Test func aPreparationFreezesInTheBackground() {
        var run = ungated()
        run.focus(1)
        run.startSet(at: at(0))
        run.enterBackground(at: at(2))
        #expect(run.progress.timer?.paused == 3)
        #expect(run.progress.timer?.confirm == true)
        #expect(run.tick(at: at(100)) == [])
        run.continueAfterInterruption(at: at(100))
        #expect(run.progress.timer?.endsAt == at(103))
        #expect(run.progress.timer?.confirm == false)
    }

    @Test func aRestThatRanOutWhileAwayDoesNotChain() {
        var run = ungated()
        run.focus(1)
        run.setChainsSets(true)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        run.tick(at: at(50))
        run.enterForeground(at: at(500))
        #expect(run.progress.timer?.autoNext == false)
        #expect(run.tick(at: at(500)) == [.restEnd])
        #expect(run.progress.timer == nil)
    }

    @Test func aReopenedSessionAsksToConfirmARunningSet() throws {
        var run = ungated()
        run.focus(1)
        run.startSet(at: at(0))
        run.tick(at: at(5))
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = FileStrengthProgressStore(directory: directory)
        try store.save(run.progress, sid: run.session.sid)
        let reopened = ungated(saved: try #require(store.load(sid: run.session.sid)))
        #expect(reopened.progress.current == 1)
        #expect(reopened.progress.timer?.confirm == true)
        #expect(reopened.progress.startedAt == at(5))
        store.remove(sid: run.session.sid)
        #expect(store.load(sid: run.session.sid) == nil)
    }

    // MARK: - Contacts, skipping, undoing

    @Test func contactsAreCountedPerBlock() {
        var run = ungated()
        run.focus(2)
        run.count(by: 10, at: at(0))
        run.count(by: -1, at: at(1))
        #expect(run.step(2).count == 9)
        run.validate(run.step(2).count, at: at(2))
        #expect(run.step(2).count == 0)
        #expect(run.step(2).slots == [9])
    }

    @Test func skippingKeepsTheSetsAlreadyDone() {
        var run = ungated()
        run.validate(12, at: at(0))
        run.skip(at: at(10))
        #expect(run.step(0).stopped)
        #expect(run.progress.feedback == 0)
        run.skip(at: at(20))
        #expect(run.step(1).skipped)
        #expect(run.progress.current == 2)
    }

    @Test func theLastValidationCanBeTakenBack() {
        var run = ungated()
        run.validate(12, at: at(0))
        run.validate(12, at: at(1))
        run.undo()
        #expect(run.step(0).slots == [12])
        #expect(run.progress.timer == nil)
        #expect(!run.canUndo)
    }

    @Test func aFinishedExerciseCanBeRedone() {
        var run = ungated()
        run.validate(12, at: at(0))
        run.skip(at: at(1))
        run.focus(0)
        run.redo(0)
        #expect(run.currentSlot?.n == 1)
        #expect(run.step(0).slots.isEmpty)
    }

    // MARK: - The guardrail

    @Test func theGuardrailWaitsForTheOpeningReading() {
        var run = gated()
        #expect(run.needsOpening)
        #expect(run.gateZones.map(\.zone) == ["knee"])
        run.answerOpening(zone: "knee", pain: 2)
        #expect(!run.needsOpening)
        #expect(!run.gateFired)
        #expect(!run.step(2).gated)
    }

    @Test func aPainAboveTheThresholdTakesTheJumpsOut() {
        var run = gated()
        run.answerOpening(zone: "knee", pain: 4)
        #expect(run.gateFired)
        #expect(run.step(2).gated)
        #expect(run.isDone(2))
        run.focus(2)
        #expect(run.progress.viewingRetired)
        run.redo(2)
        #expect(run.step(2).gated)
    }

    @Test func skippingTheOpeningArmsNothing() {
        var run = gated()
        run.skipOpening()
        #expect(!run.needsOpening)
        #expect(!run.gateFired)
        #expect(run.pain("knee", .before) == nil)
    }

    // MARK: - The review and the report

    func finished() -> StrengthRun {
        var run = gated()
        run.answerOpening(zone: "knee", pain: 4)
        for count in [12, 12, 11, 10] {
            run.validate(count, at: at(0))
        }
        run.validate(nil, at: at(300))
        run.skip(at: at(400))
        run.validate(25, at: at(1200))
        return run
    }

    @Test func theReviewSuggestsTheDuration() {
        let run = finished()
        #expect(run.progress.review)
        #expect(run.progress.minutes == 20)
        #expect(run.reviewed == [0, 1, 3])
    }

    @Test func theReportTellsEachExerciseAndOnlyTheReadZones() throws {
        var run = finished()
        run.setPain("knee", .after, 3)
        run.setPain("ankle", .after, nil)
        run.setScore(0, 7)
        run.setExerciseNote(0, "Gauche plus dure.")
        run.setNote(String(repeating: "x", count: 400))
        let pending = run.reportToSend(at: at(1300))
        let report = try #require(pending)
        #expect(report.performedOn == "2025-10-09")
        #expect(report.minutes == 20)
        #expect(report.note.count == 300)
        #expect(report.pains == ["knee": Notebook.StrengthPainPosted(before: 4, after: 3)])
        #expect(report.exercises.map(\.i) == [0, 1, 2, 3])
        #expect(report.exercises.map(\.status) == [.completed, .partial, .gated, .completed])
        #expect(report.exercises[0].reps == [12, 12, 11, 10])
        #expect(report.exercises[0].score == 7)
        #expect(report.exercises[1].reps == [nil])
        #expect(report.exercises[2].skipped && report.exercises[2].gated && report.exercises[2].reps.isEmpty)
    }

    @Test func theReportCarriesTheServersKeys() throws {
        var run = finished()
        let pending = run.reportToSend(at: at(1300))
        let report = try #require(pending)
        let json = try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(report))
        #expect(Set(json.keys) == ["performed_on", "pains", "minutes", "note", "exercises"])
        guard case .array(let exercises)? = json["exercises"], case .object(let first)? = exercises.first else {
            Issue.record("exercises missing")
            return
        }
        #expect(Set(first.keys) == ["i", "score", "skipped", "gated", "status", "note", "reps"])
    }

    @Test func aPendingReportGoesOutUnchangedAndLocksTheSession() throws {
        var run = finished()
        let pending = run.reportToSend(at: at(1300))
        let first = try #require(pending)
        #expect(run.isLocked)
        run.setNote("changed")
        run.validate(1, at: at(1400))
        let again = run.reportToSend(at: at(9999))
        #expect(again == first)
        run.delivered(duplicate: false)
        let none = run.reportToSend(at: at(9999))
        #expect(none == nil)
        #expect(run.progress.delivery == .sent(duplicate: false))
        #expect(!run.isUnfinished)
    }

    @Test func aRefusedReportCanBeChangedAgain() throws {
        var run = finished()
        _ = run.reportToSend(at: at(1300))
        run.refused()
        run.setNote("Corrigé.")
        let corrected = run.reportToSend(at: at(1400))
        #expect(corrected?.note == "Corrigé.")
    }

    @Test func aReopenedPendingReportShowsTheReview() throws {
        var run = finished()
        _ = run.reportToSend(at: at(1300))
        var progress = run.progress
        progress.review = false
        let reopened = StrengthRun(session: run.session, zones: run.zones, saved: progress)
        #expect(reopened.progress.review)
    }

    // MARK: - Around the session

    @Test func theNotebookOffersToResumeAStartedSession() {
        var run = ungated()
        #expect(StrengthResume(session: run.session, progress: run.progress) == nil)
        for count in [12, 12, 11, 10] {
            run.validate(count, at: at(0))
        }
        run.skip(at: at(1))
        let resume = StrengthResume(session: run.session, progress: run.progress)
        #expect(resume?.startedAt == at(0))
        #expect(resume?.done == 2)
        #expect(resume?.total == 4)
        _ = run.reportToSend(at: at(2))
        run.delivered(duplicate: true)
        #expect(StrengthResume(session: run.session, progress: run.progress) == nil)
    }

    @Test func aPendingReportIsRetriedLaterAndLater() {
        var retry = StrengthRetry()
        let waits = (0..<8).map { _ in retry.next() }
        #expect(waits == [5, 10, 20, 40, 80, 160, 300, 300].map { Duration.seconds($0) })
        retry.reset()
        #expect(retry.next() == .seconds(5))
    }

    @Test func thePreparationListsWhatToGetAndWhatChanged() throws {
        var run = gated()
        run.answerOpening(zone: "knee", pain: 4)
        let preparation = StrengthPreparation(run: run, previous: Self.view.previousSession)
        #expect(preparation.items == [.equipment("Marche"), .dumbbells(Notebook.StrengthLoad(kg: 8, count: 1))])
        let changes = try #require(preparation.changes)
        #expect(changes.count == 2)
        guard case .changed(let calves, let aspects) = changes[0], case .added(let pushUps) = changes[1] else {
            Issue.record("unexpected changes \(changes)")
            return
        }
        #expect(calves.slug == "mollets" && aspects == [.dose])
        #expect(pushUps.slug == "pompes")
        #expect(StrengthPreparation(run: run, previous: nil).changes == nil)
    }
}
