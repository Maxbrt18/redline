import RedlineKit
import SwiftUI

/// The debrief of a run, asked one value at a time: on Today and in the session detail.
protocol DebriefAsk {
    var id: String { get }
    var fields: [String] { get }
    var labels: [String: String] { get }
    var total: Int { get }
    var durationMin: Int? { get }
    var painValues: [Double] { get }
}

extension Notebook.CellsAsk: DebriefAsk {}
extension Notebook.DetailAsk: DebriefAsk {}

/// How a debrief field is asked: on a scale, by a few options, or by several of them.
struct AskSpec {
    enum Answers {
        case scale([Double], unit: String)
        case options([(JSONValue, String)])
        case several([(String, String)])
    }

    let label: String
    /// The field named in « Puis … ».
    let name: String
    var hint: String?
    let answers: Answers

    /// The field's spec; a zone's pain is named by its label and offered on the instance's
    /// scale, the onset of gut trouble in quarter hours up to the session's duration.
    init(field: String, ask: some DebriefAsk) {
        if field.hasPrefix("pain:") {
            let zone = ask.labels[field] ?? String(field.dropFirst(5))
            self.init(label: "Douleur \(zone), au pic", name: "la douleur (\(zone))", answers: .scale(ask.painValues, unit: ""))
            return
        }
        let tenth = (0...10).map(Double.init)
        switch field {
        case "rpe":
            self.init(label: "RPE, séance entière", name: "le RPE", answers: .scale((1...10).map(Double.init), unit: ""))
        case "reserve":
            self.init(label: "À la fin, il t’en restait une ?", name: "la réserve", hint: "une répétition entière, au même effort",
                      answers: .options([(.number(1), "Oui, une entière"), (.number(0), "Non, j’étais au bout")]))
        case "enjoyment":
            self.init(label: "Plaisir", name: "le plaisir", answers: .scale(tenth, unit: ""))
        case "carbs_g_h":
            self.init(label: "Glucides pris, par heure", name: "les glucides",
                      answers: .scale([0, 20, 30, 40, 50, 60, 70, 80, 90], unit: " g"))
        case "fuel_formats":
            self.init(label: "Sous quelle forme ?", name: "la forme",
                      answers: .several([("drink", "Boisson"), ("gel", "Gel"), ("chew", "Pâte ou gomme"), ("solid", "Solide")]))
        case "fuel_mix":
            self.init(label: "Les sucres", name: "les sucres", answers: .options([
                (.string("glucose"), "Glucose seul"), (.string("glucose_fructose"), "Glucose + fructose"),
                (.string("unknown"), "Je ne sais pas")]))
        case "gut_symptoms":
            self.init(label: "Troubles digestifs, au pic", name: "les troubles", answers: .scale(tenth, unit: ""))
        case "gut_symptom_kinds":
            self.init(label: "Lesquels ?", name: "lesquels", answers: .several([
                ("nausea", "Nausée"), ("reflux", "Reflux"), ("cramps", "Crampes"), ("bloating", "Ballonnement"),
                ("urgency", "Besoin pressant")]))
        case "gut_onset_min":
            let quarters = max(1, Int((Double(ask.durationMin ?? 60) / 15).rounded(.up)))
            self.init(label: "Apparus vers", name: "le moment", answers: .options((1...quarters).map { index in
                (.number(Double(index * 15)), Self.elapsed(index * 15))
            }))
        default:
            self.init(label: field, name: field, answers: .scale(tenth, unit: ""))
        }
    }

    private init(label: String, name: String, hint: String? = nil, answers: Answers) {
        self.label = label
        self.name = name
        self.hint = hint
        self.answers = answers
    }

    /// « 45 min », « 1 h 15 ».
    static func elapsed(_ minutes: Int) -> String {
        minutes < 60 ? "\(minutes) min" : "\(minutes / 60) h" + (minutes % 60 > 0 ? " \(minutes % 60)" : "")
    }
}

extension EnvironmentValues {
    /// What a screen does once one of its questions is answered: say so, and load itself
    /// again so that the next question takes its place.
    @Entry var answered: ((_ alert: Bool) async -> Void)?
    /// Loads the screen again, after a change made from it.
    @Entry var reloadScreen: (() async -> Void)?
}

/// Sends answers for one card, keeps its buttons still while one is on its way, and says
/// why the server refused one.
@Observable
final class AnswerSender {
    private(set) var busy = false
    private(set) var picked: String?
    private(set) var failure: String?

    func send(_ ask: String, value: JSONValue, tag: String, session: NotebookSession?,
              then answered: ((Bool) async -> Void)?) async {
        guard !busy, case .signedIn(let client)? = session?.state else { return }
        busy = true
        picked = tag
        failure = nil
        defer { busy = false; picked = nil }
        do {
            let alert = try await client.answer(ask, value: value)
            await answered?(alert)
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }
}

/// A question's box: a panel, its rows, then a note.
struct AskPanel<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) { content }
            .padding(Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
    }
}

