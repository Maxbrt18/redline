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

    /// « Série 2 sur 3 », or nothing for a single set.
    static func position(_ slot: Notebook.StrengthSlot, measure: Notebook.StrengthDose.Measure) -> String? {
        slot.of > 1 ? "\(unit(measure)) \(slot.n) sur \(slot.of)" : nil
    }

    /// « Série 2 · droite »: what a rest leads to.
    static func slot(_ slot: Notebook.StrengthSlot, measure: Notebook.StrengthDose.Measure) -> String {
        let parts = [slot.of > 1 ? "\(unit(measure)) \(slot.n)" : nil, side(slot.s)].compactMap { $0 }
        return parts.isEmpty ? "Une seule série" : parts.joined(separator: " · ").capitalizedFirst
    }

    static func load(_ load: Notebook.StrengthLoad) -> String {
        load.kg == 0 ? "Poids du corps" : "\(load.count) × \(Numbers.text(load.kg)) kg"
    }

    /// Whole seconds under a minute, minutes and seconds above.
    static func clock(_ seconds: Double) -> String {
        let whole = max(0, Int(seconds.rounded(.up)))
        return whole < 60 ? "\(whole)" : "\(whole / 60):" + String(format: "%02d", whole % 60)
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

/// What the live exercise is in: one state per screen.
enum StrengthState {
    case ready(Notebook.StrengthSlot)
    case prepare(Notebook.StrengthSlot, StrengthTimer)
    case work(Notebook.StrengthSlot, StrengthTimer)
    case rest(Notebook.StrengthSlot, StrengthTimer)
    case interrupted(StrengthTimer)
    case gated
    case finished

    init(run: StrengthRun) {
        guard !run.step(run.progress.current).gated else { self = .gated; return }
        guard let slot = run.currentSlot else { self = .finished; return }
        switch run.progress.timer {
        case let timer? where timer.confirm: self = .interrupted(timer)
        case let timer? where timer.kind == .rest: self = .rest(slot, timer)
        case let timer? where timer.kind == .prepare: self = .prepare(slot, timer)
        case let timer? where timer.kind == .work: self = .work(slot, timer)
        default: self = .ready(slot)
        }
    }

    var resting: Bool {
        if case .rest = self { return true }
        return false
    }
}

/// The live exercise, read at arm's length: what to do and how much, in three zones that
/// never move. On top, the session's progress and the exercise; in the middle, one figure;
/// at the bottom, the validation, always at the same place. A one-sided set anchors its
/// figure on its side of the screen. Everything rarer is in the bar's menu.
struct StrengthFocus: View {
    let model: StrengthModel
    let run: StrengthRun
    /// Raised by « Autre nombre »: the screen asks for the figure in an alert.
    @Binding var asking: Bool
    @State private var adjusting = false
    @State private var amrap = 0

    private var index: Int { run.progress.current }
    private var exercise: Notebook.StrengthExercise { run.exercises[index] }
    private var step: StepProgress { run.step(index) }
    private var state: StrengthState { StrengthState(run: run) }

    var body: some View {
        VStack(spacing: 0) {
            top
            center.frame(maxHeight: .infinity)
            bottom
        }
        .padding(.horizontal, Space.l)
        .padding(.bottom, Space.m)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Palette.bg)
        .onChange(of: step.slots.count) { adjusting = false; amrap = 0 }
        .onChange(of: index) { adjusting = false; amrap = 0 }
    }

    // MARK: - Top

    private var top: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            StrengthSegments(run: run)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(state.resting ? "Repos" : exercise.name)
                    .textRole(.title).foregroundStyle(Palette.ink).lineLimit(2)
                Text(subtitle).textRole(.secondary).foregroundStyle(Palette.muted).lineLimit(1)
            }
            // Two lines of name and the subtitle always fit, so the figure never moves.
            .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
            if run.gateFired, let say = run.session.gate?.say {
                Label(say, systemImage: "exclamationmark.shield")
                    .font(TextRole.caption.font.weight(.semibold))
                    .foregroundStyle(Palette.accent)
                    .lineLimit(2)
            }
            if model.unsaved {
                Label("Sauvegarde indisponible : garde la séance ouverte jusqu’à l’envoi.",
                      systemImage: "exclamationmark.triangle")
                    .textRole(.caption).foregroundStyle(Palette.accent)
            }
        }
        .padding(.top, Space.xs)
    }

    private var subtitle: String {
        switch state {
        case .rest: ""
        case .gated: "Retiré par le garde-fou"
        case .finished: step.skipped ? "Pas fait" : step.stopped ? "Partiel" : "Terminé"
        case .interrupted: "Reprise après interruption"
        case .ready(let slot), .prepare(let slot, _), .work(let slot, _):
            StrengthWords.position(slot, measure: exercise.dose.measure) ?? "Une seule série"
        }
    }

    // MARK: - Center

    @ViewBuilder private var center: some View {
        switch state {
        case .ready(let slot):
            ready(slot)
        case .prepare(let slot, let timer):
            anchored(slot) {
                TimerFigure(timer: timer)
                caption("prépare-toi")
            }
        case .work(let slot, let timer):
            VStack(spacing: Space.m) {
                anchored(slot) {
                    TimerFigure(timer: timer)
                    caption(timer.paused != nil ? "en pause" : "à tenir")
                }
                TimerBar(timer: timer)
            }
        case .rest(let slot, let timer):
            VStack(spacing: Space.m) {
                TimerFigure(timer: timer)
                TimerBar(timer: timer)
                VStack(spacing: Space.xxs) {
                    Text("Ensuite : \(exercise.name)").textRole(.body).fontWeight(.semibold).lineLimit(2)
                    Text(StrengthWords.slot(slot, measure: exercise.dose.measure))
                        .textRole(.secondary).foregroundStyle(Palette.muted)
                }
                .multilineTextAlignment(.center)
            }
        case .interrupted(let timer):
            VStack(spacing: Space.s) {
                TimerFigure(timer: timer).opacity(0.6)
                caption("Confirme ce que tu as fait avant de poursuivre.").multilineTextAlignment(.center)
            }
        case .gated:
            Text(run.session.gate?.say ?? "Cet exercice est retiré aujourd’hui.")
                .textRole(.body).multilineTextAlignment(.center)
        case .finished:
            Text(StrengthWords.dose(of: exercise, step: step, done: true))
                .figure(.large).foregroundStyle(Palette.muted).minimumScaleFactor(0.5)
        }
    }

    @ViewBuilder private func ready(_ slot: Notebook.StrengthSlot) -> some View {
        let dose = exercise.dose
        switch dose.measure {
        case .reps where dose.counts?.isEmpty == false:
            let counts = dose.counts ?? []
            anchored(slot) {
                StrengthFigure(text: counts.count > 1 ? "\(counts[0])–\(counts[counts.count - 1])" : "\(counts[0])")
                caption(["répétitions", exercise.load.map(StrengthWords.load)].compactMap { $0 }.joined(separator: " · "))
                caption(forward).padding(.top, Space.xs)
            }
        case .time:
            anchored(slot) {
                StrengthFigure(text: StrengthWords.clock(Double(slot.t ?? StrengthRun.defaultSetSeconds)))
                caption("secondes à tenir")
            }
        case .contacts:
            let target = slot.t ?? 0
            let counting = adjusting || step.count > 0
            VStack(spacing: Space.m) {
                anchored(slot) {
                    StrengthFigure(text: "\(counting ? step.count : target)")
                    caption(counting ? "sur \(target) contacts" : "contacts")
                }
                if counting {
                    HStack(spacing: Space.s) {
                        ForEach([-1, 1, 10], id: \.self) { delta in
                            Nudge(title: delta > 0 ? "+\(delta)" : "−\(-delta)") {
                                model.update { $0.count(by: delta, at: .now) }
                            }
                        }
                    }
                }
            }
        case .amrap:
            VStack(spacing: Space.m) {
                anchored(slot) {
                    StrengthFigure(text: "\(amrap)")
                    caption("jusqu’à l’échec")
                }
                HStack(spacing: Space.s) {
                    ForEach([-1, 1, 5], id: \.self) { delta in
                        Nudge(title: delta > 0 ? "+\(delta)" : "−\(-delta)") { amrap = min(999, max(0, amrap + delta)) }
                    }
                }
            }
        default:
            anchored(slot) {
                Text(dose.currentText).textRole(.title)
                caption(forward).padding(.top, Space.xs)
            }
        }
    }

    /// The side is the block's anchor: the left of the screen for the left side, the right
    /// for the right, the middle when the set has no side.
    private func anchored(_ slot: Notebook.StrengthSlot, @ViewBuilder _ content: () -> some View) -> some View {
        let edge: (HorizontalAlignment, Alignment, TextAlignment) = switch slot.s {
        case .g?: (.leading, .leading, .leading)
        case .d?: (.trailing, .trailing, .trailing)
        default: (.center, .center, .center)
        }
        return VStack(alignment: edge.0, spacing: 0) {
            if let side = StrengthWords.side(slot.s) {
                Text(side.capitalizedFirst).font(.title.bold()).foregroundStyle(Palette.ink)
            }
            content()
        }
        .multilineTextAlignment(edge.2)
        .frame(maxWidth: .infinity, alignment: edge.1)
        .animation(.spring(duration: 0.45, bounce: 0.25), value: StrengthWords.side(slot.s))
    }

    private func caption(_ text: String) -> some View {
        Text(text).font(.title3).foregroundStyle(Palette.muted)
    }

    /// Where the validation leads: the other side, the next set, the next exercise, the end.
    private var forward: String {
        let slots = exercise.dose.slots
        let done = step.slots.count
        guard done + 1 < slots.count else {
            return index == run.exercises.count - 1 ? "puis le bilan" : "puis l’exercice suivant"
        }
        let next = slots[done + 1]
        if next.n == slots[done].n, let side = StrengthWords.side(next.s) { return "puis la \(side)" }
        return "puis " + StrengthWords.slot(next, measure: exercise.dose.measure).lowercased()
    }

    // MARK: - Bottom

    /// One quiet row above, the main action below: the main action's place never changes.
    private var bottom: some View {
        VStack(spacing: Space.s) {
            HStack(spacing: Space.s) { secondary }
                .frame(minHeight: 44)
            main
        }
        .controlSize(.extraLarge)
    }

    @ViewBuilder private var secondary: some View {
        switch state {
        case .ready:
            switch exercise.dose.measure {
            case .reps where exercise.dose.counts?.isEmpty == false: quiet("Autre nombre") { asking = true }
            case .contacts where !adjusting && step.count == 0: quiet("Autre nombre") { adjusting = true }
            case .amrap: quiet("Saisir un nombre") { asking = true }
            default: EmptyView()
            }
        case .prepare: quiet("Annuler le départ") { model.update { $0.stopTimer() } }
        case .interrupted(let timer):
            quiet("Reprendre le chrono") { model.update { $0.continueAfterInterruption(at: .now) } }
            if timer.kind == .work { quiet("Recommencer") { model.update { $0.stopTimer() } } }
        case .finished:
            quiet("Refaire") { model.update { $0.redo(index) } }
            if step.stopped { quiet("Reprendre l’exercice") { model.update { $0.resumeStopped(index) } } }
        default: EmptyView()
        }
    }

    @ViewBuilder private var main: some View {
        switch state {
        case .ready(let slot):
            switch exercise.dose.measure {
            case .reps where exercise.dose.counts?.isEmpty == false:
                // Each count banks the set in one tap; none is pressed in advance, or the
                // progression's criterion would pass without a measure.
                HStack(spacing: Space.s) {
                    ForEach(exercise.dose.counts ?? [], id: \.self) { count in
                        Button { model.change { $0.validate(count, at: .now) } } label: {
                            Text("\(count)").figure(.large).frame(maxWidth: .infinity, minHeight: 48)
                        }
                        .buttonStyle(.glassProminent)
                        .accessibilityLabel("\(count) répétitions faites")
                    }
                }
            case .time: prominent("Démarrer") { model.change { $0.startSet(at: .now) } }
            case .contacts:
                let target = slot.t ?? 0
                if adjusting || step.count > 0 {
                    prominent("\(step.count) faits") {
                        model.change { $0.validate(step.count > 0 ? step.count : nil, at: .now) }
                    }
                } else {
                    prominent("\(target) faits") { model.change { $0.validate(target, at: .now) } }
                }
            case .amrap: prominent("Terminer · \(amrap)") { model.change { $0.validate(amrap, at: .now) } }
            default: prominent("Série faite") { model.change { $0.validate(nil, at: .now) } }
            }
        case .prepare: held
        case .work(_, let timer):
            if timer.paused != nil {
                prominent("Reprendre") { model.update { $0.resume(at: .now) } }
            } else {
                Button { model.update { $0.pause(at: .now) } } label: { label("Pause") }.buttonStyle(.glass)
            }
        case .rest: prominent("Reprendre") { model.update { $0.stopTimer() } }
        case .interrupted(let timer):
            if timer.kind == .work {
                prominent("Série réalisée") { model.change { $0.validate(nil, at: .now) } }
            } else {
                prominent("Recommencer") { model.update { $0.stopTimer() } }
            }
        case .gated, .finished: prominent("Revenir à la séance") { model.update { $0.moveOn(at: .now) } }
        }
    }

    private func label(_ title: String) -> some View {
        Text(title).font(.title2.bold()).lineLimit(1).minimumScaleFactor(0.6)
            .frame(maxWidth: .infinity, minHeight: 48)
    }

    private func prominent(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { label(title) }.buttonStyle(.glassProminent)
    }

    /// The main action's place, kept empty while there is nothing to press.
    private var held: some View {
        label(" ").hidden().padding(.vertical, Space.s).accessibilityHidden(true)
    }

    private func quiet(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .textRole(.secondary).fontWeight(.semibold)
            .tint(Palette.ink)
            .buttonStyle(.borderless)
            .frame(maxWidth: .infinity, minHeight: 44)
    }
}

