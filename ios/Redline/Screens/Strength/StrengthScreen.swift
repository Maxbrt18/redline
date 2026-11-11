import RedlineKit
import SwiftUI

/// The guided strength session, full screen over the notebook: its head, the opening pain
/// reading when the coach set a guardrail, the exercises with the live one open, then the
/// review. Close is the only way out, and leaving a started session asks first, saying that
/// nothing is lost.
struct StrengthScreen: View {
    let session: NotebookSession
    @State private var model: StrengthModel
    @State private var leaving = false
    @State private var sound = StrengthSound()
    @State private var soundSettings = false
    @AppStorage(StrengthSound.enabledKey) private var soundOn = true
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    init(session: NotebookSession, launch: StrengthLaunch) {
        self.session = session
        _model = State(initialValue: StrengthModel(launch: launch, session: session))
    }

    var body: some View {
        NavigationStack {
            content
                .background(Palette.bg)
                .navigationTitle(model.run?.session.title ?? "Renfo")
                .navigationBarTitleDisplayMode(.large)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Fermer", systemImage: "xmark") {
                            if model.run?.isUnfinished == true { leaving = true } else { close() }
                        }
                    }
                    ToolbarItem(placement: .primaryAction) {
                        Button(soundOn ? "Son" : "Son coupé", systemImage: soundOn ? "speaker.wave.2" : "speaker.slash") {
                            soundSettings = true
                        }
                    }
                    // The number pad has no key to close it, and it covers the button forward.
                    ToolbarItemGroup(placement: .keyboard) {
                        Spacer()
                        Button("OK") {
                            UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                        }
                        .fontWeight(.semibold)
                    }
                }
                .sheet(isPresented: $soundSettings) { StrengthSoundSettings(sound: sound) }
                .confirmationDialog("Quitter la séance ?", isPresented: $leaving, titleVisibility: .visible) {
                    Button("Quitter", role: .destructive) { close() }
                    Button("Rester", role: .cancel) {}
                } message: {
                    Text("Rien n’est perdu : tu reprendras où tu en es.")
                }
        }
        .interactiveDismissDisabled()
        .task {
            await model.open()
            await StrengthNotifier.ask()
        }
        .onAppear {
            UIApplication.shared.isIdleTimerDisabled = true
            sound.start()
        }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
            sound.stop()
            StrengthNotifier.withdraw()
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                model.enterBackground()
                StrengthNotifier.schedule(for: model.run)
            case .active:
                StrengthNotifier.withdraw()
                sound.start()
                Task { await model.enterForeground() }
            default: break
            }
        }
        .onChange(of: model.signals?.count) {
            for signal in model.signals?.list ?? [] {
                sound.play(signal, saying: Self.announcement(signal))
            }
        }
        .sensoryFeedback(trigger: model.signals?.count) { _, _ in
            switch model.signals?.list.last {
            case .workStart?, .restEnd?: .impact(weight: .heavy)
            case .workEnd?: .success
            case .changeSide?: .warning
            case .countdown?: .impact(weight: .light)
            case nil: nil
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .opening:
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let title, let text):
            ContentUnavailableView(title, systemImage: "exclamationmark.triangle", description: Text(text))
        case .running:
            if let run = model.run {
                ScrollViewReader { proxy in
                    ScrollView {
                        StrengthPage(model: model, run: run)
                            .padding(.horizontal, 20)
                            .padding(.bottom, 32)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .onChange(of: run.progress.review ? -1 : run.progress.current) { _, index in
                        withAnimation { proxy.scrollTo(index < 0 ? "review" : "step-\(index)", anchor: .center) }
                    }
                }
            }
        }
    }

    /// What the voice says, when the athlete asked for it: the side of a set, or a change of side.
    private static func announcement(_ signal: StrengthSignal) -> String? {
        switch signal {
        case .workStart(let side): StrengthWords.side(side)
        case .changeSide: "Change de côté"
        default: nil
        }
    }

    private func close() {
        model.close()
        let onClose = model.launch.onClose
        dismiss()
        Task { await onClose?() }
    }
}