/// Why the server refused an answer, under the card's answers.
private struct AnswerFailure: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "exclamationmark.circle")
            .textRole(.caption)
            .foregroundStyle(Palette.accent)
    }
}

/// One answer button: a full-width cell, filled while its answer is on its way. Its words
/// wrap rather than shrink, so every answer of a card reads at the same size.
private struct AnswerButton: View {
    let text: String
    var picked = false
    var on = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(text)
                .textRole(.secondary)
                .fontWeight(.semibold)
                .monospacedDigit()
                .multilineTextAlignment(.center)
                .padding(.horizontal, Space.xxs)
                .padding(.vertical, Space.xs)
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(picked || on ? Palette.bg : Palette.ink)
                .background(picked || on ? Palette.ink : Palette.bg, in: .rect(cornerRadius: Radius.surface))
                .overlay(RoundedRectangle(cornerRadius: Radius.surface).strokeBorder(Palette.grid, lineWidth: picked || on ? 0 : 1))
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// Answers laid out at a glance, with no hidden scrolling: up to seven on one row, more
/// on two. At the accessibility sizes figures take as many columns as fit, and words one
/// line each.
private struct AnswerGrid: View {
    let answers: [(tag: String, text: String)]
    var columns: Int?
    /// The answers are words, not figures.
    var words = false
    var picked: String?
    var on: Set<String> = []
    let choose: (String) -> Void
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .subheadline) private var figureWidth: CGFloat = 40

    var body: some View {
        LazyVGrid(columns: gridColumns, spacing: Space.xs) {
            ForEach(answers, id: \.tag) { answer in
                AnswerButton(text: answer.text, picked: picked == answer.tag, on: on.contains(answer.tag)) { choose(answer.tag) }
            }
        }
    }

    private var gridColumns: [GridItem] {
        if typeSize.isAccessibilitySize {
            return words ? [GridItem(.flexible())] : [GridItem(.adaptive(minimum: figureWidth), spacing: Space.xs)]
        }
        let count = columns ?? (answers.count <= 7 ? answers.count : (answers.count + 1) / 2)
        return Array(repeating: GridItem(.flexible(), spacing: Space.xs), count: max(count, 1))
    }
}

/// A run's debrief, one value per gesture: the field asked now, its rank, its answers,
/// then what comes after. Each answer reloads the screen, which moves on to the next.
struct DebriefCard<Ask: DebriefAsk>: View {
    let ask: Ask
    var targets: Notebook.Targets?
    @Environment(\.notebook) private var session
    @Environment(\.answered) private var answered
    @State private var sender = AnswerSender()
    @State private var several: Set<String> = []

    var body: some View {
        if let field = ask.fields.first {
            let spec = AskSpec(field: field, ask: ask)
            let key = "\(ask.id):\(field)"
            AskPanel {
                let rank = Text("\(ask.total - ask.fields.count + 1) sur \(ask.total)").textRole(.caption).foregroundStyle(Palette.muted)
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline) {
                        question(field, spec)
                        Spacer(minLength: Space.xs)
                        rank
                    }
                    VStack(alignment: .leading, spacing: Space.xxs) {
                        rank
                        question(field, spec)
                    }
                }
                answers(spec, key: key)
                if let failure = sender.failure { AnswerFailure(text: failure) }
                if let note = note(field, spec) {
                    Text(note).textRole(.caption).foregroundStyle(Palette.muted)
                }
            }
            .disabled(sender.busy)
            .sensoryFeedback(.success, trigger: ask.fields.count)
            .onChange(of: field) { several = [] }
        }
    }

    private func question(_ field: String, _ spec: AskSpec) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(spec.label).textRole(.secondary).fontWeight(.semibold)
            if let aside = aside(field, spec) { Text(aside).textRole(.caption).foregroundStyle(Palette.muted) }
        }
    }

    @ViewBuilder private func answers(_ spec: AskSpec, key: String) -> some View {
        switch spec.answers {
        case .scale(let values, let unit):
            AnswerGrid(answers: values.map { (Numbers.text($0), Numbers.text($0) + unit) }, picked: sender.picked) { tag in
                if let value = values.first(where: { Numbers.text($0) == tag }) { send(key, .number(value), tag: tag) }
            }
        case .options(let options):
            AnswerGrid(answers: options.map { (Self.tag($0.0), $0.1) }, columns: options.count == 3 ? 1 : 2,
                       words: true, picked: sender.picked) { tag in
                if let option = options.first(where: { Self.tag($0.0) == tag }) { send(key, option.0, tag: tag) }
            }
        case .several(let options):
            AnswerGrid(answers: options.map { ($0.0, $0.1) }, columns: 2, words: true, on: several) { tag in
                if several.contains(tag) { several.remove(tag) } else { several.insert(tag) }
            }
            Button {
                send(key, .array(options.map(\.0).filter(several.contains).map(JSONValue.string)), tag: "confirm")
            } label: {
                Text("Valider").fontWeight(.semibold).frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(several.isEmpty)
        }
    }

    private func send(_ key: String, _ value: JSONValue, tag: String) {
        Task { await sender.send(key, value: value, tag: tag, session: session, then: answered) }
    }

    private static func tag(_ value: JSONValue) -> String {
        switch value {
        case .number(let number): Numbers.text(number)
        case .string(let text): text
        default: "\(value)"
        }
    }

    /// What was aimed at, beside the question it bears on.
    private func aside(_ field: String, _ spec: AskSpec) -> String? {
        if field == "rpe", let range = targets?.rpeRange { return "visé \(range)" }
        if field == "carbs_g_h", let target = targets?.carbsTargetGH { return "visé \(target) g/h" }
        return spec.hint
    }

    private func note(_ field: String, _ spec: AskSpec) -> String? {
        let next = ask.fields.dropFirst().map { AskSpec(field: $0, ask: ask).name }
        var notes: [String] = []
        if let last = next.last {
            let listed = next.count > 1 ? next.dropLast().joined(separator: ", ") + " et " + last : last
            notes.append("Puis \(listed)" + (next.count > 1 ? ", un geste chacun." : "."))
        }
        if field.hasPrefix("pain:"), let top = ask.painValues.last {
            notes.append("Plus de \(Numbers.text(top)) ? Dis-le au coach dans Messenger.")
        }
        return notes.isEmpty ? nil : notes.joined(separator: " ")
    }
}

