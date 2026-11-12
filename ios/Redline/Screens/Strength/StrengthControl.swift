import RedlineKit
import SwiftUI

/// How the session's figures read.
enum StrengthWords {
    static func side(_ side: Notebook.StrengthSlot.S?) -> String? {
        switch side {
        case .d?: "droite"
        case .g?: "gauche"
        case .unknown(let raw)?: raw
        case nil: nil
        }
    }

    /// Blocks say « Bloc », sets say « Série »: reading « Série 3/4 » during plyometrics
    /// would send the athlete looking for a set that does not exist.
    static func unit(_ measure: Notebook.StrengthDose.Measure) -> String {
        measure == .contacts ? "Bloc" : "Série"
    }

    static func slot(_ slot: Notebook.StrengthSlot, measure: Notebook.StrengthDose.Measure) -> String {
        let parts = [slot.of > 1 ? "\(unit(measure)) \(slot.n)/\(slot.of)" : nil, side(slot.s)].compactMap { $0 }
        return parts.isEmpty ? "À faire" : parts.joined(separator: " · ")
    }

    static func load(_ load: Notebook.StrengthLoad) -> String {
        load.kg == 0 ? "Poids du corps" : "\(load.count) × \(Numbers.text(load.kg)) kg"
    }

    static func clock(_ seconds: Double) -> String {
        let whole = max(0, Int(seconds.rounded()))
        return "\(whole / 60):" + String(format: "%02d", whole % 60)
    }

    /// What a finished exercise shows in the list: what was done rather than what was
    /// prescribed. « Removed » is the coach's rule, « not done » the athlete's choice.
    static func dose(of exercise: Notebook.StrengthExercise, step: StepProgress, done: Bool) -> String {
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

/// The live exercise: what to do now, and the one button forward. The rest is prescribed
/// by the session, so the forward button owns it: it banks the set, runs the rest if there
/// is one, and lands on whatever comes next.
struct StrengthControl: View {
    let model: StrengthModel
    let run: StrengthRun
    let index: Int

    @State private var typed = ""
    @State private var otherCount = false

    private var exercise: Notebook.StrengthExercise { run.exercises[index] }
    private var step: StepProgress { run.step(index) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            controlBody
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.panel, in: .rect(cornerRadius: 16))
        .onChange(of: step.slots.count) { typed = ""; otherCount = false }
    }

    @ViewBuilder private var controlBody: some View {
        if step.gated {
            ControlLabel("Retiré par le garde-fou de douleur")
            if let say = run.session.gate?.say { Text(say).font(.subheadline) }
            cues
            wide("Revenir à la séance") { model.update { $0.moveOn(at: .now) } }
        } else if let slot = run.currentSlot {
            if let timer = run.progress.timer, timer.kind == .rest {
                rest(slot)
            } else if let timer = run.progress.timer, timer.confirm {
                interrupted(timer)
            } else if let timer = run.progress.timer, timer.kind == .prepare {
                ControlLabel("Prépare-toi")
                prescription(slot)
                Figure(timer: timer, dim: false)
                quiet("Annuler le départ") { model.update { $0.stopTimer() } }
            } else {
                live(slot)
            }
        } else {
            finished
        }
    }

    // MARK: - The states

    @ViewBuilder private func live(_ slot: Notebook.StrengthSlot) -> some View {
        let dose = exercise.dose
        ControlLabel(StrengthWords.slot(slot, measure: dose.measure))
        prescription(slot)
        switch dose.measure {
        case .time:
            timed(slot)
        case .contacts:
            contacts(slot)
        case .reps where dose.counts != nil:
            Text(dose.text).font(.subheadline)
            counts(dose.counts ?? [])
        case .amrap:
            Text(dose.text).font(.subheadline)
            numberField
            wide(forward) { model.change { $0.validate(Int(typed), at: .now) } }
        default:
            Text(dose.text).font(.subheadline)
            wide(forward) { model.change { $0.validate(nil, at: .now) } }
        }
        quiet(step.slots.isEmpty ? "Passer l’exercice" : "Arrêter ici — garder les séries faites") {
            model.update { $0.skip(at: .now) }
        }
        undo
        cues
        last
        feedback
        let banked = step.slots.compactMap { $0 }
        if !banked.isEmpty {
            Text("Déjà fait : " + banked.map(String.init).joined(separator: " · "))
                .font(.footnote).monospacedDigit().foregroundStyle(Palette.muted)
        }
    }

    @ViewBuilder private func timed(_ slot: Notebook.StrengthSlot) -> some View {
        let seconds = slot.t ?? StrengthRun.defaultSetSeconds
        if let timer = run.progress.timer, timer.kind == .work {
            Figure(timer: timer, dim: false)
            if timer.paused != nil {
                wide("Reprendre") { model.update { $0.resume(at: .now) } }
            } else {
                wide("Pause") { model.update { $0.pause(at: .now) } }
            }
        } else {
            Text(StrengthWords.clock(Double(seconds))).font(.figure(64)).monospacedDigit().foregroundStyle(Palette.muted)
            wide("Démarrer") { model.change { $0.startSet(at: .now) } }
            Toggle("Enchaîner les séries de cet exercice", isOn: Binding(
                get: { run.progress.chainsSets }, set: { value in model.update { $0.setChainsSets(value) } }))
                .font(.subheadline)
                .tint(Palette.red)
        }
        // The countdown validates on its own at zero; this is for a set done without the clock.
        quiet(forward) { model.change { $0.validate(nil, at: .now) } }
    }

    @ViewBuilder private func contacts(_ slot: Notebook.StrengthSlot) -> some View {
        let target = slot.t ?? 0
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("\(step.count)").font(.figure(64)).monospacedDigit()
            Text("/ \(target) contacts").font(.subheadline).foregroundStyle(Palette.muted)
        }
        if otherCount || step.count > 0 {
            HStack(spacing: 8) {
                ForEach([-1, 1, 10], id: \.self) { delta in
                    Button(delta > 0 ? "+\(delta)" : "−\(-delta)") { model.update { $0.count(by: delta, at: .now) } }
                        .buttonStyle(.bordered)
                        .tint(Palette.ink)
                        .frame(maxWidth: .infinity)
                }
            }
            wide(forward) { model.change { $0.validate(step.count > 0 ? step.count : nil, at: .now) } }
        } else {
            wide("\(target) contacts faits, puis repos") { model.change { $0.validate(target, at: .now) } }
            quiet("Autre nombre") { otherCount = true }
        }
    }

