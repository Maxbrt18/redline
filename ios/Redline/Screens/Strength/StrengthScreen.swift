import RedlineKit
import SwiftUI

/// The guided strength session, full screen over the notebook. Before the first set, the
/// head, what to get ready and the opening pain reading; then one exercise at a time, one
/// figure per screen (`StrengthFocus`), the rest drawn in inverted colours; then the
/// review. The list of exercises, the cues and the rarer actions are in the bar's menu.
/// Close is the only way out, and leaving a started session asks first, saying that
/// nothing is lost.
struct StrengthScreen: View {
    let session: NotebookSession
    @State private var model: StrengthModel
    @State private var leaving = false
    @State private var sound = StrengthSound()
    @State private var soundSettings = false
    @State private var overview = false
    @State private var gesture = false
    @State private var rating = false
    @State private var asking = false
    @State private var typed = ""
    @State private var titleInView = true
    @AppStorage(StrengthSound.enabledKey) private var soundOn = true
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.colorScheme) private var scheme

    init(session: NotebookSession, launch: StrengthLaunch) {
        self.session = session
        _model = State(initialValue: StrengthModel(launch: launch, session: session))
    }

    /// The exercise screen is showing, not the opening or the review.
    private var focusing: Bool {
        guard case .running = model.phase, let run = model.run else { return false }
        return !run.needsOpening && !run.progress.review
    }

    /// A rest turns the page over: ink background, paper text.
    private var resting: Bool {
        guard focusing, let run = model.run else { return false }
        return StrengthState(run: run).resting
    }

    private var shownScheme: ColorScheme { resting ? (scheme == .dark ? .light : .dark) : scheme }

    var body: some View {
        NavigationStack {
            content
                .background(Palette.bg)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { toolbar }
                .toolbarColorScheme(resting ? shownScheme : nil, for: .navigationBar)
        }
        .environment(\.colorScheme, shownScheme)
        .animation(.easeInOut(duration: 0.3), value: resting)
        // Sheets and the alert hang outside the inverted page, so a rest does not turn them over.
        .sheet(isPresented: $soundSettings) { StrengthSoundSettings(sound: sound) }
        .sheet(isPresented: $overview) {
            if let run = model.run { StrengthOverview(model: model, run: run) }
        }
        .sheet(isPresented: $gesture) {
            if let run = model.run { StrengthGesture(model: model, run: run) }
        }
        .sheet(isPresented: $rating) {
            if let run = model.run, let index = run.progress.feedback, run.exercises.indices.contains(index) {
                StrengthRating(model: model, run: run, index: index)
            }
        }
        .alert("Combien de répétitions ?", isPresented: $asking) {
            TextField("répétitions", text: $typed).keyboardType(.numberPad)
            Button("Valider") {
                if let count = Int(typed.filter(\.isNumber).prefix(3)) { model.change { $0.validate(count, at: .now) } }
                typed = ""
            }
            Button("Annuler", role: .cancel) { typed = "" }
        }
        .onChange(of: asking) { _, open in model.editing = open }
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
            case .changeSide?: .impact(weight: .medium)
            case .countdown?: .impact(weight: .light)
            case nil: nil
            }
        }
        // A set banked by a tap answers under the finger.
        .sensoryFeedback(trigger: banked) { old, new in new > old ? .success : nil }
    }

    /// Every set validated so far, across the session.
    private var banked: Int {
        model.run.map { run in run.exercises.indices.reduce(0) { $0 + run.step($1).slots.count } } ?? 0
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            Button("Fermer", systemImage: "xmark") {
                if model.run?.isUnfinished == true { leaving = true } else { close() }
            }
            // Attached to the button itself so the popover points at it, not at the page.
            .confirmationDialog("Quitter la séance ?", isPresented: $leaving, titleVisibility: .visible) {
                Button("Quitter la séance") { close() }
                Button("Rester", role: .cancel) {}
            } message: {
                Text("Rien n’est perdu : tu reprendras où tu en es.")
            }
        }
        ToolbarItem(placement: .primaryAction) {
            Button(soundOn ? "Son" : "Son coupé", systemImage: soundOn ? "speaker.wave.2" : "speaker.slash") {
                soundSettings = true
            }
        }
        if focusing, let run = model.run {
            ToolbarItem(placement: .primaryAction) { menu(run) }
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

    private func menu(_ run: StrengthRun) -> some View {
        let index = run.progress.current
        let exercise = run.exercises[index]
        let step = run.step(index)
        let state = StrengthState(run: run)
        return Menu {
            Button("La séance", systemImage: "list.bullet") { overview = true }
            Button("Le geste", systemImage: "info.circle") { gesture = true }
            if let finished = run.progress.feedback, run.exercises.indices.contains(finished) {
                Button("Noter : \(run.exercises[finished].name)", systemImage: "star") { rating = true }
            }
            if exercise.dose.measure == .time {
                Toggle("Enchaîner les séries", isOn: Binding(
                    get: { run.progress.chainsSets }, set: { value in model.update { $0.setChainsSets(value) } }))
                switch state {
                case .ready, .work:
                    // The countdown validates on its own at zero; this is for a set done without it.
                    Button("Série faite sans chrono", systemImage: "checkmark") {
                        model.change { $0.validate(nil, at: .now) }
                    }
                default: EmptyView()
                }
            }
            if run.currentSlot != nil, !step.gated {
                Button(step.slots.isEmpty ? "Passer l’exercice" : "Arrêter ici, garder les séries faites",
                       systemImage: "forward") { model.update { $0.skip(at: .now) } }
            }
            if run.canUndo {
                Button("Annuler la dernière validation", systemImage: "arrow.uturn.backward") { model.update { $0.undo() } }
            }
        } label: {
            Image(systemName: "ellipsis")
        }
        .accessibilityLabel("Plus d’options")
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .opening:
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let title, let text):
            ContentUnavailableView(title, systemImage: "exclamationmark.triangle", description: Text(text))
        case .running:
            if let run = model.run {
                let title = LocalizedStringKey(run.session.title)
                if run.needsOpening {
                    ScrollView {
                        VStack(alignment: .leading, spacing: Space.l) {
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                PageTitle(title: title)
                                SessionHead(session: run.session)
                            }
                            Preparation(preparation: StrengthPreparation(run: run, previous: model.previous))
                            Opening(model: model, run: run)
                        }
                        .padding(.horizontal, Space.gutter)
                        .padding(.bottom, Space.xl)
                    }
                    .compactTitle(title, inView: $titleInView)
                } else if run.progress.review {
                    ScrollView {
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            PageTitle(title: title)
                            StrengthReview(model: model, run: run)
                        }
                        .padding(.horizontal, Space.gutter)
                        .padding(.bottom, Space.xl)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .compactTitle(title, inView: $titleInView)
                } else {
                    StrengthFocus(model: model, run: run, asking: $asking)
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

/// The whole session, opened from the menu: its head, what to get ready, the guardrail's
/// word when it fired, and every exercise; touching one goes to it.
private struct StrengthOverview: View {
    let model: StrengthModel
    let run: StrengthRun
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    SessionHead(session: run.session)
                    if run.gateFired, let say = run.session.gate?.say {
                        Label(say, systemImage: "exclamationmark.shield")
                            .font(TextRole.secondary.font.weight(.semibold)).foregroundStyle(Palette.accent)
                    }
                    Preparation(preparation: StrengthPreparation(run: run, previous: model.previous))
                    VStack(spacing: 0) {
                        ForEach(Array(run.exercises.enumerated()), id: \.offset) { index, exercise in
                            Button {
                                model.update { $0.focus(index) }
                                dismiss()
                            } label: {
                                StepRow(run: run, index: index, exercise: exercise)
                            }
                            .buttonStyle(.plain)
                            .disabled(run.isLocked)
                        }
                    }
                }
                .padding(.horizontal, Space.gutter)
                .padding(.bottom, Space.xl)
            }
            .background(Palette.bg)
            .navigationTitle(run.session.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button(role: .close) { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}

/// How to do the live exercise, and what was done last time: what to read before deciding.
private struct StrengthGesture: View {
    let model: StrengthModel
    let run: StrengthRun
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let exercise = run.exercises[run.progress.current]
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.l) {
                    section("Prescription", lines: [exercise.dose.text]
                            + [exercise.load.map { "Charge prévue : " + StrengthWords.load($0) }].compactMap { $0 }
                            + (exercise.equipment.isEmpty ? [] : ["À préparer : " + exercise.equipment.joined(separator: " · ")]))
                    if !exercise.cue.isEmpty || !(model.cues[exercise.slug] ?? []).isEmpty {
                        section("Le geste", lines: [exercise.cue].filter { !$0.isEmpty } + (model.cues[exercise.slug] ?? []))
                    }
                    if let last = exercise.last { section("Réalisé la dernière fois", lines: [last]) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.l)
            }
            .background(Palette.bg)
            .navigationTitle(exercise.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button(role: .close) { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    private func section(_ title: String, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(title).textRole(.heading)
            ForEach(lines, id: \.self) { Text($0).textRole(.body) }
        }
    }
}

/// The exercise just finished, to rate while the next one waits.
private struct StrengthRating: View {
    let model: StrengthModel
    let run: StrengthRun
    let index: Int
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ExerciseFeedback(model: model, run: run, index: index)
                .padding(Space.l)
                .frame(maxHeight: .infinity, alignment: .top)
                .background(Palette.bg)
                .navigationTitle(run.exercises[index].name)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button(role: .close) { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}

/// The date, the length, the coach's note and the session's rule.
private struct SessionHead: View {
    let session: Notebook.StrengthSession

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            let meta = [Day(session.date)?.spelled().capitalizedFirst ?? session.date,
                        session.durationMin > 0 ? "~\(session.durationMin) min" : ""].filter { !$0.isEmpty }
            Text(meta.joined(separator: " · ")).font(TextRole.caption.font.weight(.semibold)).foregroundStyle(Palette.muted)
            if !session.note.isEmpty { Text(session.note).textRole(.secondary) }
            if !session.rule.isEmpty { Text(session.rule).textRole(.caption).foregroundStyle(Palette.muted) }
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
        .padding(Space.m)
        .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
    }
}

/// One exercise of the session's list: its number, name and what it asks for, or what
/// was done; the live one's number is in the red line.
private struct StepRow: View {
    let run: StrengthRun
    let index: Int
    let exercise: Notebook.StrengthExercise

    var body: some View {
        let live = !run.progress.review && index == run.progress.current
        let done = run.isDone(index)
        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
            Text(String(format: "%02d", index + 1))
                .figure(.medium)
                .foregroundStyle(live ? Palette.accent : Palette.muted)
            VStack(alignment: .leading, spacing: 2) {
                Text(exercise.name).textRole(.body).fontWeight(.semibold)
                    .foregroundStyle(done && !live ? Palette.muted : Palette.ink)
                Text(StrengthWords.dose(of: exercise, step: run.step(index), done: done))
                    .textRole(.secondary).monospacedDigit().foregroundStyle(Palette.muted)
            }
            Spacer(minLength: 0)
            if done && !live {
                Image(systemName: run.step(index).gated || run.step(index).skipped ? "minus.circle" : "checkmark.circle.fill")
                    .foregroundStyle(Palette.muted)
                    .accessibilityLabel(run.step(index).gated || run.step(index).skipped ? "pas fait" : "fait")
            }
        }
        .padding(.vertical, Space.s)
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
