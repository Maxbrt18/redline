import RedlineKit
import SwiftUI

/// Plan: the cycle in force, whole. The head says what it is and where the athlete stands
/// in it; then the proposal waiting to be taken, the trame in bars, the week in progress and
/// the two after it, the flagship sessions, every week of the cycle, what to watch and what
/// follows.
struct PlanScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: NotebookTab.plan.title, session: session,
                       model: ScreenModel(.plan)) { plan in
            PlanHero(plan: plan)
        } content: { plan in
            PlanContent(plan: plan, session: session)
        }
        .navigationDestination(for: WeekLink.self) { link in
            WeekScreen(session: session, label: link.label, root: false)
        }
    }
}

private struct PlanHero: View {
    let plan: Notebook.PlanView

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            if let cycle = plan.cycle {
                Kicker(text: PlanWords.title(of: cycle))
                if let race = cycle.race {
                    Text(PlanWords.race(race)).textRole(.secondary).opacity(0.9)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .lastTextBaseline, spacing: 10) { position(of: cycle) }
                    VStack(alignment: .leading, spacing: 0) { position(of: cycle) }
                }
            } else {
                Text("Le planificateur met un cycle en place à son prochain passage.")
                    .textRole(.secondary).opacity(0.9)
            }
        }
    }

    @ViewBuilder private func position(of cycle: Notebook.PlanCycle) -> some View {
        let index = cycle.weeks.firstIndex { $0.state == .current }
        if let index {
            Text("\(index + 1)").figure(.hero)
            Text("semaine sur \(cycle.weeks.count)").textRole(.secondary).opacity(0.82)
            Spacer(minLength: Space.xs)
            Text(PlanWords.phase(cycle.weeks[index].phase)).textRole(.secondary).bold()
        } else if let first = cycle.weeks.first {
            Text("\(cycle.weeks.count)").figure(.hero)
            Text("semaines à partir du \(PlanWords.day(first.monday))")
                .textRole(.secondary).opacity(0.82)
        }
    }
}

private struct PlanContent: View {
    let plan: Notebook.PlanView
    let session: NotebookSession
    @State private var showingProposal = false
    @Environment(\.reloadScreen) private var reload

    var body: some View {
        VStack(alignment: .leading, spacing: Space.section) {
            if let proposal = plan.proposal {
                ProposalBanner(proposal: proposal) { showingProposal = true }
            }
            if let cycle = plan.cycle {
                VStack(alignment: .leading, spacing: 0) {
                    SectionTitle(text: "La trame", meta: "km par semaine")
                    TrameBars(weeks: cycle.weeks)
                }
                NearWeeks(weeks: near(cycle))
                flagships(cycle)
                PhaseWeeks(cycle: cycle)
                if !cycle.risks.isEmpty {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        Text("À surveiller").textRole(.body).bold()
                        ForEach(Array(cycle.risks.enumerated()), id: \.offset) { _, risk in
                            Text(PlanWords.risk(risk)).textRole(.secondary)
                        }
                    }
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
                }
                Text(PlanWords.next(of: cycle)).textRole(.secondary).foregroundStyle(Palette.muted)
            }
        }
        .sheet(isPresented: $showingProposal) {
            ProposalSheet(session: session) { await reload() }
        }
    }

    /// The week in progress and the two after it.
    private func near(_ cycle: Notebook.PlanCycle) -> [Notebook.PlanWeek] {
        guard let index = cycle.weeks.firstIndex(where: { $0.state == .current }) else {
            return Array(cycle.weeks.prefix(3))
        }
        return Array(cycle.weeks[index..<min(index + 3, cycle.weeks.count)])
    }

    @ViewBuilder private func flagships(_ cycle: Notebook.PlanCycle) -> some View {
        let found = cycle.weeks.flatMap { week in
            week.keys.filter { $0.role == .flagship }.map { (week, $0) }
        }
        if !found.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                SectionTitle(text: "Séances phares")
                ForEach(Array(found.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                        Image(systemName: "star.fill").foregroundStyle(Palette.accent)
                            .accessibilityLabel("Séance phare")
                        VStack(alignment: .leading, spacing: 2) {
                            Text(PlanWords.weekName(item.0.week)).textRole(.body).bold()
                            Text(PlanWords.key(item.1, numbered: false)).textRole(.secondary)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                    .padding(.vertical, Space.xs)
                }
            }
        }
    }
}

/// The proposal waiting for the athlete, in ink: one tap opens it whole.
private struct ProposalBanner: View {
    let proposal: Notebook.PlanProposalSummary
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("Proposition en attente").font(TextRole.caption.font.weight(.semibold))
                    .foregroundStyle(Palette.onDoneAlert)
                Text(proposal.race.map { "Préparation, \($0.name)" } ?? "Préparation")
                    .textRole(.heading)
                Text("\(proposal.weeks) semaines à partir du \(PlanWords.day(proposal.startsOn)), "
                     + "jusqu’à \(Numbers.text(proposal.peakKm))\u{00a0}km par semaine. "
                     + "Elle t’attend jusqu’au \(PlanWords.day(proposal.expiresOn)).")
                    .textRole(.secondary).opacity(0.86)
                Text("Voir la proposition").textRole(.body).bold()
                    .padding(.horizontal, Space.m).padding(.vertical, Space.s)
                    .background(Palette.track, in: .capsule)
                    .foregroundStyle(Palette.onTrack)
                    .padding(.top, Space.xxs)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(Space.m)
            .foregroundStyle(Palette.onDone)
            .background(Palette.done, in: .rect(cornerRadius: Radius.card))
        }
        .buttonStyle(.plain)
    }
}

