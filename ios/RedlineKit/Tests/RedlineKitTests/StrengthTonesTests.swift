import Testing
@testable import RedlineKit

struct StrengthTonesTests {
    @Test func eachSignalIsToldApartByItsBeeps() {
        #expect(StrengthSignal.workEnd.tones.count == 2)
        #expect(StrengthSignal.restEnd.tones.count == 3)
        #expect(StrengthSignal.changeSide.tones.map(\.hertz) == [784, 988])
        #expect(StrengthSignal.countdown(2).tones == StrengthSignal.countdown(3).tones)
    }

    @Test func theSamplesLastAsLongAsTheLastBeep() {
        let samples = StrengthSynth.samples(StrengthSignal.restEnd.tones, rate: 1000, level: 0.5)
        #expect(samples.count == 1200)
        #expect(samples.allSatisfy { abs($0) <= 0.5 })
    }

    @Test func aBeepStartsAndEndsInSilence() {
        let samples = StrengthSynth.samples([StrengthTone(hertz: 440, seconds: 0.1)], rate: 8000, level: 1)
        #expect(abs(samples[0]) < 0.01)
        #expect(abs(samples[samples.count - 1]) < 0.02)
        #expect(samples.contains { abs($0) > 0.9 })
    }
}
