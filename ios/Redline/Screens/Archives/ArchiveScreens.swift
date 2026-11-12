import QuickLook
import RedlineKit
import SwiftUI

/// Every archived card, opened from the Race tab.
struct ArchivesLink: Hashable {}

/// An archived card, opened from a list, a week, or a link received in Messenger.
struct EntryLink: Hashable {
    let id: String
    var title: String?
}

/// The cards, latest first, under the month they belong to; the next page on request.
struct ArchivesScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: "Archives", session: session, model: ScreenModel(.entries()), onTrack: false) { _ in
            Kicker(text: "Bilans, monthly et cartes").foregroundStyle(Palette.muted)
        } content: { page in
            ArchivesList(first: page)
        }
    }
}

private struct ArchivesList: View {
    let first: Notebook.EntriesView
    @Environment(\.notebook) private var session
    @State private var more: [Notebook.Entry] = []
    @State private var next: Int??
    @State private var loading = false
    @State private var failure: String?

    var body: some View {
        // A card listed on both pages, when one arrived in between, is shown once.
        let entries = (first.entries + more).reduce(into: [Notebook.Entry]()) { list, entry in
            if !list.contains(where: { $0.id == entry.id }) { list.append(entry) }
        }.sorted { $0.occurredOn > $1.occurredOn }
        let months = entries.map { String($0.occurredOn.prefix(7)) }.reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
        VStack(alignment: .leading, spacing: 0) {
            ForEach(months, id: \.self) { month in
                Text(Self.monthName(month))
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Palette.muted)
                    .padding(.top, 22)
                    .padding(.bottom, 2)
                ForEach(entries.filter { $0.occurredOn.hasPrefix(month) }, id: \.id) { entry in
                    NavigationLink(value: EntryLink(id: entry.id, title: entry.title)) {
                        EntryRow(title: entry.title, date: entry.occurredOn)
                    }
                    .buttonStyle(.plain)
                }
            }
            if let offset = next ?? first.nextOffset {
                Button {
                    Task { await load(offset) }
                } label: {
                    Text(loading ? "Chargement…" : "Voir plus d’archives").frame(maxWidth: .infinity)
                }
                .buttonStyle(.glass)
                .controlSize(.large)
                .disabled(loading)
                .padding(.top, 20)
            }
            if let failure {
                Text(failure).font(.footnote).foregroundStyle(Palette.over).padding(.top, 8)
            }
        }
        .padding(.bottom, 24)
    }

    private func load(_ offset: Int) async {
        guard case .signedIn(let client)? = session?.state else { return }
        loading = true
        defer { loading = false }
        do {
            let page = try await client.get(.entries(offset: offset))
            more += page.entries
            next = .some(page.nextOffset)
            failure = nil
        } catch {
            failure = NotebookSession.words(for: error)
        }
    }

    /// The month and year, capitalised.
    static func monthName(_ month: String) -> String {
        guard let day = Day(month + "-01") else { return month }
        return day.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).month(.wide).year())
            .capitalizedFirst
    }
}

/// A card in a list: its title, its date, a chevron.
struct EntryRow: View {
    let title: String
    let date: String?
    var pending = false

    var body: some View {
        HStack(spacing: Space.xs) {
            Text(title).textRole(.secondary).foregroundStyle(pending ? Palette.muted : Palette.ink)
            Spacer()
            Text(pending ? "dimanche" : date.map(shortDate) ?? "").textRole(.caption).foregroundStyle(Palette.muted)
            if !pending {
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .padding(.vertical, 12)
        .contentShape(.rect)
    }
}

/// A card opened: its image (touched, it opens in Quick Look, with zoom and the share
/// sheet), the coach's words, and for a strength card its prescription and what was done.
struct EntryScreen: View {
    let session: NotebookSession
    let link: EntryLink

    var body: some View {
        NotebookScreen(title: LocalizedStringKey(link.title ?? "Carte"), session: session, model: ScreenModel(.entry(link.id)),
                       onTrack: false, heading: link.title == nil ? { $0.title } : nil) { entry in
            Kicker(text: Day(entry.occurredOn)?.spelled().capitalizedFirst ?? entry.occurredOn).foregroundStyle(Palette.muted)
        } content: { entry in
            EntryContent(entry: entry)
        }
    }
}

private struct EntryContent: View {
    let entry: Notebook.EntryView

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !entry.imageHash.isEmpty { CardImage(entry: entry) }
            if !entry.body.isEmpty {
                SectionTitle(text: "Le mot du coach")
                Text(entry.body).font(.body).padding(.top, 12).textSelection(.enabled)
            }
            if let snapshot = entry.snapshot {
                let exercises = ExerciseList.exercises(snapshot)
                if !exercises.isEmpty {
                    DisclosureGroup("Consignes de cette séance") {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(exercises.enumerated()), id: \.offset) { index, exercise in
                                HStack(alignment: .firstTextBaseline, spacing: 10) {
                                    Text("\(index + 1)").font(.figure(16)).foregroundStyle(Palette.muted).frame(width: 18)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(exercise.name)
                                        if !exercise.dose.isEmpty {
                                            Text(exercise.dose).font(.subheadline).foregroundStyle(Palette.muted)
                                        }
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 8)
                            }
                        }
                    }
                    .tint(Palette.ink)
                    .padding(.top, 24)
                }
            }
            if let report = entry.report {
                StrengthReportView(report: report, names: Dictionary(
                    ExerciseList.slugsAndNames(entry.snapshot ?? [:]), uniquingKeysWith: { first, _ in first }))
            } else if entry.kind == "strength" {
                Text("Aucun compte rendu rattaché à cette carte.").font(.footnote).foregroundStyle(Palette.muted).padding(.top, 20)
            }
            if entry.kind == "monthly", !entry.related.isEmpty {
                SectionTitle(text: "Les weekly de ce mois")
                ForEach(entry.related, id: \.id) { weekly in
                    NavigationLink(value: EntryLink(id: weekly.id, title: weekly.title)) {
                        EntryRow(title: weekly.title, date: weekly.occurredOn)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.bottom, 24)
    }
}

/// The card's image, kept on the device once loaded. Touched, it opens in Quick Look.
private struct CardImage: View {
    let entry: Notebook.EntryView
    @Environment(\.notebook) private var session
    @State private var file: URL?
    @State private var image: UIImage?
    @State private var failed = false
    @State private var previewed: URL?

