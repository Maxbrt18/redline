import RedlineKit
import SwiftUI

/// What the day's card tells, read once from Today's view: the moment of the day, the
/// questions in the order they are asked, and whether the coach's note fits in the card.
struct TodayLayout {
    enum Moment {
        /// A session waits today.
        case planned(Notebook.SessionItem)
        /// Today's run is done: the card becomes its verdict.
        case done(Notebook.SessionItem, Notebook.Fact)
        /// Nothing waits today; `finished` when what was planned is already done.
        case rest(next: Notebook.SessionItem?, finished: Bool)
    }

    /// A question of the day, as the card's panel asks it.
    enum Ask {
        case morning([Notebook.WakeAsk], Notebook.DailyAsk?)
        case cells(Notebook.CellsAsk)
        case heart(Notebook.HrAsk)
        case match(Notebook.MatchAsk)
    }

    let moment: Moment
    /// The morning first, then which session an activity was, the debriefs (today's run
    /// before an older one), then the heart.
    let asks: [Ask]
    /// The run done today, whose debrief closes the card.
    let ownRun: String?

    init(_ today: Notebook.TodayView) {
        let items = today.sessions.filter { $0.kind != "rest" }
        let ran = items.last { !$0.isStrength && $0.isDone && $0.runFact != nil }
        ownRun = ran?.runFact?.observationId
        if let main = items.first(where: { !$0.isStrength && !$0.isDone }) {
            moment = .planned(main)
        } else if let ran, let fact = ran.runFact {
            moment = .done(ran, fact)
        } else if let strength = items.first(where: { $0.isStrength && !$0.isDone }) {
            moment = .planned(strength)
        } else {
            moment = .rest(next: Self.next(today), finished: !items.isEmpty)
        }
        let wakes = today.asks.compactMap { if case .wake(let wake) = $0 { wake } else { nil } }
        let daily = today.asks.lazy.compactMap { if case .daily(let daily) = $0 { daily } else { nil } }.first
        let own = ownRun.map { "cell:\($0)" }
        let cells = today.asks.compactMap { if case .cells(let cells) = $0 { cells } else { nil } }
            .sorted { ($0.id == own ? 0 : 1) < ($1.id == own ? 0 : 1) }
        let hearts = today.asks.compactMap { if case .hr(let hr) = $0 { hr } else { nil } }
        let matches = today.asks.compactMap { if case .match(let match) = $0 { match } else { nil } }
        asks = (wakes.isEmpty && daily == nil ? [] : [.morning(wakes, daily)]) + matches.map(Ask.match)
            + cells.map(Ask.cells) + hearts.map(Ask.heart)
    }

    /// The session the card is about, when there is one today.
    var item: Notebook.SessionItem? {
        switch moment {
        case .planned(let item), .done(let item, _): item
        case .rest: nil
        }
    }

    var tone: CardTone {
        switch moment {
        case .planned: .track
        case .done: .done
        case .rest: .paper
        }
    }

    /// The coach's note for the day's session, shown in the card when no question takes
    /// its place, below the card otherwise.
    var note: String? {
        guard case .planned(let item) = moment, item.status != "held" else { return nil }
        return item.note
    }

    var noteInCard: Bool { asks.isEmpty && note != nil }

    /// The next session after today: tomorrow's run first, then the next day that holds one.
    private static func next(_ today: Notebook.TodayView) -> Notebook.SessionItem? {
        let later = (today.tomorrow.sessions + today.tomorrow.next + today.ahead)
            .filter { $0.date > today.date && $0.kind != "rest" && !$0.isDone }
        return later.first { !$0.isStrength } ?? later.first
    }
}

/// The day's card, at the head of Today and across its width: red while a session waits,
/// ink once it is done, paper on a day off. Its shape stays the same in every moment: the
/// date and the title, a few words, the figure that matters, the lanes, then a panel that
/// holds the one thing to do now. The lanes are decoration before the session and become
/// its limits once it is done.
struct DayCard: View {
    let today: Notebook.TodayView
    let layout: TodayLayout
    @State private var readingNote = false

