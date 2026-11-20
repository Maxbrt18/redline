import RedlineKit
import SwiftUI

/// Today under its card: the coach's note when a question holds the card, the questions
/// left, the rest of the day, the week as a strip, and the landmarks as tiles. Each section
/// is a white surface under a quiet title.
struct TodayContent: View {
    let today: Notebook.TodayView
    let layout: TodayLayout
    let openTab: (NotebookTab) -> Void
    @State private var readingNote = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.section) {
            if ["awaiting", "stale"].contains(today.plan.status) {
                Label(today.plan.message, systemImage: "hourglass")
                    .textRole(.secondary)
                    .foregroundStyle(Palette.muted)
            }
            if !layout.noteInCard, let note = layout.note {
                CardSection(title: "Le mot du coach") {
                    CoachNotePreview(note: note) { readingNote = true }
                        .padding(Space.m)
                        .surface()
                }
            }
            if layout.asks.count > 1 {
                CardSection(title: "À remplir aussi") {
                    VStack(spacing: Space.s) {
                        ForEach(Array(layout.asks.dropFirst().enumerated()), id: \.offset) { _, ask in
                            LeftAsk(ask: ask)
                        }
                    }
                }
            }
            AlsoToday(today: today, layout: layout)
            CardSection(title: "Cette semaine") {
                Button { openTab(.week) } label: { WeekStrip(days: today.strip, totals: today.totals) }
                    .buttonStyle(.plain)
            }
            LandmarkTiles(today: today, openTab: openTab)
        }
        .padding(.bottom, Space.l)
        .sheet(isPresented: $readingNote) {
            if let note = layout.note, let item = layout.item { CoachNoteSheet(title: item.title, note: note) }
        }
    }
}

/// A section of Today: a quiet title above a white surface.
struct CardSection<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Palette.muted)
                .padding(.leading, Space.xxs)
                .accessibilityAddTraits(.isHeader)
            content
        }
    }
}

extension View {
    /// A white surface on the paper.
    func surface() -> some View {
        frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.card, in: .rect(cornerRadius: Radius.card))
    }
}

/// A question left after the card's, in its own surface.
private struct LeftAsk: View {
    let ask: TodayLayout.Ask

    var body: some View {
        Group {
            switch ask {
            case .morning(let wakes, let daily): MorningCard(wakes: wakes, daily: daily)
            case .cells(let cells): DebriefCard(ask: cells)
            case .heart(let hr): HeartCard(ask: hr)
            }
        }
        .environment(\.askInCard, true)
        .padding(Space.m)
        .surface()
    }
}

// MARK: The coach's note

/// The coach's note in two lines, and the way to the whole of it.
struct CoachNotePreview: View {
    let note: String
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(note)
                    .textRole(.body)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .foregroundStyle(Palette.ink)
                Text("Lire la suite").font(.subheadline.weight(.semibold)).foregroundStyle(Palette.accent)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Ouvre le mot du coach en entier")
    }
}

/// The coach's note in full, in a sheet over Today.
struct CoachNoteSheet: View {
    let title: String
    let note: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.m) {
                    Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Palette.muted)
                    Text(note).textRole(.body).lineSpacing(4).textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.gutter)
            }
            .background(Palette.bg)
            .navigationTitle("Le mot du coach")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("OK") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

// MARK: The rest of the day

/// What else the day holds besides the card's session: the strength session, a second run,
/// what was done off the program.
private struct AlsoToday: View {
    let today: Notebook.TodayView
    let layout: TodayLayout

