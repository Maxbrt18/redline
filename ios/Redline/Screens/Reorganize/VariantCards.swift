import RedlineKit
import SwiftUI

/// One placement the engine found: the seven days as bars, what moves and what goes, the
/// preferences it bends, and the way to take it.
struct VariantCard: View {
    let view: Notebook.ReorganizeView
    let variant: Notebook.Variant
    let index: Int
    /// Whether taking it is the screen's one prominent action.
    let prominent: Bool
    let take: () async -> Void
    @State private var taking = false

    var body: some View {
        AskPanel {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline) {
                    title
                    Spacer()
                    summaryText
                }
                VStack(alignment: .leading, spacing: Space.xxs) {
                    title
                    summaryText
                }
            }
            bars
            VStack(alignment: .leading, spacing: Space.xxs) {
                ForEach(Array(variant.moves.enumerated()), id: \.offset) { _, move in
                    line(move.title.shortTitle, DayWords.day(move.before) + " → " + DayWords.day(move.after), gone: false)
                }
                ForEach(Array(variant.dropped.enumerated()), id: \.offset) { _, drop in
                    line(drop.title.shortTitle, "saute" + (drop.km.map { " · −\(Numbers.text($0)) km" } ?? ""), gone: true)
                }
            }
            if let around = variant.aroundMin {
                Text("Environ \(DayWords.hours(around)) à courir autour du groupe, en allant le rejoindre et en rentrant.")
                    .textRole(.caption).foregroundStyle(Palette.muted)
            }
            ForEach(Array(variant.notes.enumerated()), id: \.offset) { _, note in
                Text(Self.note(note)).textRole(.caption).foregroundStyle(Palette.muted)
            }
            TakeButton(text: "Prendre cette variante", prominent: prominent) {
                taking = true
                Task { await take(); taking = false }
            }
            .disabled(taking)
        }
        .sensoryFeedback(.success, trigger: taking) { _, now in !now }
    }

    private var title: some View {
        Text("Variante \(index + 1)").textRole(.body).fontWeight(.semibold)
    }

    private var summaryText: some View {
        Text(summary).textRole(.caption).foregroundStyle(Palette.muted)
    }

    private var summary: String {
        var parts: [String] = []
        if !variant.moves.isEmpty { parts.append(variant.moves.count == 1 ? "1 date change" : "\(variant.moves.count) dates changent") }
        if !variant.dropped.isEmpty { parts.append(variant.dropped.count == 1 ? "1 saute" : "\(variant.dropped.count) sautent") }
        return parts.isEmpty ? "comme prévu" : parts.joined(separator: " · ")
    }

    /// A moved session in ink, one left where it was in the plan's dots, a day without one
    /// as a rule. Seven columns share the width, so the graphic stops growing with the text
    /// size; the moves are said again in words below it.
    private var bars: some View {
        let top = max(1, variant.days.map(\.load).max() ?? 1)
        return HStack(alignment: .bottom, spacing: Space.xxs) {
            ForEach(variant.days, id: \.date) { day in
                let past = view.days.first { $0.date == day.date }?.past ?? false
                VStack(spacing: Space.xxs) {
                    Text(day.labels.isEmpty ? " " : day.labels.joined(separator: "+"))
                        .font(.caption2.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.6)
                        .foregroundStyle(day.moved ? Palette.ink : Palette.muted)
                    if day.carried {
                        Text("groupe").font(.caption2).lineLimit(1).minimumScaleFactor(0.6).foregroundStyle(Palette.muted)
                    }
                    bar(day, height: 10 + 62 * day.load / top)
                    Text("\(Day(day.date)?.day ?? 0)").figure(.inline, past ? .planned : .done)
                        .foregroundStyle(past ? Palette.muted : Palette.ink)
                    Text(DayWords.weekday(day.date)).font(.caption2).foregroundStyle(Palette.muted)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(variant.days.map { "\(DayWords.day($0.date)) : \($0.labels.isEmpty ? "rien" : $0.labels.joined(separator: " et "))" }
            .joined(separator: ", "))
    }

    @ViewBuilder private func bar(_ day: Notebook.VariantDay, height: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: Radius.mark)
        if day.labels.isEmpty {
            shape.fill(Palette.ghost).frame(width: 18, height: 2)
        } else if day.moved {
            shape.fill(Palette.ink).frame(width: 18, height: height)
        } else {
            DotPattern.planned.frame(width: 18, height: height).clipShape(shape)
        }
    }

    private func line(_ title: String, _ detail: String, gone: Bool) -> some View {
        // What goes says so in its words (« saute »); it reads in ink, what moves in muted.
        HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
            Text(title).textRole(.secondary).fontWeight(.semibold)
            Text(detail).textRole(.secondary).foregroundStyle(gone ? Palette.ink : Palette.muted)
        }
    }

    static func note(_ note: Notebook.VariantNote) -> String {
        let title = note.title ?? ""
        let when = note.date.map(DayWords.day) ?? ""
        return switch note.remark {
        case .impactNearHard: "\(title) (avec sauts) tombe la veille ou le jour d’une séance exigeante."
        case .loadAbovePlan: "Trois jours plus chargés que prévu (+\(note.value ?? 0) %)."
        case .longOffDays: "\(title) quitte ses jours habituels (\(when))."
        case .groupUncarried: "Aucune séance ne va avec le groupe du \(when)."
        default: "\(title) tombe sur un jour de repos prévu (\(when))."
        }
    }
}