/// The volume of each week, run in ink, in progress in red, deloads and the taper pale;
/// the phases under it.
struct TrameBars: View {
    let weeks: [Notebook.PlanWeek]
    var height: CGFloat = 104

    var body: some View {
        let peak = max(weeks.map(\.volumeKm).max() ?? 1, 1)
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack(alignment: .bottom, spacing: 4) {
                ForEach(weeks, id: \.week) { week in
                    RoundedRectangle(cornerRadius: Radius.mark / 1.5)
                        .fill(color(of: week))
                        .frame(height: max(4, height * week.volumeKm / peak))
                        .frame(maxWidth: .infinity)
                }
            }
            .frame(height: height, alignment: .bottom)
            .accessibilityElement()
            .accessibilityLabel(Text("Volume des \(weeks.count) semaines, jusqu’à \(Numbers.text(peak)) km"))
            phases
        }
    }

    private func color(of week: Notebook.PlanWeek) -> Color {
        if week.state == .current { return Palette.track }
        if week.deload || week.phase == .taper { return Palette.ghost }
        return week.state == .done ? Palette.ink : Palette.faint
    }

    /// Each phase under its weeks, in proportion.
    private var phases: some View {
        let runs = PlanWords.runs(of: weeks)
        return GeometryReader { proxy in
            HStack(spacing: 4) {
                ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                    Text(PlanWords.phase(run.phase)).textRole(.caption).foregroundStyle(Palette.muted)
                        .lineLimit(1).minimumScaleFactor(0.8)
                        .frame(width: max(0, proxy.size.width * CGFloat(run.count) / CGFloat(max(weeks.count, 1)) - 4),
                               alignment: .leading)
                }
            }
        }
        .frame(height: 18)
        .overlay(alignment: .top) { Rectangle().fill(Palette.grid).frame(height: 1).offset(y: -4) }
    }
}

/// The week in progress and the two after it: each opens its written program.
private struct NearWeeks: View {
    let weeks: [Notebook.PlanWeek]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Cette semaine et les deux suivantes")
            ForEach(weeks, id: \.week) { week in
                if week.written {
                    NavigationLink(value: WeekLink(label: week.week)) { PlanWeekLine(week: week, chevron: true) }
                        .buttonStyle(.plain)
                } else {
                    PlanWeekLine(week: week, chevron: false)
                }
                if week.week != weeks.last?.week { Divider() }
            }
        }
    }
}

/// A week on one line: its name and state, its key sessions, its volume and long run.
private struct PlanWeekLine: View {
    let week: Notebook.PlanWeek
    var chevron = false

    var body: some View {
        HStack(spacing: Space.s) {
            RoundedRectangle(cornerRadius: 2).fill(bar).frame(width: 4)
            VStack(alignment: .leading, spacing: 3) {
                Text(PlanWords.headline(of: week)).textRole(.body).bold()
                ForEach(week.keys, id: \.position) { key in
                    Text(PlanWords.key(key)).textRole(.secondary).foregroundStyle(Palette.muted)
                }
                if !week.written && week.state == .upcoming {
                    Text("Programme écrit au bilan du dimanche").textRole(.caption).foregroundStyle(Palette.faint)
                }
            }
            Spacer(minLength: Space.xs)
            VStack(alignment: .trailing, spacing: 0) {
                Text(Numbers.text(week.volumeKm)).figure(.medium, week.state == .done ? .done : .planned)
                Text(week.longRunKm > 0 ? "SL \(Numbers.text(week.longRunKm)) km" : "course")
                    .textRole(.caption).foregroundStyle(Palette.muted)
            }
            if chevron {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .padding(.vertical, Space.s)
        .contentShape(.rect)
    }

    private var bar: Color {
        switch week.state {
        case .current: Palette.track
        case .done: Palette.ink
        default: week.deload || week.phase == .taper ? Palette.ghost : Palette.faint
        }
    }
}

/// Every week of the cycle, phase by phase, a line each.
private struct PhaseWeeks: View {
    let cycle: Notebook.PlanCycle

    var body: some View {
        let runs = PlanWords.runs(of: cycle.weeks)
        VStack(alignment: .leading, spacing: Space.section) {
            ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                let weeks = cycle.weeks.filter { $0.phase == run.phase }
                VStack(alignment: .leading, spacing: 0) {
                    SectionTitle(text: PlanWords.phase(run.phase).capitalized(with: Locale(identifier: "fr_FR")),
                                 meta: PlanWords.span(of: weeks))
                    ForEach(weeks, id: \.week) { week in
                        HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                            Text("S\(Int(week.week.suffix(2)) ?? 0)").textRole(.secondary)
                                .foregroundStyle(Palette.muted).frame(width: 40, alignment: .leading)
                            VStack(alignment: .leading, spacing: 2) {
                                if week.deload {
                                    Text("Décharge").textRole(.secondary).foregroundStyle(Palette.muted)
                                }
                                ForEach(week.keys, id: \.position) { key in
                                    Text(PlanWords.key(key, paces: false)).textRole(.secondary)
                                }
                                if week.keys.isEmpty && !week.deload {
                                    Text("Sans séance clé").textRole(.secondary).foregroundStyle(Palette.muted)
                                }
                            }
                            Spacer(minLength: Space.xs)
                            Text(Numbers.text(week.volumeKm)).figure(.inline, .planned)
                        }
                        .padding(.vertical, Space.xs)
                        .opacity(week.state == .done ? 0.6 : 1)
                        if week.week != weeks.last?.week { Divider() }
                    }
                }
            }
        }
    }
}
