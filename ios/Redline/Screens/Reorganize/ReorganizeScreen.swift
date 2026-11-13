import RedlineKit
import SwiftUI

/// A week to reorganise, opened from Week.
struct ReorganizeLink: Hashable {
    let week: String
}

/// The habits, opened from the reorganisation.
struct HabitsLink: Hashable {}

/// The athlete says how each day presents itself, in words of what they can do; the
/// variants follow at once, computed by the engine without the coach, and a tap takes one.
/// Changing a session is the coach's: the request goes to them from here.
struct ReorganizeScreen: View {
    let session: NotebookSession
    let week: String
    @State private var model: ReorganizeModel
    @State private var opened: DayLink?
    @State private var notice: Notice?
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss

    init(session: NotebookSession, week: String) {
        self.session = session
        self.week = week
        _model = State(initialValue: ReorganizeModel(week: week))
    }

    private struct DayLink: Identifiable {
        let date: String
        var id: String { date }
    }

    /// What a request answered, shown where it was made, so that it lands in view.
    private struct Notice {
        enum Place { case save, variants, ask }
        let words: String
        let done: Bool
        let place: Place
    }

    var body: some View {
        PaperScreen(title: "Réorganiser") {
            Kicker(text: "Semaine \(Int(week.suffix(2)) ?? 0)" + (Day(isoMonday: week).map { " · " + weekSpan(monday: $0.text) } ?? ""))
                .foregroundStyle(Palette.muted)
        } content: {
            VStack(alignment: .leading, spacing: 0) {
                if let view = model.view {
                    content(view)
                } else if let failure = model.failure {
                    ContentUnavailableView {
                        Label("Réorganisation indisponible", systemImage: "exclamationmark.icloud")
                    } description: {
                        Text(failure)
                    } actions: {
                        Button("Réessayer") { Task { await model.load(session) } }.buttonStyle(.glass)
                    }
                } else {
                    HStack(spacing: Space.xs) {
                        ProgressView()
                        Text("Calcul des variantes…").textRole(.secondary).foregroundStyle(Palette.muted)
                    }
                    .padding(.top, Space.l)
                }
            }
            .padding(.bottom, Space.l)
        }
        .task { await model.load(session) }
        .sheet(item: $opened) { link in
            if let day = model.view?.days.first(where: { $0.date == link.date }) {
                DaySheet(model: model, day: day, session: session)
            }
        }
        .navigationDestination(for: HabitsLink.self) { _ in HabitsScreen(session: session) }
    }

    @ViewBuilder private func content(_ view: Notebook.ReorganizeView) -> some View {
        if !view.written, view.blocked == nil {
            Intro(text: "Le coach n’a pas encore écrit cette semaine. Dis-lui comment se présentent tes jours : il placera les séances au bon endroit, et tu n’auras rien à réorganiser.")
            days(view)
            Button { Task { await saveDays(view) } } label: {
                Text("Enregistrer pour le bilan").foregroundStyle(Palette.bg).frame(maxWidth: .infinity)
            }
            .buttonStyle(.glassProminent)
            .tint(Palette.accent)
            .controlSize(.large)
            .disabled(saving)
            .padding(.top, Space.l)
            noticeLine(.save)
            habitsRow
        } else {
            Intro(text: "Touche un jour pour dire comment il se présente. Seules les dates changent ; pour changer une séance, c’est le coach.")
            if let blocked = view.blocked {
                Banner(text: Self.blocked(blocked))
                // A variant refused because the week moved on says why here.
                noticeLine(.variants)
            } else {
                days(view)
                if !view.proposals.isEmpty {
                    ProposalsSection(view: view, session: session, model: model) { words in leave(saying: words) }
                        .padding(.top, Space.section)
                }
                noticeLine(.variants)
                variants(view)
                if !view.heldBack.isEmpty {
                    Banner(text: view.heldBack.joined(separator: ", ") + " ne peut pas être avancé : un signal de douleur est actif. Le reculer reste possible ; l’avancer demande l’avis du coach.")
                }
                noticeLine(.ask)
                ask(view)
                habitsRow
            }
        }
    }

    private func days(_ view: Notebook.ReorganizeView) -> some View {
        VStack(spacing: 0) {
            ForEach(view.days, id: \.date) { day in
                let row = DayRow(day: day, stated: model.known(day), habit: model.touched[day.date] == nil && day.stated?.habit == true)
                if day.past {
                    row
                } else {
                    Button { opened = DayLink(date: day.date) } label: { row }.buttonStyle(.plain)
                }
            }
        }
        .padding(.top, Space.xs)
    }