/// Everything under the title, in reading order.
private struct StrengthPage: View {
    let model: StrengthModel
    let run: StrengthRun

    var body: some View {
        let session = run.session
        VStack(alignment: .leading, spacing: 18) {
            SessionHead(session: session)
            if model.unsaved {
                Label("La sauvegarde sur ce téléphone est indisponible. Garde la séance ouverte jusqu’à l’envoi.",
                      systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(Palette.over)
            }
            Preparation(preparation: StrengthPreparation(run: run, previous: model.previous))
            if run.needsOpening {
                Opening(model: model, run: run)
            } else {
                if run.gateFired, let say = session.gate?.say {
                    Label(say, systemImage: "exclamationmark.shield")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Palette.over)
                }
                VStack(spacing: 0) {
                    ForEach(Array(run.exercises.enumerated()), id: \.offset) { index, exercise in
                        StepRow(model: model, run: run, index: index, exercise: exercise).id("step-\(index)")
                    }
                }
                if run.progress.review {
                    StrengthReview(model: model, run: run).id("review")
                }
            }
        }
    }
}

/// The date, the length, the coach's note and the session's rule.
private struct SessionHead: View {
    let session: Notebook.StrengthSession

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            let meta = [Day(session.date)?.spelled().capitalizedFirst ?? session.date,
                        session.durationMin > 0 ? "~\(session.durationMin) min" : ""].filter { !$0.isEmpty }
            Text(meta.joined(separator: " · ")).font(.footnote.weight(.semibold)).foregroundStyle(Palette.muted)
            if !session.note.isEmpty { Text(session.note).font(.subheadline) }
            if !session.rule.isEmpty { Text(session.rule).font(.footnote).foregroundStyle(Palette.muted) }
        }
    }
}

/// What to get ready, and what changed since the previous session of the same routine.
private struct Preparation: View {
    let preparation: StrengthPreparation
    @AppStorage("strength.preparationOpen") private var open = false

    var body: some View {
        DisclosureGroup("Préparation", isExpanded: $open) {
            VStack(alignment: .leading, spacing: 8) {
                Text(preparation.items.isEmpty ? "Aucun matériel renseigné pour cette séance."
                     : "Matériel : " + preparation.items.map(Self.text).joined(separator: " · "))
                if let changes = preparation.changes {
                    Text("Changements depuis la précédente prescription du même type :").foregroundStyle(Palette.muted)
                    if changes.isEmpty {
                        Text("· Prescription identique.")
                    }
                    ForEach(Array(changes.enumerated()), id: \.offset) { _, change in
                        Text("· " + Self.text(change))
                    }
                } else {
                    Text("Pas de prescription précédente comparable disponible.").foregroundStyle(Palette.muted)
                }
            }
            .font(.footnote)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 8)
        }
        .font(.subheadline.weight(.semibold))
        .tint(Palette.ink)
    }

    static func text(_ item: StrengthPreparation.Item) -> String {
        switch item {
        case .equipment(let name): name
        case .dumbbells(let load): "Haltère(s) : " + StrengthWords.load(load)
        }
    }

    static func text(_ change: StrengthPreparation.Change) -> String {
        switch change {
        case .added(let exercise): "\(exercise.name) : nouveau dans cette séance"
        case .removed(let name): "\(name) : absent de la prescription du jour"
        case .changed(let exercise, let aspects):
            "\(exercise.name) : " + [
                aspects.contains(.dose) ? "dose : \(exercise.dose.text)" : nil,
                aspects.contains(.load) ? "charge : \(exercise.load.map(StrengthWords.load) ?? "non renseignée")" : nil,
                aspects.contains(.cue) ? "repère du geste actualisé" : nil,
                aspects.contains(.equipment) ? "matériel actualisé" : nil,
            ].compactMap { $0 }.joined(separator: " · ")
        }
    }
}