/// The morning: one trace per watched zone, then sleep and the load of life, a gesture
/// per line.
struct MorningCard: View {
    let wakes: [Notebook.WakeAsk]
    let daily: Notebook.DailyAsk?
    @Environment(\.notebook) private var session
    @Environment(\.answered) private var answered
    @State private var sender = AnswerSender()

    var body: some View {
        AskPanel {
            ForEach(wakes, id: \.id) { wake in
                row("Trace · \(wake.label)", hint: wake.afterEffort ? "après la séance d’hier" : "sans séance hier",
                    options: wake.levels.map { ($0.slug, $0.label.capitalizedFirst) }, key: wake.id)
            }
            if let daily {
                ForEach(daily.fields, id: \.self) { field in
                    if let (label, options) = Self.dailyChoices[field] {
                        row(label, hint: nil, options: options, key: "\(daily.id):\(field)")
                    }
                }
            }
            if let failure = sender.failure { AnswerFailure(text: failure) }
            Text("Un geste par ligne. Le coach ne t’écrit que si ça sort de l’ordinaire.")
                .textRole(.caption).foregroundStyle(Palette.muted)
        }
        .disabled(sender.busy)
        .sensoryFeedback(.success, trigger: wakes.count + (daily?.fields.count ?? 0))
    }

    private func row(_ label: String, hint: String?, options: [(String, String)], key: String) -> some View {
        // The hint follows the label in the same paragraph, so both wrap rather than truncate.
        let head = hint.map { hint in
            Text("\(Text(label).fontWeight(.semibold))  \(Text(hint).font(TextRole.caption.font).foregroundStyle(Palette.muted))")
        } ?? Text(label).fontWeight(.semibold)
        return VStack(alignment: .leading, spacing: Space.xs) {
            head.textRole(.secondary).fixedSize(horizontal: false, vertical: true)
            AnswerGrid(answers: options.map { (key + "=" + $0.0, $0.1) }, columns: options.count == 4 ? 2 : options.count,
                       words: true, picked: sender.picked) { tag in
                let slug = String(tag.dropFirst(key.count + 1))
                Task { await sender.send(key, value: .string(slug), tag: tag, session: session, then: answered) }
            }
        }
    }

    static let dailyChoices: [String: (String, [(String, String)])] = [
        "sleep": ("Sommeil", [("good", "Bon"), ("fair", "Moyen"), ("poor", "Mauvais")]),
        "life_load": ("Charge de vie", [("light", "Légère"), ("normal", "Normale"), ("high", "Élevée")]),
    ]
}

/// A heart rate higher than usual at the same pace: what might explain it.
struct HeartCard: View {
    let ask: Notebook.HrAsk
    @Environment(\.notebook) private var session
    @Environment(\.answered) private var answered
    @State private var sender = AnswerSender()

    var body: some View {
        AskPanel {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text("Ton cœur était plus haut que d’habitude").textRole(.secondary).fontWeight(.semibold)
                Text("\(ask.hr) bpm à \(Numbers.pace(Double(ask.paceS)))/km, contre \(ask.reference) sur tes \(ask.n) EF à allure voisine (+\(ask.hr - ask.reference)).")
                    .textRole(.caption).foregroundStyle(Palette.muted)
            }
            Text("Une explication ?").textRole(.secondary).fontWeight(.semibold)
            AnswerGrid(answers: Self.reasons, columns: 2, words: true, picked: sender.picked) { slug in
                Task { await sender.send(ask.id, value: .string(slug), tag: slug, session: session, then: answered) }
            }
            if let failure = sender.failure { AnswerFailure(text: failure) }
        }
        .disabled(sender.busy)
    }

    static let reasons = [("sleep", "Nuit courte"), ("cold", "Début de rhume"), ("heat", "Chaleur ou terrain"),
                          ("none", "Rien de spécial")]
}