/// The figure in the middle of the screen.
private struct StrengthFigure: View {
    let text: String

    var body: some View {
        Text(text).figure(.display).minimumScaleFactor(0.5).foregroundStyle(Palette.ink)
    }
}

/// A timer's figure, redrawn four times a second from its timestamps.
private struct TimerFigure: View {
    let timer: StrengthTimer

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.25)) { context in
            StrengthFigure(text: StrengthWords.clock(timer.seconds(at: context.date)))
        }
    }
}

/// How much of a countdown has gone.
private struct TimerBar: View {
    let timer: StrengthTimer

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.25)) { context in
            let total = Double(max(timer.total ?? 1, 1))
            ProgressView(value: min(max(total - timer.seconds(at: context.date), 0), total), total: total)
                .tint(Palette.ink)
        }
        .accessibilityHidden(true)
    }
}

/// An adjusting button under the figure.
private struct Nudge: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).figure(.medium).frame(maxWidth: .infinity, minHeight: 44)
        }
        .buttonStyle(.glass)
    }
}

/// One segment per set, grouped by exercise; a set with two sides fills by halves. The set
/// in progress is darker than those to come.
struct StrengthSegments: View {
    let run: StrengthRun

    var body: some View {
        HStack(spacing: Space.xxs) {
            ForEach(Array(run.exercises.enumerated()), id: \.offset) { index, exercise in
                HStack(spacing: Space.xxs) {
                    ForEach(Self.sets(of: exercise), id: \.self) { number in
                        segment(index: index, number: number, slots: exercise.dose.slots)
                    }
                }
                .padding(.trailing, index < run.exercises.count - 1 ? Space.xxs : 0)
            }
        }
        .frame(height: 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Progression")
        .accessibilityValue("\(run.exercises.indices.filter(run.isDone).count) exercices faits sur \(run.exercises.count)")
    }

    private static func sets(of exercise: Notebook.StrengthExercise) -> [Int] {
        Array(Set(exercise.dose.slots.map(\.n))).sorted()
    }

    private func segment(index: Int, number: Int, slots: [Notebook.StrengthSlot]) -> some View {
        let step = run.step(index)
        let positions = slots.indices.filter { slots[$0].n == number }
        let banked = positions.filter { $0 < step.slots.count }.count
        let fill = positions.isEmpty ? 0 : Double(banked) / Double(positions.count)
        let current = !run.progress.review && index == run.progress.current
            && run.currentSlot.map { $0.n == number } == true
        return ZStack(alignment: .leading) {
            Capsule().fill(current ? Palette.muted : Palette.ghost)
            Capsule().fill(Palette.ink).scaleEffect(x: fill, anchor: .leading)
        }
        .frame(maxWidth: .infinity)
        .animation(.spring(duration: 0.35, bounce: 0.3), value: fill)
    }
}

/// How hard an exercise was to do properly, and a remark on it.
struct ExerciseFeedback: View {
    let model: StrengthModel
    let run: StrengthRun
    let index: Int

    var body: some View {
        let step = run.step(index)
        VStack(alignment: .leading, spacing: Space.xs) {
            Text("Difficulté ressentie · facile ← → trop dur · facultatif").textRole(.caption).foregroundStyle(Palette.muted)
            ScoreScale(value: step.score) { value in model.update { $0.setScore(index, value) } }
            TextField("Une remarque sur cet exercice ?", text: Binding(
                get: { step.note }, set: { text in model.update { $0.setExerciseNote(index, text) } }), axis: .vertical)
                .textRole(.secondary)
                .padding(Space.s)
                .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
        }
        .disabled(run.isLocked)
    }
}