    /// Each count of the prescribed range banks the set in one tap; none is pressed in
    /// advance, or the progression's criterion would pass without a measure.
    @ViewBuilder private func counts(_ counts: [Int]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Combien de répétitions ?").font(.subheadline.weight(.semibold))
            HStack(spacing: 6) {
                ForEach(counts, id: \.self) { count in
                    Button {
                        model.change { $0.validate(count, at: .now) }
                    } label: {
                        Text("\(count)").font(.title3.weight(.semibold)).monospacedDigit()
                            .frame(maxWidth: .infinity, minHeight: 48)
                            .foregroundStyle(Palette.bg)
                            .background(Palette.ink, in: .rect(cornerRadius: 10))
                    }
                    .buttonStyle(.plain)
                }
            }
            Text(forward.hasPrefix("Puis") ? forward : forward == "Terminer" ? "Puis la fin de la séance" : "Puis : " + forward.lowercased())
                .font(.footnote).foregroundStyle(Palette.muted)
        }
        if otherCount {
            numberField
            wide(forward) { model.change { $0.validate(Int(typed), at: .now) } }
        } else if let low = counts.first, let high = counts.last {
            quiet(low == high ? "Autre nombre" : "Moins de \(low), ou plus de \(high)") { otherCount = true }
        }
    }

    /// The rest names what it is a rest before, and clears itself at zero.
    @ViewBuilder private func rest(_ slot: Notebook.StrengthSlot) -> some View {
        ControlLabel("Repos")
        if let timer = run.progress.timer { Figure(timer: timer, dim: true) }
        Text("Ensuite : \(exercise.name) · \(StrengthWords.slot(slot, measure: exercise.dose.measure))").font(.subheadline)
        prescription(slot)
        cues
        last
        feedback
        wide("Reprendre") { model.update { $0.stopTimer() } }
        undo
    }

    @ViewBuilder private func interrupted(_ timer: StrengthTimer) -> some View {
        ControlLabel("Reprise après interruption")
        Figure(timer: timer, dim: true)
        Text("Confirme ce que tu as fait avant de poursuivre.").font(.footnote).foregroundStyle(Palette.muted)
        if timer.kind == .work {
            wide("Série réalisée") { model.change { $0.validate(nil, at: .now) } }
        }
        quiet("Reprendre le chrono") { model.update { $0.continueAfterInterruption(at: .now) } }
        quiet("Recommencer le chrono") { model.update { $0.stopTimer() } }
    }

    /// Tapped back onto something already finished: the useful offer is to do it again.
    @ViewBuilder private var finished: some View {
        ControlLabel(step.skipped ? "Pas fait" : step.stopped ? "Partiel" : "Terminé")
        quiet("Refaire") { model.update { $0.redo(index) } }
        if step.stopped {
            quiet("Reprendre l’exercice") { model.update { $0.resumeStopped(index) } }
        }
        quiet("Revenir à la séance") { model.update { $0.moveOn(at: .now) } }
        undo
    }

    // MARK: - The parts

    /// Where the forward button goes: the other leg, the next set, the next exercise, the end.
    private var forward: String {
        let slots = exercise.dose.slots
        let done = step.slots.count
        guard done + 1 < slots.count else {
            return index == run.exercises.count - 1 ? "Terminer" : "Exercice suivant"
        }
        let next = slots[done + 1]
        if next.n == slots[done].n, let side = StrengthWords.side(next.s) { return "Puis la \(side)" }
        return "\(StrengthWords.unit(exercise.dose.measure)) \(next.n)" + (StrengthWords.side(next.s).map { " · \($0)" } ?? "")
    }

    @ViewBuilder private func prescription(_ slot: Notebook.StrengthSlot) -> some View {
        let side = StrengthWords.side(slot.s).map { $0.uppercased() + " · " } ?? ""
        Text(side + exercise.dose.currentText).font(.title3.weight(.semibold))
        if let load = exercise.load {
            Text("Charge prévue : " + StrengthWords.load(load)).font(.footnote).foregroundStyle(Palette.muted)
        }
        if !exercise.equipment.isEmpty {
            Text("À préparer : " + exercise.equipment.joined(separator: " · ")).font(.footnote).foregroundStyle(Palette.muted)
        }
        if !exercise.cue.isEmpty {
            Text(exercise.cue).font(.footnote.italic())
        }
    }

    /// The movement cues, folded until asked; whoever opens them wants them on every exercise.
    @ViewBuilder private var cues: some View {
        if let cues = model.cues[exercise.slug], !cues.isEmpty {
            DisclosureGroup("Le geste", isExpanded: Binding(
                get: { run.progress.showsCues }, set: { value in model.update { $0.setShowsCues(value) } })) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(cues, id: \.self) { Text("· " + $0) }
                }
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
            }
            .font(.subheadline.weight(.semibold))
            .tint(Palette.ink)
        }
    }

    /// What was done last time, right before deciding what to do today.
    @ViewBuilder private var last: some View {
        if let last = exercise.last {
            VStack(alignment: .leading, spacing: 2) {
                Text("Réalisé la dernière fois").font(.caption).foregroundStyle(Palette.muted)
                Text(last).font(.subheadline).monospacedDigit()
            }
        }
    }

    /// The exercise just finished, to rate while the next one waits.
    @ViewBuilder private var feedback: some View {
        if let finished = run.progress.feedback, run.exercises.indices.contains(finished) {
            DisclosureGroup("Noter : \(run.exercises[finished].name)") {
                ExerciseFeedback(model: model, run: run, index: finished).padding(.top, 8)
            }
            .font(.subheadline)
            .tint(Palette.ink)
        }
    }

    @ViewBuilder private var undo: some View {
        if run.canUndo {
            quiet("Annuler la dernière validation") { model.update { $0.undo() } }
        }
    }

    private var numberField: some View {
        TextField("répétitions", text: $typed)
            .keyboardType(.numberPad)
            .font(.title3.weight(.semibold))
            .monospacedDigit()
            .padding(10)
            .background(Palette.bg, in: .rect(cornerRadius: 10))
            .onChange(of: typed) { _, text in
                let digits = String(text.filter(\.isASCII).filter(\.isNumber).prefix(3))
                if digits != text { typed = digits }
                model.editing = !digits.isEmpty
            }
            .accessibilityLabel("Répétitions réalisées")
    }

    private func wide(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.body.weight(.semibold)).frame(maxWidth: .infinity, minHeight: 34)
        }
        .buttonStyle(.borderedProminent)
        .tint(Palette.ink)
        .foregroundStyle(Palette.bg)
    }

    private func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .font(.subheadline)
            .tint(Palette.muted)
            .buttonStyle(.borderless)
    }
}

