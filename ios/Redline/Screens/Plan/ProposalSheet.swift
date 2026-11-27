import RedlineKit
import SwiftUI

/// The preparation proposed, whole: three figures and its trame, what changes against the
/// cycle in force, the candidates the coach set aside, then the take. Only the athlete takes
/// it; leaving the sheet changes nothing.
struct ProposalSheet: View {
    let session: NotebookSession
    /// Loads the Plan screen again once the proposal is taken.
    let taken: @MainActor () async -> Void
    @State private var model = ScreenModel(Endpoint<Notebook.CycleProposalView>.planProposal)
    @State private var taking = false
    @State private var failure: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                if let proposal = model.value {
                    content(proposal).padding(.horizontal, Space.gutter).padding(.bottom, Space.xl)
                } else if let failure = model.failure {
                    ContentUnavailableView("Proposition indisponible", systemImage: "map",
                                           description: Text(failure))
                } else {
                    ProgressView().padding(.top, Space.xl)
                }
            }
            .background(Palette.bg)
            .navigationTitle("Proposition")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Fermer", systemImage: "xmark") { dismiss() }
                }
            }
        }
        .task { await model.load(with: session) }
    }

    @ViewBuilder private func content(_ proposal: Notebook.CycleProposalView) -> some View {
        VStack(alignment: .leading, spacing: Space.section) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text("Préparation").textRole(.title)
                if let race = proposal.race {
                    Text(PlanWords.race(race)).textRole(.body)
                }
                Text("Candidat \(PlanWords.candidate(proposal.candidate)), proposé le \(PlanWords.day(proposal.proposedOn))")
                    .textRole(.secondary).foregroundStyle(Palette.muted)
            }
            .padding(.top, Space.m)
            VStack(alignment: .leading, spacing: Space.m) {
                HStack(alignment: .top) {
                    figure(proposal.weeks.count, "semaines")
                    figure(proposal.peakKm, "km au pic")
                    figure(proposal.longRunKm, "km de SL au plus")
                }
                TrameBars(weeks: proposal.weeks, height: 84)
            }
            .padding(Space.m)
            .background(Palette.card, in: .rect(cornerRadius: Radius.card))
            changes(proposal)
            if !proposal.alternatives.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    SectionTitle(text: "Écartés par ton coach")
                    ForEach(Array(proposal.alternatives.enumerated()), id: \.offset) { _, alternative in
                        Text("\(PlanWords.candidate(alternative.option).capitalized(with: Locale(identifier: "fr_FR"))) : \(alternative.rejectedBecause)")
                            .textRole(.secondary)
                    }
                }
            }
            if !proposal.risks.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    Text("À surveiller").textRole(.body).bold()
                    ForEach(Array(proposal.risks.enumerated()), id: \.offset) { _, risk in
                        Text(PlanWords.risk(risk)).textRole(.secondary)
                    }
                }
                .padding(Space.m)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
            }
            VStack(spacing: Space.s) {
                Button {
                    Task { await take(proposal) }
                } label: {
                    Text(taking ? "Un instant…" : "Je la prends").bold().frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent).tint(Palette.track).buttonBorderShape(.capsule)
                .disabled(taking)
                Button("Pas maintenant") { dismiss() }.frame(minHeight: 44).foregroundStyle(Palette.ink)
                if let failure {
                    Text(failure).textRole(.secondary).foregroundStyle(Palette.accent)
                }
                Text("Elle t’attend jusqu’au \(PlanWords.day(proposal.expiresOn)) ; sans réponse, rien ne change.")
                    .textRole(.caption).foregroundStyle(Palette.muted).multilineTextAlignment(.center)
            }
        }
    }

    private func figure(_ value: Double, _ unit: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(Numbers.text(value)).figure(.large)
            Text(unit).textRole(.caption).foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func figure(_ value: Int, _ unit: String) -> some View {
        figure(Double(value), unit)
    }

    @ViewBuilder private func changes(_ proposal: Notebook.CycleProposalView) -> some View {
        let current = proposal.current
        let qualities = proposal.weeks.map(\.qualities).max() ?? 0
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Ce qui change")
            row("Cycle", "\(current.kind.map(PlanWords.kind) ?? "Aucun") → Préparation")
            Divider()
            row("Volume au plus",
                "\(current.volumeKm.map { Numbers.text($0) } ?? "—") → \(Numbers.text(proposal.peakKm))\u{00a0}km par semaine")
            Divider()
            row("Qualités", "\(current.qualities.map(String.init) ?? "—") → \(qualities) par semaine au plus")
            if proposal.flagships > 0 {
                Divider()
                row("Séances phares", "\(proposal.flagships)")
            }
        }
    }

    private func row(_ name: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(name).textRole(.body).foregroundStyle(Palette.muted)
            Spacer(minLength: Space.s)
            Text(value).textRole(.body).multilineTextAlignment(.trailing)
        }
        .padding(.vertical, Space.s)
    }

    private func take(_ proposal: Notebook.CycleProposalView) async {
        guard case .signedIn(let client) = session.state else { return }
        taking = true
        defer { taking = false }
        do {
            try await client.takeCycleProposal(proposal.id)
            await taken()
            dismiss()
        } catch .signedOut(let message) {
            session.forget(saying: message.isEmpty ? nil : message)
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }
}