    var body: some View {
        let tone = layout.tone
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                Text(Day(today.date)?.spelled().capitalizedFirst ?? today.date)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(tone.muted)
                PageTitle(title: NotebookTab.today.title)
                moment
                    .padding(.top, Space.l)
            }
            .padding(.horizontal, Space.gutter)
            .padding(.top, Space.xxs)
            foot
            panel
        }
        .foregroundStyle(tone.ink)
        .tint(tone.ink)
        .background(alignment: .bottom) {
            // The surface reaches far above the card, under the bar and into the bounce.
            UnevenRoundedRectangle(bottomLeadingRadius: Radius.dayCard, bottomTrailingRadius: Radius.dayCard)
                .fill(tone.surface)
                .padding(.top, -800)
        }
        .cardBar(bar)
        .sheet(isPresented: $readingNote) {
            if let note = layout.note, let item = layout.item { CoachNoteSheet(title: item.title, note: note) }
        }
    }

    // MARK: The moment

    @ViewBuilder private var moment: some View {
        switch layout.moment {
        case .planned(let item): planned(item)
        case .done(let item, let fact): done(item, fact)
        case .rest(_, let finished): rest(finished: finished)
        }
    }

    private func planned(_ item: Notebook.SessionItem) -> some View {
        let time = item.startTime.flatMap(Numbers.figureClock).map { "À \($0)" }
        let kicker = [time, item.location].compactMap { $0 }.joined(separator: ", ")
        return VStack(alignment: .leading, spacing: Space.xxs) {
            Text(kicker.isEmpty ? "Au programme" : kicker)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(layout.tone.muted)
            Text(item.title).textRole(.title)
            HeroFigure(figure: plannedFigure(item), lines: plannedLines(item))
            if item.status == "held" {
                Label("Une question t’attend dans Messenger avant cette séance.", systemImage: "bubble.left")
                    .textRole(.secondary)
                    .padding(.top, Space.xs)
            } else if !item.isStrength {
                CardWeather(item: item).padding(.top, Space.s)
            }
        }
    }

    private func done(_ item: Notebook.SessionItem, _ fact: Notebook.Fact) -> some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            Text(fact.startedAt.flatMap(Numbers.figureClock).map { "Fait à \($0)" } ?? "Fait")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(layout.tone.muted)
            Text(item.title).textRole(.title)
            HeroFigure(figure: fact.km.map { Numbers.text($0, digits: 2) } ?? "–",
                       lines: ["km", fact.durationMin.map { "\($0)′" }, fact.pace.map { "\($0) /km" }].compactMap { $0 })
        }
    }

    private func rest(finished: Bool) -> some View {
        let invalid = today.plan.status == "invalid"
        return VStack(alignment: .leading, spacing: Space.xxs) {
            Text(finished ? "Fait" : "Repos")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(layout.tone.muted)
            Text(invalid ? "Programme en préparation" : finished ? "Tout est fait pour aujourd’hui" : "Rien à faire aujourd’hui")
                .textRole(.title)
            if invalid {
                Text(today.plan.message).textRole(.secondary).foregroundStyle(layout.tone.muted)
            }
        }
    }

    /// The planned session's one figure: its duration, else its distance; the exercises of
    /// a strength session.
    private func plannedFigure(_ item: Notebook.SessionItem) -> String {
        if item.isStrength {
            if case .ready(let ready) = today.strength { return "\(ready.session.exercises.count)" }
            return "–"
        }
        if let minutes = item.targets.durationCapMin { return "\(minutes)′" }
        if let km = item.targets.distanceTargetKm ?? item.targets.distanceCapKm { return Numbers.text(km) }
        return "–"
    }

    /// What reads beside the figure: its unit, then the other limits.
    private func plannedLines(_ item: Notebook.SessionItem) -> [String] {
        if item.isStrength { return ["exercices", "séance guidée"] }
        let targets = item.targets
        let unit: String? = if targets.durationCapMin != nil { "au plus" }
            else if targets.distanceTargetKm != nil { "km" }
            else if targets.distanceCapKm != nil { "km au plus" }
            else { nil }
        return [unit, targets.hrCapBpm.map { "FC ≤ \($0)" }, targets.rpeRange.map { "Effort \($0)" }].compactMap { $0 }
    }

    // MARK: The foot

    @ViewBuilder private var foot: some View {
        if case .done(let item, let fact) = layout.moment {
            VStack(alignment: .leading, spacing: 0) {
                if let rows = LimitRows(targets: item.targets, fact: fact)?.rows {
                    GaugeLanes(rows: rows).padding(.top, Space.l)
                } else {
                    Lanes(color: layout.tone.ink).padding(.top, Space.l)
                }
                NavigationLink(value: SessionLink(id: fact.observationId, title: item.title.shortTitle)) {
                    HStack {
                        Text("Voir le bilan de la sortie").font(.body.weight(.semibold))
                        Spacer(minLength: Space.xs)
                        Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
                    }
                    .padding(.horizontal, Space.gutter)
                    .frame(minHeight: 52)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
            }
        } else {
            Lanes(color: layout.tone == .paper ? Palette.faint : layout.tone.ink)
                .padding(.top, Space.l)
                .padding(.bottom, panelShown ? 0 : Space.xs)
        }
    }

    // MARK: The panel

    private var panelShown: Bool {
        if !layout.asks.isEmpty || layout.noteInCard { return true }
        if case .rest(let next, _) = layout.moment { return next != nil }
        return false
    }

    @ViewBuilder private var panel: some View {
        if let ask = layout.asks.first {
            DayPanel(kicker: kicker(ask), aside: aside(ask)) {
                PanelAsk(ask: ask, targets: layout.item?.targets)
            }
        } else if layout.noteInCard, let note = layout.note {
            DayPanel(kicker: "Le mot du coach", aside: nil) {
                CoachNotePreview(note: note) { readingNote = true }
            }
        } else if case .rest(let next?, _) = layout.moment {
            NextSession(item: next, tomorrow: today.tomorrow.date)
                .padding([.horizontal, .bottom], Space.xs)
        }
    }

    private func kicker(_ ask: TodayLayout.Ask) -> String {
        switch ask {
        case .morning: return "Ce matin"
        case .cells(let cells) where cells.id == layout.ownRun.map({ "cell:\($0)" }): return "Pour finir"
        case .heart where layout.ownRun != nil: return "Pour finir"
        default:
            if case .planned = layout.moment { return "Avant de partir" }
            return "À compléter"
        }
    }

    /// The earlier run a question is about, beside the kicker.
    private func aside(_ ask: TodayLayout.Ask) -> String? {
        switch ask {
        case .cells(let cells) where cells.id != layout.ownRun.map({ "cell:\($0)" }):
            let day = Day(cells.date)?.weekdayAndNumber ?? cells.date
            return "\(Notebook.kindName(cells.kind)) de \(day)"
        case .heart(let hr) where hr.date != today.date:
            return "EF de \(Day(hr.date)?.weekdayAndNumber ?? hr.date)"
        case .match(let match) where match.date != today.date:
            return "Activité de \(Day(match.date)?.weekdayAndNumber ?? match.date)"
        default:
            return nil
        }
    }

    // MARK: The bar

    private var bar: CardBar {
        let badge = layout.asks.isEmpty ? nil : layout.asks.count > 1 ? "\(layout.asks.count) questions" : "1 question"
        switch layout.moment {
        case .planned(let item):
            let time = item.startTime.flatMap(Numbers.figureClock)
            let title = badge == nil ? [item.title.shortTitle, time.map { "à \($0)" }].compactMap { $0 }.joined(separator: " ")
                : item.title.shortTitle
            return CardBar(tone: .track, figure: plannedFigure(item), title: title, badge: badge)
        case .done(let item, let fact):
            return CardBar(tone: .done, figure: fact.km.map { Numbers.text($0, digits: 1) },
                           title: "\(item.title.shortTitle) faite", badge: badge)
        case .rest(_, let finished):
            return CardBar(tone: .paper, figure: nil, title: finished ? "Tout est fait" : "Repos", badge: badge)
        }
    }
}