    var body: some View {
        let items = today.sessions.filter { $0.kind != "rest" && $0.id != layout.item?.id }
        let runs = items.filter { !$0.isStrength }
        let strength = items.first { $0.isStrength }
        if strength != nil || !runs.isEmpty || !today.extra.isEmpty {
            CardSection(title: layout.item == nil && layout.asks.isEmpty ? "Aujourd’hui" : "Aussi aujourd’hui") {
                VStack(spacing: Space.s) {
                    if let strength { StrengthCard(item: strength, offer: today.strength) }
                    ForEach(runs, id: \.id) { run in RunRow(title: run.title, fact: run.runFact, planned: run.plannedValue) }
                    ForEach(today.extra, id: \.observationId) { fact in
                        RunRow(title: "\(Notebook.kindName(fact.kind)) hors programme", fact: Notebook.Fact(unplanned: fact),
                               planned: "", family: Family(kind: fact.kind))
                    }
                }
            }
        }
    }
}

/// A run of the day other than the card's: its title, and its distance once done, which
/// leads to its detail.
private struct RunRow: View {
    let title: String
    let fact: Notebook.Fact?
    let planned: String
    var family = Family.run

    var body: some View {
        let row = HStack(spacing: Space.s) {
            IconTile(symbol: family.symbol, done: fact != nil)
            Text(title).textRole(.body).fontWeight(.semibold).lineLimit(2)
            Spacer(minLength: Space.xs)
            Text(fact.flatMap { fact in fact.km.map { "\(Numbers.text($0)) km" } ?? fact.durationMin.map { "\($0) min" } } ?? planned).textRole(.secondary).foregroundStyle(Palette.muted)
            if fact != nil {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Palette.faint)
            }
        }
        .padding(Space.m)
        .surface()
        if let fact {
            NavigationLink(value: SessionLink(id: fact.observationId, title: title.shortTitle)) { row }
                .buttonStyle(.plain)
        } else {
            row
        }
    }
}

/// A square that names what a row is about: its symbol on the panel, or a check on the ink
/// once it is done.
struct IconTile: View {
    let symbol: String
    var done = false
    @ScaledMetric(relativeTo: .body) private var side: CGFloat = 44

    var body: some View {
        Image(systemName: done ? "checkmark" : symbol)
            .font(.body.weight(done ? .bold : .regular))
            .foregroundStyle(done ? Palette.bg : Palette.ink)
            .frame(width: side, height: side)
            .background(done ? Palette.ink : Palette.panel, in: .rect(cornerRadius: Radius.surface))
            .accessibilityHidden(true)
    }
}

// MARK: The week

/// The week in seven boxes: a run done in ink with its distance, today in the red line,
/// what is planned framed in dashes, a day off as a short rule. Under them, the distance
/// done against the week's target.
private struct WeekStrip: View {
    let days: [Notebook.StripDay]
    let totals: Notebook.Totals

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            HStack(spacing: Space.xxs) {
                ForEach(days, id: \.date) { day in
                    VStack(spacing: Space.xs) {
                        Text(day.letter)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(day.today ? Palette.accent : Palette.muted)
                        box(day)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .dynamicTypeSize(...DynamicTypeSize.xxLarge)
            .accessibilityHidden(true)
            HStack(spacing: Space.s) {
                ProgressView(value: min(totals.kmDone, totals.kmTarget ?? totals.kmDone), total: max(totals.kmTarget ?? 1, 0.1))
                    .tint(Palette.ink)
                Text("\(Text(Numbers.text(totals.kmDone)).fontWeight(.bold)) / \(totals.kmTarget.map { Numbers.text($0) } ?? "–") km")
                    .textRole(.secondary)
                    .monospacedDigit()
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Palette.faint)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(accessibility)
        }
        .padding(Space.m)
        .surface()
    }

    private var accessibility: String {
        let target = totals.kmTarget.map { " sur \(Numbers.text($0))" } ?? ""
        return "Cette semaine, \(Numbers.text(totals.kmDone)) km\(target), \(totals.runsDone) sortie\(totals.runsDone > 1 ? "s" : "") sur \(totals.runsPlanned)"
    }

    @ViewBuilder private func box(_ day: Notebook.StripDay) -> some View {
        let shape = RoundedRectangle(cornerRadius: Radius.mark * 2)
        let label = Self.label(day)
        if let done = day.doneKm, done > 0 {
            boxText(Numbers.text(done)).foregroundStyle(Palette.bg).background(Palette.ink, in: shape)
        } else if day.today, !label.isEmpty {
            boxText(label).foregroundStyle(Palette.onTrack).background(Palette.track, in: shape)
        } else if !label.isEmpty {
            boxText(label).foregroundStyle(Palette.ink)
                .overlay(shape.strokeBorder(Palette.faint, style: StrokeStyle(lineWidth: 1.5, dash: [3, 3])))
        } else {
            Capsule().fill(Palette.ghost).frame(width: 10, height: 2).frame(maxWidth: .infinity, minHeight: 30)
        }
    }

    private func boxText(_ text: String) -> some View {
        Text(text)
            .font(.figure(16, weight: .bold, relativeTo: .footnote))
            .monospacedDigit()
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            .padding(.horizontal, 2)
            .frame(maxWidth: .infinity, minHeight: 30)
    }

    private static func label(_ day: Notebook.StripDay) -> String {
        if let planned = day.plannedKm { return Numbers.text(planned) }
        return day.plannedLabel?.replacingOccurrences(of: "≤", with: "") ?? ""
    }
}

// MARK: The landmarks

/// The landmarks as tiles: each watched zone against its ceiling, then the race. Each
/// leads to its screen.
private struct LandmarkTiles: View {
    let today: Notebook.TodayView
    let openTab: (NotebookTab) -> Void