/// The small capitals over the control.
private struct ControlLabel: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text.uppercased()).font(.caption.weight(.bold)).foregroundStyle(Palette.red)
    }
}

/// A timer's big figure, redrawn four times a second from its timestamps.
private struct Figure: View {
    let timer: StrengthTimer
    let dim: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.25)) { context in
            Text(StrengthWords.clock(timer.seconds(at: context.date)))
                .font(.figure(64))
                .monospacedDigit()
                .foregroundStyle(dim ? Palette.muted : Palette.ink)
                .contentTransition(.numericText(countsDown: timer.isCountdown))
        }
    }
}

/// How hard an exercise was to do properly, and a remark on it.
struct ExerciseFeedback: View {
    let model: StrengthModel
    let run: StrengthRun
    let index: Int

    var body: some View {
        let step = run.step(index)
        VStack(alignment: .leading, spacing: 8) {
            Text("Difficulté ressentie · facile ← → trop dur · facultatif").font(.caption).foregroundStyle(Palette.muted)
            ScoreScale(value: step.score) { value in model.update { $0.setScore(index, value) } }
            TextField("Une remarque sur cet exercice ?", text: Binding(
                get: { step.note }, set: { text in model.update { $0.setExerciseNote(index, text) } }), axis: .vertical)
                .font(.subheadline)
                .padding(10)
                .background(Palette.bg, in: .rect(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Palette.grid, lineWidth: 1))
        }
        .disabled(run.isLocked)
    }
}