    @ViewBuilder private func variants(_ view: Notebook.ReorganizeView) -> some View {
        let differing = model.differing
        if let best = view.variants.first, !differing.isEmpty {
            if best.moves.isEmpty && best.dropped.isEmpty {
                Banner(text: "Ta semaine tient déjà avec ces jours : rien à déplacer.")
            } else {
                SectionTitle(text: view.variants.count == 1 ? "1 variante" : "\(view.variants.count) variantes")
                    .padding(.top, Space.section)
                // The engine's first placement is the screen's one prominent action, unless the
                // coach's proposals come first.
                ForEach(Array(view.variants.enumerated()), id: \.offset) { index, variant in
                    VariantCard(view: view, variant: variant, index: index, prominent: index == 0 && view.proposals.isEmpty) {
                        await take(view, index)
                    }
                    .padding(.top, Space.s)
                }
                if view.others > 0 {
                    Text("\(view.others) " + (view.others == 1 ? "autre placement respecte" : "autres placements respectent")
                         + " les règles ; ils changent plus de dates ou plient plus de préférences.")
                        .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.s)
                }
            }
        } else if !differing.isEmpty {
            Banner(text: "Aucune variante ne respecte les règles avec ces jours : seul le coach peut changer une séance.")
        }
    }

    /// Changing a session is the coach's: the request is open at once when the days ask
    /// for more, or when no variant holds; otherwise it waits behind a line.
    @ViewBuilder private func ask(_ view: Notebook.ReorganizeView) -> some View {
        let differing = model.differing
        let wanted = differing.contains { $0.kind == .more } || (!differing.isEmpty && view.variants.isEmpty)
        if wanted || model.asking {
            // Sending is the prominent action only when nothing else on the screen is one.
            AskCoachSection(model: model, session: session, prominent: view.variants.isEmpty && view.proposals.isEmpty) { words in
                tell(words, done: true, at: .ask)
                Task { await model.load(session) }
            }
            .padding(.top, Space.section)
        } else {
            Button { withAnimation { model.asking = true } } label: {
                EntryLine(title: "Demander au coach", detail: "changer une séance, en ajouter une")
            }
            .buttonStyle(.plain)
            .padding(.top, Space.l)
        }
    }

    private var habitsRow: some View {
        NavigationLink(value: HabitsLink()) {
            EntryLine(title: "Mes habitudes", detail: "ce qui revient chaque semaine")
        }
        .buttonStyle(.plain)
    }

    private func take(_ view: Notebook.ReorganizeView, _ index: Int) async {
        guard case .signedIn(let client) = session.state else { return }
        do {
            _ = try await client.takeVariant(view.week, days: model.stated, revision: view.revision, choice: index)
            leave(saying: "Semaine réorganisée. Le coach la voit à ton prochain message.")
        } catch {
            notice = nil
            model.asking = false
            await model.load(session)
            tell(NotebookSession.words(for: error), done: false, at: .variants)
        }
    }

    /// The days of a week not written yet, kept for the review: those touched here, and
    /// those stated before that are no habit.
    private func saveDays(_ view: Notebook.ReorganizeView) async {
        guard case .signedIn(let client) = session.state else { return }
        saving = true
        defer { saving = false }
        let days = view.days.filter { !$0.past }.compactMap { day in
            model.touched[day.date] ?? day.stated.flatMap { $0.habit ? nil : StatedDay(date: day.date, state: $0) }
        }
        do {
            try await client.stateDays(view.week, days: days)
            tell("Enregistré : le coach en tiendra compte en écrivant ta semaine.", done: true, at: .save)
            await model.load(session)
        } catch {
            tell(NotebookSession.words(for: error), done: false, at: .save)
        }
    }

    private func tell(_ words: String, done: Bool, at place: Notice.Place) {
        notice = Notice(words: words, done: done, place: place)
        AccessibilityNotification.Announcement(words).post()
    }

    @ViewBuilder private func noticeLine(_ place: Notice.Place) -> some View {
        if let notice, notice.place == place {
            Group {
                if notice.done {
                    Label(notice.words, systemImage: "checkmark.circle.fill")
                        .textRole(.secondary).fontWeight(.semibold)
                } else {
                    RequestFailure(text: notice.words)
                }
            }
            .padding(.top, Space.m)
        }
    }

    private func leave(saying words: String) {
        session.notice = words
        dismiss()
    }

    static func blocked(_ reason: Notebook.ReorganizeView.Blocked) -> String {
        switch reason {
        case .awaiting: "Une question du coach attend ta réponse dans Messenger : réponds-lui avant de réorganiser."
        case .missing: "Aucun programme enregistré pour cette semaine : rien à réorganiser."
        case .closed: "Cette semaine est terminée."
        case .unknown: "Cette semaine ne peut pas être réorganisée."
        }
    }
}

