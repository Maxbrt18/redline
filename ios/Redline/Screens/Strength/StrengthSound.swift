import AVFoundation
import RedlineKit
import SwiftUI
import UserNotifications

/// The session's signals out loud: beeps mixed with the athlete's music, and, if asked, the
/// side said aloud, the music lowered while it is said. During a plank nobody looks at the
/// screen.
@MainActor
final class StrengthSound: NSObject, AVSpeechSynthesizerDelegate {
    static let enabledKey = "strength.sound"
    static let levelKey = "strength.level"
    static let voiceKey = "strength.voice"
    private static let rate = 44_100.0

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let speech = AVSpeechSynthesizer()
    private let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1)!
    /// The audio session is (de)activated off the main thread: the call can block long
    /// enough to freeze the screen.
    private static let sessionQueue = DispatchQueue(label: "strength.audio-session")

    override init() {
        super.init()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
        speech.delegate = self
    }

    static var enabled: Bool { UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true }
    static var level: Double { UserDefaults.standard.object(forKey: levelKey) as? Double ?? 0.8 }
    static var voice: Bool { UserDefaults.standard.bool(forKey: voiceKey) }

    /// The session opens: sound mixes with whatever plays, and never stops it.
    func start() {
        activate(ducking: false)
    }

    func stop() {
        speech.stopSpeaking(at: .immediate)
        player.stop()
        engine.stop()
        Self.sessionQueue.async {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
    }

    func play(_ signal: StrengthSignal, saying text: String? = nil) {
        guard Self.enabled else { return }
        if !engine.isRunning { try? engine.start() }
        let samples = StrengthSynth.samples(signal.tones, rate: Self.rate, level: 0.65 * Self.level)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let channel = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { channel.update(from: $0.baseAddress!, count: samples.count) }
        player.scheduleBuffer(buffer)
        if !player.isPlaying { player.play() }
        if let text, Self.voice { say(text) }
    }

    private func say(_ text: String) {
        activate(ducking: true)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "fr-FR")
        utterance.volume = Float(Self.level)
        // Spoken after the beeps rather than over them.
        utterance.preUtteranceDelay = 0.3
        speech.stopSpeaking(at: .immediate)
        speech.speak(utterance)
    }

    /// Ducking applies when the session activates: the music comes back up once the
    /// session is reactivated without it.
    private func activate(ducking: Bool, releasing: Bool = false) {
        Self.sessionQueue.async {
            let session = AVAudioSession.sharedInstance()
            if ducking || releasing {
                try? session.setActive(false, options: releasing ? .notifyOthersOnDeactivation : [])
            }
            try? session.setCategory(.playback, options: ducking ? [.mixWithOthers, .duckOthers] : [.mixWithOthers])
            try? session.setActive(true)
        }
        if !engine.isRunning { try? engine.start() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.activate(ducking: false, releasing: true) }
    }
}

/// When the app is in the background, a running countdown still reaches the athlete: a local
/// notification at its end, withdrawn as soon as the app is back.
enum StrengthNotifier {
    private static let identifier = "strength-timer"

    static func ask() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
    }

    static func schedule(for run: StrengthRun?, now: Date = .now) {
        guard let run, let timer = run.progress.timer, let endsAt = timer.endsAt,
              timer.paused == nil, !timer.confirm, endsAt > now else { return }
        let content = UNMutableNotificationContent()
        switch timer.kind {
        case .rest:
            content.title = "Fin du repos"
            let exercise = run.exercises[run.progress.current]
            content.body = run.currentSlot.map {
                "Ensuite : \(exercise.name) · \(StrengthWords.slot($0, measure: exercise.dose.measure))"
            } ?? "Reviens à la séance."
        case .work:
            content.title = "Série terminée"
            content.body = "Reviens confirmer la série."
        case .prepare:
            return
        }
        content.sound = .default
        content.interruptionLevel = .active
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: endsAt.timeIntervalSince(now), repeats: false)
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: trigger))
    }

    static func withdraw() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [identifier])
        center.removeDeliveredNotifications(withIdentifiers: [identifier])
    }
}

/// The sound settings, in a sheet opened from the session's bar.
struct StrengthSoundSettings: View {
    let sound: StrengthSound
    @AppStorage(StrengthSound.enabledKey) private var enabled = true
    @AppStorage(StrengthSound.levelKey) private var level = 0.8
    @AppStorage(StrengthSound.voiceKey) private var voice = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle("Signaux sonores", isOn: $enabled)
                    LabeledContent("Intensité") {
                        Slider(value: $level, in: 0.2...1, step: 0.1)
                    }
                    .disabled(!enabled)
                }
                Section {
                    Toggle(isOn: $voice) {
                        Text("Annoncer le côté à voix haute")
                        Text("La musique baisse le temps de l’annonce.")
                    }
                    .disabled(!enabled)
                }
                Section {
                    Button("Tester le son") {
                        enabled = true
                        sound.play(.workEnd, saying: voice ? "Gauche" : nil)
                    }
                } footer: {
                    Text("Fin du travail : deux bips graves. Fin du repos : trois bips aigus. Changement de côté : deux notes montantes. Le volume suit celui du téléphone. Application en arrière-plan : une notification à la fin du repos.")
                }
            }
            .navigationTitle("Son")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("OK") { dismiss() } }
            }
        }
        .tint(Palette.accent)
        .presentationDetents([.medium, .large])
    }
}