/// The day's one figure, as large as the screen allows, with its words in a column beside it.
private struct HeroFigure: View {
    let figure: String
    let lines: [String]

    var body: some View {
        HStack(alignment: .lastTextBaseline, spacing: Space.m) {
            Text(figure).figure(.display)
            VStack(alignment: .leading, spacing: 2) {
                ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                    Text(line).font(index == 0 ? .subheadline : .subheadline.weight(.semibold))
                }
            }
            .padding(.bottom, Space.s)
            .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The panel at the foot of the card: white, concentric with it, and holding the one thing
/// to do now under a kicker in the red line.
private struct DayPanel<Content: View>: View {
    let kicker: String
    let aside: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            HStack(alignment: .firstTextBaseline) {
                Text(kicker).font(.footnote.weight(.semibold)).foregroundStyle(Palette.accent)
                Spacer(minLength: Space.xs)
                if let aside { Text(aside).font(.footnote).foregroundStyle(Palette.muted) }
            }
            content
        }
        .foregroundStyle(Palette.ink)
        .tint(Palette.accent)
        .environment(\.askInCard, true)
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.card, in: .rect(cornerRadius: Radius.dayPanel))
        .padding([.horizontal, .bottom], Space.xs)
    }
}

/// The question the panel asks, in its own card.
private struct PanelAsk: View {
    let ask: TodayLayout.Ask
    let targets: Notebook.Targets?

    var body: some View {
        switch ask {
        case .morning(let wakes, let daily): MorningCard(wakes: wakes, daily: daily)
        case .cells(let cells): DebriefCard(ask: cells, targets: targets)
        case .heart(let hr): HeartCard(ask: hr)
        case .match(let match): MatchCard(ask: match)
        }
    }
}

/// The next session on a day off: the red moves to it.
private struct NextSession: View {
    let item: Notebook.SessionItem
    let tomorrow: String

