import Foundation

/// One beep: its pitch, its length and when it starts after the signal, in seconds.
public struct StrengthTone: Hashable, Sendable {
    public let hertz: Double
    public let seconds: Double
    public let delay: Double

    public init(hertz: Double, seconds: Double, delay: Double = 0) {
        self.hertz = hertz
        self.seconds = seconds
        self.delay = delay
    }
}

public extension StrengthSignal {
    /// What a signal sounds like, told apart without looking: the end of a set is two low
    /// beeps, the end of a rest three high ones, a change of side two rising notes, and
    /// each of the last three seconds of a countdown one short beep.
    var tones: [StrengthTone] {
        switch self {
        case .countdown: [StrengthTone(hertz: 880, seconds: 0.22)]
        case .workStart: [StrengthTone(hertz: 988, seconds: 0.35)]
        case .changeSide: [StrengthTone(hertz: 784, seconds: 0.28), StrengthTone(hertz: 988, seconds: 0.28, delay: 0.4)]
        case .workEnd: [StrengthTone(hertz: 523, seconds: 0.4), StrengthTone(hertz: 523, seconds: 0.4, delay: 0.55)]
        case .restEnd: [StrengthTone(hertz: 1046, seconds: 0.25), StrengthTone(hertz: 1046, seconds: 0.25, delay: 0.4),
                        StrengthTone(hertz: 1046, seconds: 0.4, delay: 0.8)]
        }
    }
}

/// A signal's tones as audio samples: triangle waves at `level`, each with a short ramp
/// in and out, since a gain cut abruptly clicks in the speaker.
public enum StrengthSynth {
    public static func samples(_ tones: [StrengthTone], rate: Double, level: Double) -> [Float] {
        let length = tones.map { $0.delay + $0.seconds }.max() ?? 0
        var out = [Float](repeating: 0, count: Int((length * rate).rounded()))
        let ramp = 0.012
        for tone in tones {
            let start = Int(tone.delay * rate)
            let count = Int(tone.seconds * rate)
            for index in 0..<count where start + index < out.count {
                let time = Double(index) / rate
                let phase = (time * tone.hertz).truncatingRemainder(dividingBy: 1)
                let triangle = 4 * abs(phase - 0.5) - 1
                let envelope = min(1, time / ramp, (tone.seconds - time) / ramp)
                out[start + index] += Float(triangle * envelope * level)
            }
        }
        return out
    }
}
