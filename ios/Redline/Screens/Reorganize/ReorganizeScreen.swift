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
    @State private var notice: String?
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

    var body: some View {
        PaperScreen(title: "Réorganiser") {
            Kicker(text: "Semaine \(Int(week.suffix(2)) ?? 0)" + (Day(isoMonday: week).map { " · " + weekSpan(monday: $0.text) } ?? ""))
                .foregroundStyle(Palette.muted)
        } content: {
            VStack(alignment: .leading, spacing: 0) {
                if let notice {
                    Label(notice, systemImage: "checkmark.circle.fill").font(.footnote.weight(.semibold)).padding(.bottom, 12)
                }
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
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Calcul des variantes…").font(.subheadline).foregroundStyle(Palette.muted)
                    }
                    .padding(.top, 20)
                }
            }
            .padding(.bottom, 24)
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
                Text("Enregistrer pour le bilan").frame(maxWidth: .infinity)
            }
            .buttonStyle(.glassProminent)
            .tint(Palette.tartan)
            .controlSize(.large)
            .disabled(saving)
            .padding(.top, 20)
            habitsRow
        } else {
            Intro(text: "Touche un jour pour dire comment il se présente. Seules les dates changent ; pour changer une séance, c’est le coach.")
            if let blocked = view.blocked {
                Banner(text: Self.blocked(blocked))
            } else {
                days(view)
                if !view.proposals.isEmpty {
                    ProposalsSection(view: view, session: session, model: model) { words in leave(saying: words) }
                }
                variants(view)
                if !view.heldBack.isEmpty {
                    Banner(text: view.heldBack.joined(separator: ", ") + " ne peut pas être avancé : un signal de douleur est actif. Le reculer reste possible ; l’avancer demande l’avis du coach.")
                }
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
        .padding(.top, 8)
    }

    @ViewBuilder private func variants(_ view: Notebook.ReorganizeView) -> some View {
        let differing = model.differing
        if let best = view.variants.first, !differing.isEmpty {
            if best.moves.isEmpty && best.dropped.isEmpty {
                Banner(text: "Ta semaine tient déjà avec ces jours : rien à déplacer.")
            } else {
                SectionTitle(text: view.variants.count == 1 ? "1 variante" : "\(view.variants.count) variantes")
                ForEach(Array(view.variants.enumerated()), id: \.offset) { index, variant in
                    VariantCard(view: view, variant: variant, index: index) { await take(view, index) }
                        .padding(.top, 14)
                }
                if view.others > 0 {
                    Text("\(view.others) " + (view.others == 1 ? "autre placement respecte" : "autres placements respectent")
                         + " les règles ; ils changent plus de dates ou plient plus de préférences.")
                        .font(.footnote).foregroundStyle(Palette.muted).padding(.top, 12)
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
            AskCoachSection(model: model, session: session) { words in
                notice = words
                Task { await model.load(session) }
            }
        } else {
            Button { withAnimation { model.asking = true } } label: {
                EntryLine(title: "Demander au coach", detail: "changer une séance, en ajouter une")
            }
            .buttonStyle(.plain)
            .padding(.top, 20)
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
            notice = NotebookSession.words(for: error)
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
            notice = "Enregistré : le coach en tiendra compte en écrivant ta semaine."
            await model.load(session)
        } catch {
            notice = NotebookSession.words(for: error)
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

/// A day of the week: its sessions, how it presents itself, and the state's tag.
private struct DayRow: View {
    let day: Notebook.ReorganizeDay
    let stated: StatedDay?
    let habit: Bool

    var body: some View {
        let sessions = day.sessions
        let title = sessions.isEmpty ? (day.rest ? "Repos" : "Rien de prévu") : sessions.map(\.title.shortTitle).joined(separator: " + ")
        let set = stated.map { $0.kind != .asPlanned } ?? false
        let line = [sessions.first?.figure, stated.map { DayWords.line($0, habit: habit) }].compactMap { $0 }.filter { !$0.isEmpty }
            .joined(separator: " · ")
        HStack(spacing: 12) {
            DayNumber(date: day.date, today: day.date == Day(.now).text)
            FamilyBadge(family: sessions.first.map { Family(kind: $0.kind, workout: $0.workout) } ?? .rest, small: true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(sessions.isEmpty ? Palette.muted : Palette.ink)
                if !line.isEmpty { Text(line).font(.caption).foregroundStyle(Palette.muted) }
            }
            Spacer(minLength: 6)
            if set, let stated {
                Text(DayWords.label(stated.kind))
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Palette.panel, in: .capsule)
            }
            if !day.past {
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .padding(.vertical, 10)
        .opacity(day.past ? 0.55 : 1)
        .contentShape(.rect)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
        .accessibilityElement(children: .combine)
    }
}

/// A line that opens something: a title, a few words under it, a chevron.
struct EntryLine: View {
    let title: String
    let detail: String

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail).font(.caption).foregroundStyle(Palette.muted)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
        }
        .padding(.vertical, 12)
        .contentShape(.rect)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.grid).frame(height: 0.5) }
    }
}

struct Intro: View {
    let text: String

    var body: some View {
        Text(text).font(.subheadline).foregroundStyle(Palette.muted)
    }
}

/// What stops or bounds the reorganisation, in a panel.
struct Banner: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.subheadline)
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel, in: .rect(cornerRadius: 12))
            .padding(.top, 16)
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