    var body: some View {
        let day = item.date == tomorrow ? "Demain" : Day(item.date)?.weekdayAndNumber.capitalizedFirst ?? item.date
        let kicker = [day, item.startTime.flatMap(Numbers.figureClock).map { "départ \($0)" }].compactMap { $0 }.joined(separator: ", ")
        HStack(alignment: .lastTextBaseline, spacing: Space.s) {
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(kicker).font(.footnote.weight(.semibold)).opacity(0.85)
                Text(item.title).font(.title3.bold())
                if let cap = item.targets.hrCapBpm { Text("FC ≤ \(cap)").textRole(.secondary) }
            }
            Spacer(minLength: Space.xs)
            Text(item.plannedValue).figure(.large)
        }
        .foregroundStyle(Palette.onTrack)
        .padding(Space.m)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.track, in: .rect(cornerRadius: Radius.dayPanel))
        .accessibilityElement(children: .combine)
    }
}

/// A done run's limits on the card's lanes: one lane per limit, filled with what was done,
/// the limit as a tick, and what passes it in the alert colour after a symbol that says so.
private struct GaugeLanes: View {
    let rows: [LimitRows.Row]
    private static let markAt = 0.75
    /// The thickness of a lane's rail and of what fills it.
    private static let bar: CGFloat = 8
    @ScaledMetric(relativeTo: .footnote) private var labelWidth: CGFloat = 64
    @ScaledMetric(relativeTo: .title3) private var valueWidth: CGFloat = 76

    var body: some View {
        VStack(spacing: 0) {
            line
            ForEach(rows) { row in
                HStack(spacing: Space.s) {
                    Text(row.label).font(.footnote.weight(.semibold))
                        .foregroundStyle(row.crossed ? Palette.onDoneAlert : Palette.onDone.opacity(0.75))
                        .frame(width: labelWidth, alignment: .leading)
                    rail(row).frame(height: Self.bar * 2)
                    HStack(alignment: .firstTextBaseline, spacing: Space.xxs) {
                        if row.crossed {
                            Image(systemName: "arrow.up.right").font(.caption.weight(.bold))
                                .accessibilityLabel("au-delà de la limite")
                        }
                        Text(row.value).figure(.medium)
                    }
                    .foregroundStyle(row.crossed ? Palette.onDoneAlert : Palette.onDone)
                    .frame(width: valueWidth, alignment: .trailing)
                }
                .padding(.leading, Space.gutter)
                .padding(.trailing, Space.gutter)
                .frame(minHeight: 44)
                .accessibilityElement(children: .combine)
                .accessibilityValue(row.sub)
                line
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
    }

    private var line: some View {
        Rectangle().fill(Palette.onDone.opacity(0.22)).frame(height: 1.5)
    }

    private func rail(_ row: LimitRows.Row) -> some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let high = row.low + (row.mark - row.low) / Self.markAt
            let x = { (value: Double) in width * min(max((value - row.low) / (high - row.low), 0), 1) }
            let mark = width * Self.markAt
            let bar = Self.bar, round = Self.bar / 2
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.onDone.opacity(0.14)).frame(height: bar)
                if let band = row.band {
                    Capsule().strokeBorder(Palette.onDone.opacity(0.6), lineWidth: 1.5)
                        .frame(width: max(mark - x(band), 0), height: bar * 2)
                        .offset(x: x(band))
                }
                // One bar that runs on past the limit: square where the alert takes over, so the
                // two parts read as a single fill with the rail's own rounding at both ends.
                if let fill = row.fill, row.crossed {
                    UnevenRoundedRectangle(topLeadingRadius: round, bottomLeadingRadius: round)
                        .fill(Palette.onDone)
                        .frame(width: mark, height: bar)
                    UnevenRoundedRectangle(bottomTrailingRadius: round, topTrailingRadius: round)
                        .fill(Palette.onDoneAlert)
                        .frame(width: max(x(fill) - mark, 0), height: bar)
                        .offset(x: mark)
                } else if let fill = row.fill {
                    Capsule().fill(Palette.onDone).frame(width: x(fill), height: bar)
                }
                Rectangle().fill(Palette.onDone.opacity(0.85)).frame(width: 2, height: 18).offset(x: mark - 1)
            }
            .frame(width: width, height: bar * 2)
        }
    }
}

/// While Today loads: the red card with its title, the rest held in place.
struct DayCardSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            Text("Vendredi 9 octobre").font(.footnote.weight(.semibold)).redacted(reason: .placeholder)
            PageTitle(title: NotebookTab.today.title)
            Text("EF, permis accompagné").textRole(.title).redacted(reason: .placeholder).padding(.top, Space.l)
            Text("55′").figure(.display).redacted(reason: .placeholder)
        }
        .padding(.horizontal, Space.gutter)
        .padding(.top, Space.xxs)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, Space.l)
        .foregroundStyle(Palette.onTrack)
        .background(alignment: .bottom) {
            UnevenRoundedRectangle(bottomLeadingRadius: Radius.dayCard, bottomTrailingRadius: Radius.dayCard)
                .fill(Palette.track)
                .padding(.top, -800)
        }
        .cardBar(CardBar(tone: .track, title: ""))
    }
}
