import RedlineKit
import SwiftUI

/// The end of the session: each exercise's difficulty, the pain after, the duration, a word
/// for the coach, and the sending. Once sent, nothing changes; a correction goes through
/// Messenger.
struct StrengthReview: View {
    let model: StrengthModel
    let run: StrengthRun
    @State private var correction: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Séance terminée").font(.title2.weight(.bold))
            // Not a performance mark and not a pain score: how hard the exercise was to do properly.
            Text("Pour chaque exercice : à quel point c’était dur de le faire correctement ? 0 = facile, 10 = trop dur ou ça tirait. Sous 6 on durcit la semaine prochaine, 6-7 on garde le palier, au-dessus de 7 on redescend d’un cran.")
                .font(.footnote).foregroundStyle(Palette.muted)
            VStack(alignment: .leading, spacing: 18) { inputs }
                .disabled(run.isLocked)
            actions
            if let status = model.sentStatus {
                Text(status).font(.subheadline.weight(.semibold))
            }
            if let correction {
                Text("Complète puis colle ce texte dans Messenger pour le transmettre au coach. Il est copié.")
                    .font(.footnote).foregroundStyle(Palette.muted)
                Text(correction).font(.footnote).textSelection(.enabled)
            }
        }
        .padding(.top, 8)
    }

    @ViewBuilder private var inputs: some View {
        ForEach(run.reviewed, id: \.self) { index in
            VStack(alignment: .leading, spacing: 6) {
                Text(run.exercises[index].name).font(.subheadline.weight(.semibold))
                ExerciseFeedback(model: model, run: run, index: index)
            }
        }
        ForEach(run.zones, id: \.zone) { zone in
            VStack(alignment: .leading, spacing: 6) {
                Text("\(zone.label), maintenant").font(.subheadline.weight(.semibold))
                ScoreScale(value: run.pain(zone.zone, .after)) { value in
                    model.update { $0.setPain(zone.zone, .after, value) }
                }
            }
        }
        VStack(alignment: .leading, spacing: 6) {
            Text("Durée (min)").font(.subheadline.weight(.semibold))
            TextField("min", value: Binding(
                get: { run.progress.minutes }, set: { value in model.update { $0.setMinutes(value) } }), format: .number)
                .keyboardType(.numberPad)
                .font(.title3.weight(.semibold))
                .monospacedDigit()
                .padding(10)
                .background(Palette.panel, in: .rect(cornerRadius: 10))
        }
        VStack(alignment: .leading, spacing: 6) {
            Text("À dire au coach (facultatif)").font(.subheadline.weight(.semibold))
            TextField("", text: Binding(
                get: { run.progress.note }, set: { text in model.update { $0.setNote(text) } }), axis: .vertical)
                .lineLimit(3...8)
                .padding(10)
                .background(Palette.panel, in: .rect(cornerRadius: 10))
        }
    }

    private var isSent: Bool {
        if case .sent = run.progress.delivery { return true }
        return false
    }

    @ViewBuilder private var actions: some View {
        if isSent {
            Button {
                let text = "Correction du compte rendu \(run.session.title) (\(run.progress.performedOn ?? run.session.date)) : "
                UIPasteboard.general.string = text
                correction = text
            } label: {
                Text("Ajouter une correction").frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.bordered)
            .tint(Palette.ink)
        } else {
            Button {
                Task { await model.send() }
            } label: {
                Group {
                    if model.sending { ProgressView() } else { Text("Envoyer au coach").font(.body.weight(.semibold)) }
                }
                .frame(maxWidth: .infinity, minHeight: 34)
            }
            .buttonStyle(.borderedProminent)
            .tint(Palette.ink)
            .foregroundStyle(Palette.bg)
            .disabled(model.sending || run.isLocked)
            if run.canUndo {
                Button("Annuler la dernière validation") { model.update { $0.undo() } }
                    .font(.subheadline).tint(Palette.muted).buttonStyle(.borderless)
            }
        }
    }
}