    var body: some View {
        if !today.pain.zones.isEmpty || today.race != nil {
            CardSection(title: "Repères") {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: Space.s), GridItem(.flexible(), spacing: Space.s)],
                          spacing: Space.s) {
                    ForEach(today.pain.zones, id: \.zone) { zone in
                        NavigationLink(value: PainLink(zone: zone.zone, label: zone.label)) { pain(zone) }
                            .buttonStyle(.plain)
                    }
                    if let race = today.race {
                        Button { openTab(.race) } label: { self.race(race) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func pain(_ zone: Notebook.PainTrendZone) -> some View {
        let peak = zone.weeks.last?.peak
        let over = (peak ?? 0) > today.pain.cap
        return Tile(label: zone.label,
                    figure: peak.map { Numbers.text($0) } ?? "–", unit: peak == nil ? nil : "/10 au pic",
                    words: peak == nil ? "rien noté cette semaine" : "plafond \(Numbers.text(today.pain.cap))",
                    alert: over)
    }

    private func race(_ race: Notebook.Race) -> some View {
        let date = Day(race.date)?.spelled(weekday: false)
        let words = [race.distanceKm.map { "\(Numbers.text($0)) km" }, date.map { "le \($0)" }].compactMap { $0 }.joined(separator: ", ")
        return Tile(label: race.name, figure: "J-\(race.days)", unit: nil, words: words, alert: true)
    }
}

/// One landmark: its name, its figure, a few words.
private struct Tile: View {
    let label: String
    let figure: String
    let unit: String?
    let words: String
    var alert = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            HStack {
                Text(label).font(.footnote.weight(.semibold)).foregroundStyle(Palette.muted).lineLimit(1)
                Spacer(minLength: Space.xxs)
                Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(Palette.faint)
            }
            HStack(alignment: .lastTextBaseline, spacing: Space.xxs) {
                Text(figure).figure(.large).foregroundStyle(alert ? Palette.accent : Palette.ink)
                if let unit { Text(unit).textRole(.caption).foregroundStyle(Palette.muted) }
            }
            Text(words).textRole(.caption).foregroundStyle(Palette.muted).lineLimit(2)
        }
        .padding(Space.m)
        .frame(maxHeight: .infinity, alignment: .topLeading)
        .surface()
        .accessibilityElement(children: .combine)
    }
}