    var body: some View {
        VStack(spacing: 12) {
            if let image, let file {
                Button { previewed = file } label: {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .clipShape(.rect(cornerRadius: 14))
                        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Palette.grid, lineWidth: 0.5))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Agrandir la carte \(entry.title)")
                ShareLink(item: file) {
                    Label("Partager la carte", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity)
                }
                .buttonStyle(.glass)
                .controlSize(.large)
            } else if failed {
                ContentUnavailableView("Image indisponible", systemImage: "photo",
                                       description: Text("Tire la page pour réessayer."))
            } else {
                RoundedRectangle(cornerRadius: 14).fill(Palette.panel).aspectRatio(1.13, contentMode: .fit)
                    .overlay { ProgressView() }
            }
        }
        .quickLookPreview($previewed)
        .task(id: entry.imageHash) { await load() }
    }

    /// Quick Look and the share sheet take a file: the image is written under the name
    /// the page gives it when downloaded.
    private func load() async {
        guard case .signedIn(let client)? = session?.state else { return }
        do {
            let data = try await client.cardImage(entry)
            let url = FileManager.default.temporaryDirectory.appending(path: "redline-\(entry.id).png")
            try data.write(to: url, options: .atomic)
            image = UIImage(data: data)
            file = url
            failed = image == nil
        } catch {
            failed = true
        }
    }
}

/// What the athlete reported of a strength session: the day and duration, the note,
/// each exercise's state and repetitions.
private struct StrengthReportView: View {
    let report: Notebook.StrengthReport
    let names: [String: String]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Ce que tu as réalisé")
            Text([Day(report.performedOn)?.spelled(weekday: false), report.durationMinutes.map { "\($0) min" }]
                .compactMap { $0 }.joined(separator: " · "))
                .font(.subheadline).foregroundStyle(Palette.muted).padding(.top, 12)
            if !report.note.isEmpty {
                Text(report.note).padding(.top, 8)
            }
            ForEach(report.exercises, id: \.slug) { exercise in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(names[exercise.slug] ?? exercise.slug).font(.subheadline.weight(.semibold))
                        Spacer()
                        Text(Self.state(exercise.status)).font(.caption.weight(.semibold))
                            .foregroundStyle(exercise.status == "completed" ? Palette.ink : Palette.muted)
                    }
                    let details = [Self.repetitions(exercise.sets), exercise.difficulty.map { "effort \($0)/10" }]
                        .compactMap { $0 }.joined(separator: " · ")
                    if !details.isEmpty { Text(details).font(.caption).foregroundStyle(Palette.muted) }
                    if !exercise.note.isEmpty { Text(exercise.note).font(.footnote) }
                }
                .padding(.vertical, 12)
                .accessibilityElement(children: .combine)
            }
        }
    }

    static func state(_ status: String?) -> String {
        switch status {
        case "completed": "fait"
        case "partial": "partiel"
        case "skipped": "passé"
        case "gated": "retenu"
        default: "non précisé"
        }
    }

    /// « 10 · 10 · 10 rép. », a side's sets marked G or D.
    static func repetitions(_ sets: [Notebook.StrengthSet]) -> String? {
        guard !sets.isEmpty else { return nil }
        return sets.map { set in
            (set.side == .left ? "G " : set.side == .right ? "D " : "") + "\(set.repetitions)"
        }.joined(separator: " · ") + " rép."
    }
}