/// The pain right now, before starting: what arms the guardrail, and what makes the
/// reading at the end comparable. Skippable, like everything here.
private struct Opening: View {
    let model: StrengthModel
    let run: StrengthRun

    var body: some View {
        let zones = run.gateZones
        VStack(alignment: .leading, spacing: 14) {
            Text("La douleur, là, avant de commencer").font(.title3.weight(.semibold))
            Text("0 = rien du tout, 10 = ça fait mal en marchant. Trois secondes, et le chiffre de la fin devient lisible : sans celui-ci, il n’a rien à quoi se comparer.")
                .font(.subheadline).foregroundStyle(Palette.muted)
            ForEach(zones, id: \.zone) { zone in
                if zones.count > 1 { Text(zone.label).font(.subheadline.weight(.semibold)) }
                ScoreScale(value: run.pain(zone.zone, .before)) { value in
                    model.update { $0.answerOpening(zone: zone.zone, pain: value) }
                }
            }
            Button("Je passe") { model.update { $0.skipOpening() } }
                .buttonStyle(.bordered)
                .tint(Palette.ink)
        }
        .padding(14)
        .background(Palette.panel, in: .rect(cornerRadius: 16))
    }
}

/// One exercise of the list: its number, name and what it asks for (or what was done);
/// the live one carries its control.
private struct StepRow: View {
    let model: StrengthModel
    let run: StrengthRun
    let index: Int
    let exercise: Notebook.StrengthExercise

    var body: some View {
        let live = !run.progress.review && index == run.progress.current
        let done = run.isDone(index)
        VStack(alignment: .leading, spacing: 12) {
            if live {
                head(live: true, done: done)
            } else {
                Button {
                    model.update { $0.focus(index) }
                } label: {
                    head(live: false, done: done)
                }
                .buttonStyle(.plain)
                .disabled(run.isLocked)
            }
            if live {
                StrengthControl(model: model, run: run, index: index)
            }
        }
        .padding(.vertical, 14)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
    }

    private func head(live: Bool, done: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(String(format: "%02d", index + 1))
                .font(.figure(20, relativeTo: .headline))
                .foregroundStyle(live ? Palette.red : Palette.faint)
            VStack(alignment: .leading, spacing: 3) {
                Text(exercise.name).font(.body.weight(.semibold)).foregroundStyle(done && !live ? Palette.muted : Palette.ink)
                if live, !exercise.target.isEmpty {
                    Text(exercise.target).font(.footnote).foregroundStyle(Palette.muted)
                }
                if !live {
                    Text(StrengthWords.dose(of: exercise, step: run.step(index), done: done))
                        .font(.subheadline).monospacedDigit().foregroundStyle(Palette.muted)
                }
            }
            Spacer(minLength: 0)
            if done && !live {
                Image(systemName: run.step(index).gated || run.step(index).skipped ? "minus.circle" : "checkmark.circle.fill")
                    .foregroundStyle(Palette.faint)
            }
        }
        .contentShape(.rect)
    }
}

/// 0 to 10 at a glance; a second tap on the value chosen clears it.
struct ScoreScale: View {
    let value: Int?
    var range = StrengthRun.scores
    let choose: (Int?) -> Void

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Array(range), id: \.self) { step in
                let on = value == step
                Button {
                    choose(on ? nil : step)
                } label: {
                    Text("\(step)")
                        .font(.subheadline.weight(.semibold))
                        .monospacedDigit()
                        .frame(maxWidth: .infinity, minHeight: 38)
                        .foregroundStyle(on ? Palette.bg : Palette.ink)
                        .background(on ? Palette.ink : Palette.bg, in: .rect(cornerRadius: 8))
                        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.grid, lineWidth: on ? 0 : 1))
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .sensoryFeedback(.selection, trigger: value)
    }
}