/// A day of the week: its sessions, how it presents itself, and the state's tag. At the
/// accessibility sizes the day goes above, on a line of its own, as in Week.
private struct DayRow: View {
    let day: Notebook.ReorganizeDay
    let stated: StatedDay?
    let habit: Bool
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let today = day.date == Day(.now).text
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    DayNumber(date: day.date, today: today, inline: true)
                    HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            words
                            tag
                        }
                        Spacer(minLength: 0)
                        chevron
                    }
                }
            } else {
                HStack(spacing: Space.s) {
                    DayNumber(date: day.date, today: today)
                    FamilyBadge(family: day.sessions.first.map { Family(kind: $0.kind, workout: $0.workout) } ?? .rest, small: true)
                    words
                    Spacer(minLength: Space.xs)
                    tag
                    chevron
                }
            }
        }
        .padding(.vertical, Space.s)
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }

    /// A past day reads in the quieter colour; it stays legible.
    private var words: some View {
        let sessions = day.sessions
        let title = sessions.isEmpty ? (day.rest ? "Repos" : "Rien de prévu") : sessions.map(\.title.shortTitle).joined(separator: " + ")
        let line = [sessions.first?.figure, stated.map { DayWords.line($0, habit: habit) }].compactMap { $0 }.filter { !$0.isEmpty }
            .joined(separator: " · ")
        return VStack(alignment: .leading, spacing: 2) {
            Text(title).textRole(.secondary).fontWeight(.semibold)
                .foregroundStyle(sessions.isEmpty || day.past ? Palette.muted : Palette.ink)
            if !line.isEmpty { Text(line).textRole(.caption).foregroundStyle(Palette.muted) }
        }
    }

    @ViewBuilder private var tag: some View {
        if let stated, stated.kind != .asPlanned {
            Text(DayWords.label(stated.kind))
                .font(TextRole.caption.font.weight(.semibold))
                .padding(.horizontal, Space.xs).padding(.vertical, Space.xxs)
                .background(Palette.panel, in: .capsule)
        }
    }

    @ViewBuilder private var chevron: some View {
        if !day.past {
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
    }
}

/// A line that opens something: a title, a few words under it, a chevron.
struct EntryLine: View {
    let title: String
    let detail: String

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).textRole(.secondary).fontWeight(.semibold)
                Text(detail).textRole(.caption).foregroundStyle(Palette.muted)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, Space.s)
        .contentShape(.rect)
    }
}

struct Intro: View {
    let text: String

    var body: some View {
        Text(text).textRole(.secondary).foregroundStyle(Palette.muted)
    }
}

/// What stops or bounds the reorganisation, in a panel.
struct Banner: View {
    let text: String

    var body: some View {
        Text(text)
            .textRole(.secondary)
            .padding(Space.m)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
            .padding(.top, Space.m)
    }
}

extension Day {
    /// The Monday of an ISO week label: the week of 4 January is the first.
    init?(isoMonday label: String) {
        guard let match = label.wholeMatch(of: /(\d{4})-W(\d{2})/), let week = Int(match.2),
              let fourth = Day("\(match.1)-01-04") else { return nil }
        self = fourth.adding(1 - DayWords.isoWeekday(fourth.text) + 7 * (week - 1))
    }
}

extension View {
    /// A short choice: segments side by side, or a menu at the accessibility sizes, where
    /// segments would cut their words.
    func segmentsOrMenu() -> some View {
        modifier(SegmentsOrMenu())
    }
}

private struct SegmentsOrMenu: ViewModifier {
    @Environment(\.dynamicTypeSize) private var typeSize

    func body(content: Content) -> some View {
        if typeSize.isAccessibilitySize {
            content.pickerStyle(.menu)
        } else {
            content.pickerStyle(.segmented)
        }
    }
}