/// What the coach proposed for this revision of the week, each taken with one tap.
struct ProposalsSection: View {
    let view: Notebook.ReorganizeView
    let session: NotebookSession
    let model: ReorganizeModel
    let taken: (String) -> Void
    @State private var busy = false
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            SectionTitle(text: "Le coach propose")
            // The coach's first proposal is the screen's one prominent action.
            ForEach(Array(view.proposals.enumerated()), id: \.element.id) { index, proposal in
                AskPanel {
                    Text(proposal.title).textRole(.body).fontWeight(.semibold)
                    Text(proposal.body).textRole(.secondary)
                    if !proposal.lines.isEmpty {
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            ForEach(proposal.lines, id: \.self) { Text($0).textRole(.caption).foregroundStyle(Palette.muted) }
                        }
                    }
                    TakeButton(text: "Prendre", prominent: index == 0) { Task { await take(proposal) } }
                        .disabled(busy)
                }
            }
            if let failure { RequestFailure(text: failure) }
            Button { Task { await dismissAll() } } label: { ChevronRow(text: "Aucune, je garde ma semaine") }
                .buttonStyle(.plain)
                .disabled(busy)
        }
    }

    private func take(_ proposal: Notebook.Proposal) async {
        guard case .signedIn(let client) = session.state else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await client.takeProposal(proposal.id)
            taken("Proposition prise : ta semaine est à jour.")
        } catch {
            failure = NotebookSession.words(for: error)
            await model.load(session)
        }
    }

    private func dismissAll() async {
        guard case .signedIn(let client) = session.state else { return }
        busy = true
        defer { busy = false }
        do {
            try await client.dismissProposals(view.week)
            await model.load(session)
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }
}

/// The request to the coach: the days stated go with it, with what the athlete would like
/// and, if they wish, why.
struct AskCoachSection: View {
    let model: ReorganizeModel
    let session: NotebookSession
    /// Whether sending is the screen's one prominent action.
    let prominent: Bool
    let sent: (String) -> Void
    @State private var sending = false
    @State private var failure: String?

    private static let wishes = [("intensity", "Plus d’intensité"), ("volume", "Plus de volume"), ("other", "Autre")]

    var body: some View {
        @Bindable var model = model
        let said = model.differing.map { DayWords.day($0.date) + " : " + DayWords.label($0.kind).lowercased() }
            .joined(separator: " · ")
        let days = Text(said.isEmpty ? "aucun jour déclaré" : said).bold()
        VStack(alignment: .leading, spacing: Space.s) {
            SectionTitle(text: "Demander au coach")
            AskPanel {
                Text("Envoyé avec ta demande : \(days), la semaine telle qu’elle est, et ce que le carnet a pu calculer.")
                    .textRole(.caption).foregroundStyle(Palette.muted)
                VStack(alignment: .leading, spacing: Space.xs) {
                    Text("Ce que tu aimerais").textRole(.secondary).fontWeight(.semibold)
                    Picker("Ce que tu aimerais", selection: $model.wish) {
                        ForEach(Self.wishes, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    .segmentsOrMenu()
                    .labelsHidden()
                }
                VStack(alignment: .leading, spacing: Space.xs) {
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        Text("Contexte").textRole(.secondary).fontWeight(.semibold)
                        Text("facultatif").textRole(.caption).foregroundStyle(Palette.muted)
                    }
                    TextField("Par exemple : fin des partiels jeudi, je serai plus reposé.", text: Binding(
                        get: { model.context }, set: { model.context = String($0.prefix(500)) }), axis: .vertical)
                        .lineLimit(3...6)
                        .padding(Space.s)
                        .background(Palette.bg, in: .rect(cornerRadius: Radius.surface))
                }
                if let failure { RequestFailure(text: failure) }
                TakeButton(text: "Envoyer au coach", prominent: prominent) { Task { await send() } }
                    .disabled(sending)
            }
        }
    }

    private func send() async {
        guard case .signedIn(let client) = session.state else { return }
        sending = true
        defer { sending = false }
        do {
            try await client.askCoach(model.week, days: model.stated, wish: model.wish, context: model.context)
            model.asking = false
            model.context = ""
            failure = nil
            sent("Envoyé : le coach répond dans Messenger, et ses propositions arrivent ici.")
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }
}

/// A full-width action in a card: filled in the red line when it is the screen's one
/// prominent action, in glass otherwise.
private struct TakeButton: View {
    let text: String
    let prominent: Bool
    let action: () -> Void

    var body: some View {
        if prominent {
            Button(action: action) { Text(text).frame(maxWidth: .infinity) }
                .buttonStyle(.glassProminent)
                .tint(Palette.accent)
                .controlSize(.large)
        } else {
            Button(action: action) { Text(text).frame(maxWidth: .infinity) }
                .buttonStyle(.glass)
                .controlSize(.large)
        }
    }
}

/// A request that failed: a symbol says so, the words say why.
struct RequestFailure: View {
    let text: String

    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .textRole(.caption)
            .foregroundStyle(Palette.accent)
    }
}
