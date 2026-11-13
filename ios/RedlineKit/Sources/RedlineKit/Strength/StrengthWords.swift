import Foundation

/// How the session's figures read.
public enum StrengthWords {
    public static func side(_ side: Notebook.StrengthSlot.S?) -> String? {
        switch side {
        case .d?: "droite"
        case .g?: "gauche"
        case .unknown(let raw)?: raw
        case nil: nil
        }
    }

    /// Blocks say « Bloc », sets say « Série »: reading « Série 3/4 » during plyometrics
    /// would send the athlete looking for a set that does not exist.
    public static func unit(_ measure: Notebook.StrengthDose.Measure) -> String {
        measure == .contacts ? "Bloc" : "Série"
    }

    /// « Série 2 sur 3 », or nothing for a single set.
    public static func position(_ slot: Notebook.StrengthSlot, measure: Notebook.StrengthDose.Measure) -> String? {
        slot.of > 1 ? "\(unit(measure)) \(slot.n) sur \(slot.of)" : nil
    }

    /// « Série 2 · droite »: what a rest leads to.
    public static func slot(_ slot: Notebook.StrengthSlot, measure: Notebook.StrengthDose.Measure) -> String {
        let parts = [slot.of > 1 ? "\(unit(measure)) \(slot.n)" : nil, side(slot.s)].compactMap { $0 }
        return parts.isEmpty ? "Une seule série" : parts.joined(separator: " · ").capitalizedFirst
    }

    public static func load(_ load: Notebook.StrengthLoad) -> String {
        load.kg == 0 ? "Poids du corps" : "\(load.count) × \(Numbers.text(load.kg)) kg"
    }

    /// Whole seconds under a minute, minutes and seconds above.
    public static func clock(_ seconds: Double) -> String {
        let whole = max(0, Int(seconds.rounded(.up)))
        return whole < 60 ? "\(whole)" : "\(whole / 60):" + String(format: "%02d", whole % 60)
    }

    /// A length said in full: « 45 s » under a minute, « 1:30 » above.
    public static func duration(_ seconds: Int) -> String {
        seconds < 60 ? "\(max(0, seconds)) s" : clock(Double(seconds))
    }

    /// What a finished exercise shows in the list: what was done rather than what was
    /// prescribed. « Removed » is the coach's rule, « not done » the athlete's choice.
    public static func dose(of exercise: Notebook.StrengthExercise, step: StepProgress, done: Bool) -> String {
        guard done else { return exercise.dose.text }
        if step.gated { return "retiré — garde-fou" }
        if step.skipped { return "pas fait" }
        let banked = step.slots.compactMap { $0 }
        var text = exercise.dose.text
        if !banked.isEmpty {
            text = exercise.dose.measure == .contacts ? "\(banked.reduce(0, +)) contacts"
                : banked.map(String.init).joined(separator: " · ")
        }
        return step.stopped ? "partiel · \(text)" : text
    }
}
