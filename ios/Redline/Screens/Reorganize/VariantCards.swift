import RedlineKit
import SwiftUI

/// One placement the engine found: the seven days as bars, what moves and what goes, the
/// preferences it bends, and the way to take it.
struct VariantCard: View {
    let view: Notebook.ReorganizeView
    let variant: Notebook.Variant
    let index: Int
    let take: () async -> Void
    @State private var taking = false

    var body: some View {
        AskPanel {
            HStack(alignment: .firstTextBaseline) {
                Text("Variante \(index + 1)").font(.headline)
                Spacer()
                Text(summary).font(.caption).foregroundStyle(Palette.muted)
            }
            bars
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(variant.moves.enumerated()), id: \.offset) { _, move in
                    line(move.title.shortTitle, DayWords.day(move.before) + " → " + DayWords.day(move.after), gone: false)
                }
                ForEach(Array(variant.dropped.enumerated()), id: \.offset) { _, drop in
                    line(drop.title.shortTitle, "saute" + (drop.km.map { " · −\(Numbers.text($0)) km" } ?? ""), gone: true)
                }
            }
            if let around = variant.aroundMin {
                Text("Environ \(DayWords.hours(around)) à courir autour du groupe, en allant le rejoindre et en rentrant.")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            ForEach(Array(variant.notes.enumerated()), id: \.offset) { _, note in
                Text(Self.note(note)).font(.footnote).foregroundStyle(Palette.muted)
            }
            Button {
                taking = true
                Task { await take(); taking = false }
            } label: {
                Text("Prendre cette variante").frame(maxWidth: .infinity)
            }
            .buttonStyle(.glassProminent)
            .tint(Palette.tartan)
            .controlSize(.large)
            .disabled(taking)
        }
        .sensoryFeedback(.success, trigger: taking) { _, now in !now }
    }

    private var summary: String {
        var parts: [String] = []
        if !variant.moves.isEmpty { parts.append(variant.moves.count == 1 ? "1 date change" : "\(variant.moves.count) dates changent") }
        if !variant.dropped.isEmpty { parts.append(variant.dropped.count == 1 ? "1 saute" : "\(variant.dropped.count) sautent") }
        return parts.isEmpty ? "comme prévu" : parts.joined(separator: " · ")
    }

    /// A moved session in ink, one left where it was in the plan's dots, a day without one
    /// as a rule.
    private var bars: some View {
        let top = max(1, variant.days.map(\.load).max() ?? 1)
        return HStack(alignment: .bottom, spacing: 6) {
            ForEach(variant.days, id: \.date) { day in
                let past = view.days.first { $0.date == day.date }?.past ?? false
                VStack(spacing: 4) {
                    Text(day.labels.isEmpty ? " " : day.labels.joined(separator: "+"))
                        .font(.caption2.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.6)
                        .foregroundStyle(day.moved ? Palette.ink : Palette.muted)
                    if day.carried {
                        Text("groupe").font(.system(size: 9, weight: .semibold)).foregroundStyle(Palette.hRun)
                    }
                    bar(day, height: 10 + 62 * day.load / top)
                    Text("\(Day(day.date)?.day ?? 0)").font(.figure(15))
                    Text(DayWords.weekday(day.date)).font(.caption2).foregroundStyle(Palette.muted)
                }
                .frame(maxWidth: .infinity)
                .opacity(past ? 0.5 : 1)
            }
        }
        .frame(height: 140, alignment: .bottom)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(variant.days.map { "\(DayWords.day($0.date)) : \($0.labels.isEmpty ? "rien" : $0.labels.joined(separator: " et "))" }
            .joined(separator: ", "))
    }

    @ViewBuilder private func bar(_ day: Notebook.VariantDay, height: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: 3)
        if day.labels.isEmpty {
            shape.fill(Palette.ghost).frame(width: 18, height: 2)
        } else if day.moved {
            shape.fill(Palette.ink).frame(width: 18, height: height)
        } else {
            DotPattern.planned.frame(width: 18, height: height).clipShape(shape)
        }
    }

    private func line(_ title: String, _ detail: String, gone: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold))
            Text(detail).font(.subheadline).foregroundStyle(gone ? Palette.over : Palette.muted)
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
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle(text: "Le coach propose")
            ForEach(view.proposals, id: \.id) { proposal in
                AskPanel {
                    Text(proposal.title).font(.headline)
                    Text(proposal.body).font(.subheadline)
                    if !proposal.lines.isEmpty {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(proposal.lines, id: \.self) { Text($0).font(.footnote).foregroundStyle(Palette.muted) }
                        }
                    }
                    Button { Task { await take(proposal) } } label: { Text("Prendre").frame(maxWidth: .infinity) }
                        .buttonStyle(.glassProminent)
                        .tint(Palette.tartan)
                        .controlSize(.large)
                        .disabled(busy)
                }
            }
            if let failure { Text(failure).font(.footnote).foregroundStyle(Palette.over) }
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
    let sent: (String) -> Void
    @State private var sending = false
    @State private var failure: String?

    private static let wishes = [("intensity", "Plus d’intensité"), ("volume", "Plus de volume"), ("other", "Autre")]

    var body: some View {
        @Bindable var model = model
        let said = model.differing.map { DayWords.day($0.date) + " : " + DayWords.label($0.kind).lowercased() }
            .joined(separator: " · ")
        VStack(alignment: .leading, spacing: 14) {
            SectionTitle(text: "Demander au coach")
            AskPanel {
                (Text("Envoyé avec ta demande : ") + Text(said.isEmpty ? "aucun jour déclaré" : said).bold()
                 + Text(", la semaine telle qu’elle est, et ce que le carnet a pu calculer."))
                    .font(.footnote).foregroundStyle(Palette.muted)
                VStack(alignment: .leading, spacing: 8) {
                    Text("Ce que tu aimerais").font(.subheadline.weight(.semibold))
                    Picker("Ce que tu aimerais", selection: $model.wish) {
                        ForEach(Self.wishes, id: \.0) { Text($0.1).tag($0.0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("Contexte").font(.subheadline.weight(.semibold))
                        Text("facultatif").font(.caption).foregroundStyle(Palette.muted)
                    }
                    TextField("Par exemple : fin des partiels jeudi, je serai plus reposé.", text: Binding(
                        get: { model.context }, set: { model.context = String($0.prefix(500)) }), axis: .vertical)
                        .lineLimit(3...6)
                        .padding(10)
                        .background(Palette.bg, in: .rect(cornerRadius: 10))
                }
                if let failure { Text(failure).font(.footnote).foregroundStyle(Palette.over) }
                Button { Task { await send() } } label: { Text("Envoyer au coach").frame(maxWidth: .infinity) }
                    .buttonStyle(.glassProminent)
                    .tint(Palette.tartan)
                    .controlSize(.large)
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
