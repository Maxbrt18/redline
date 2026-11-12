import RedlineKit
import SwiftUI

/// The race plan, on paper: a pace to explore, the profile, the passages with their band
/// and the weather at each, the climbs, the course's figures.
struct RacePlanScreen: View {
    let session: NotebookSession

    var body: some View {
        NotebookScreen(title: "Plan de course", session: session, model: ScreenModel(.racePlan), onTrack: false) { view in
            Kicker(text: [Numbers.text(view.plan.lengthKm) + " km", "D+ \(Int(view.plan.dplusM)) m",
                          view.plan.start.flatMap(Numbers.clock).map { "départ \($0)" }].compactMap { $0 }.joined(separator: " · "))
        } content: { view in
            RacePlanContent(view: view)
        }
    }
}

private struct RacePlanContent: View {
    let view: Notebook.RacePlanView
    /// A pace being explored: it retimes everything here and records nothing.
    @State private var explored: Double?

    private var plan: Notebook.RacePlan { view.plan }
    private var pace: Double? { explored ?? plan.paceSPerKm }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.section) {
            VStack(alignment: .leading, spacing: 0) {
                if let pace {
                    PaceStepper(pace: pace) { explored = $0 }
                    Text(paceNote(pace)).textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
                } else {
                    Text("Allure à choisir avec le coach : les passages s’afficheront ensuite.")
                        .textRole(.caption).foregroundStyle(Palette.muted)
                }
            }
            VStack(alignment: .leading, spacing: 0) {
                SectionTitle(text: "Profil", meta: plan.source == .ign ? "altitude IGN" : "altitude du GPX")
                RaceProfile(plan: plan, full: true).padding(.top, Space.s)
            }
            if let pace { passages(pace) }
            forecastNote
            if !plan.climbs.isEmpty { ClimbList(plan: plan, pace: pace) }
            VStack(alignment: .leading, spacing: 0) {
                SectionTitle(text: "Parcours")
                Text(courseFacts).textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
            }
        }
        .padding(.bottom, Space.l)
    }

    private func paceNote(_ pace: Double) -> String {
        guard let chosen = plan.paceSPerKm, pace != chosen else {
            return "Allure retenue avec le coach. Le − / + explore sans rien enregistrer."
        }
        return "Allure retenue avec le coach : \(Numbers.pace(chosen))/km. Ici, tu explores : rien n’est enregistré."
    }

    /// In large, the time on the watch since the start; the time of day comes after
    /// « vers », so a passage never reads as two clocks.
    private func passages(_ pace: Double) -> some View {
        let timed = plan.passages(pace: pace)
        return VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Passages", meta: plan.start.flatMap(Numbers.clock).map { "temps de course · départ \($0)" } ?? "temps de course")
            ForEach(Array(timed.enumerated()), id: \.offset) { index, passage in
                PassageRow(plan: plan, passage: passage, hour: hour(of: passage, at: index))
            }
            if let finish = timed.last {
                Text("Arrivée 9 fois sur 10 entre \(Numbers.hours(finish.nineInTen.lowerBound)) et \(Numbers.hours(finish.nineInTen.upperBound)) de course. Nuit, fatigue et terrain technique ne sont pas modélisés.")
                    .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
            }
        }
    }

    /// Before the forecast reaches the race day, when it will.
    @ViewBuilder private var forecastNote: some View {
        if pace != nil, let start = plan.start, view.weather == nil, let opens = Day(String(start.prefix(10)))?.adding(-15) {
            VStack(alignment: .leading, spacing: 0) {
                SectionTitle(text: "Météo aux passages")
                Text("Prévision disponible vers le \(opens.spelled(weekday: false)) : ressenti, ciel et vent à chaque ravito, à l’heure du passage.")
                    .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
            }
        }
    }

    private func hour(of passage: Notebook.RacePlan.TimedPassage, at index: Int) -> Notebook.PassageHour? {
        guard let weather = view.weather, index < weather.count, let key = plan.forecastHour(after: passage.hours) else { return nil }
        return weather[index].first { $0.time == key }
    }

    private var courseFacts: String {
        let band = plan.dplusBandM.flatMap { $0.count == 2 ? " (\(Int($0[0])) – \(Int($0[1])))" : nil } ?? ""
        var facts = ["\(Numbers.text(plan.lengthKm)) km · D+ \(Int(plan.dplusM)) m\(band) · D− \(Int(plan.dminusM)) m."]
        if let carbs = plan.carbsGH { facts.append("Glucides : \(carbs)\u{00a0}g/h de course.") }
        if plan.stopMinutes > 0 { facts.append("Arrêts : \(Numbers.text(plan.stopMinutes))\u{00a0}min par ravito.") }
        return facts.joined(separator: " ")
    }
}

/// The pace explored, five seconds at a time, between two glass buttons; at the
/// accessibility sizes the figure stands above the two buttons.
private struct PaceStepper: View {
    let pace: Double
    let change: (Double) -> Void
    private let step = 5.0
    @Environment(\.dynamicTypeSize) private var typeSize
    /// A button's side: never under the 44 pt of a tap target, growing a little with the
    /// text size.
    @ScaledMetric(relativeTo: .title3) private var side: CGFloat = 44

    var body: some View {
        Group {
            if typeSize.isAccessibilitySize {
                VStack(spacing: Space.s) {
                    figure
                    HStack {
                        slower
                        Spacer(minLength: Space.m)
                        faster
                    }
                }
            } else {
                HStack(spacing: Space.s) {
                    slower
                    figure.frame(maxWidth: .infinity)
                    faster
                }
            }
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, Space.s)
        .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
        .animation(.snappy, value: pace)
        .sensoryFeedback(.selection, trigger: pace)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Allure explorée")
        .accessibilityValue("\(Numbers.pace(pace)) par kilomètre")
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: change(max(150, pace - step))
            case .decrement: change(pace + step)
            @unknown default: break
            }
        }
    }

    private var figure: some View {
        VStack(spacing: 2) {
            Text(Numbers.pace(pace)).figure(.large).contentTransition(.numericText())
            Text("/km à plat équivalent").textRole(.caption).foregroundStyle(Palette.muted)
                .multilineTextAlignment(.center)
        }
    }

    private var slower: some View { button("Plus lent", symbol: "minus") { change(pace + step) } }
    private var faster: some View { button("Plus vite", symbol: "plus") { change(max(150, pace - step)) } }

    /// Both glyphs in the same square, so that the two circles are the same size.
    private func button(_ label: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.title3.weight(.semibold)).dynamicTypeSize(...DynamicTypeSize.xxxLarge)
                .frame(width: min(side, 60), height: min(side, 60))
        }
        .buttonStyle(.glass)
        .buttonBorderShape(.circle)
        .tint(Palette.ink)
        .accessibilityLabel(label)
    }
}

/// Two columns while the text is small: what a row names on the left, its figure on the
/// right, flush with the edge. At the accessibility sizes the figure goes under the words,
/// aligned with them.
private struct PlanColumns<Leading: View, Trailing: View>: View {
    @ViewBuilder let leading: Leading
    @ViewBuilder let trailing: Trailing
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: Space.xxs) {
                leading
                trailing
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline) {
                leading
                Spacer(minLength: Space.xs)
                trailing.multilineTextAlignment(.trailing)
            }
        }
    }
}

/// An aid station or the finish: its time since the start, its band, the time of day,
/// the carbohydrates of the stretch, and the forecast at that hour.
private struct PassageRow: View {
    let plan: Notebook.RacePlan
    let passage: Notebook.RacePlan.TimedPassage
    let hour: Notebook.PassageHour?

    var body: some View {
        let clock = plan.wallClock(after: passage.hours).flatMap(Numbers.clock)
        let carbs = passage.carbs.map { "≈ \(Int(($0 / 5).rounded()) * 5) g de glucides \(passage.finish ? "au dernier tronçon" : "au tronçon")" }
        let facts = [clock.map { "vers \($0)" }, carbs].compactMap { $0 }.joined(separator: " · ")
        let name = Text(passage.name).font(TextRole.secondary.font.weight(.semibold))
        let km = Text("km \(Numbers.text(passage.km, digits: 0))").font(TextRole.caption.font).foregroundStyle(Palette.muted)
        VStack(alignment: .leading, spacing: Space.xxs) {
            PlanColumns {
                Text("\(name)  \(km)")
            } trailing: {
                Text(Numbers.hours(passage.hours)).figure(.medium)
            }
            PlanColumns {
                Text(facts).textRole(.caption).foregroundStyle(Palette.muted)
            } trailing: {
                Text("\(Numbers.hours(passage.likely.lowerBound)) – \(Numbers.hours(passage.likely.upperBound))")
                    .textRole(.caption).monospacedDigit().foregroundStyle(Palette.muted)
            }
            if let hour {
                let sky = Sky(hour)
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    SkySymbol(sky: sky).font(.body)
                    Text("\(hour.temp)°, ressenti \(hour.feels)° · \(sky.word) · vent \(hour.wind) km/h").textRole(.caption)
                }
                .padding(.top, Space.xxs)
            }
        }
        .padding(.vertical, Space.s)
        .accessibilityElement(children: .combine)
    }
}

/// The main climbs, numbered as on the profile: where, how much, how steep at worst, and
/// when the foot comes at the explored pace. The bumps are only counted.
private struct ClimbList: View {
    let plan: Notebook.RacePlan
    let pace: Double?

    var body: some View {
        let main = plan.mainClimbs, bumps = plan.climbs.filter { !$0.main }
        VStack(alignment: .leading, spacing: 0) {
            SectionTitle(text: "Côtes", meta: "\(main.count) principale\(main.count > 1 ? "s" : "")")
            ForEach(Array(main.enumerated()), id: \.offset) { index, climb in
                row(climb, number: index + 1)
            }
            if !bumps.isEmpty {
                let gain = bumps.reduce(0) { $0 + $1.gainM }
                Text("Et \(bumps.count) bosse\(bumps.count > 1 ? "s" : "") plus courte\(bumps.count > 1 ? "s" : "") : +\(Int(gain)) m en tout.")
                    .textRole(.caption).foregroundStyle(Palette.muted).padding(.top, Space.xs)
            }
        }
    }

    /// The climb's number is bare, as on the profile, set apart from where it starts.
    private func row(_ climb: Notebook.RouteClimb, number: Int) -> some View {
        let length = climb.endKm - climb.startKm
        let steep = climb.steepestPct >= climb.gradePct + 2
            ? " · jusqu’à \(Int(climb.steepestPct.rounded())) % sur \(Int((climb.steepestM / 50).rounded()) * 50) m" : ""
        let foot = pace.map { plan.foot(of: climb, pace: $0) }
        let footText = foot.map { hours in
            "pied à \(Numbers.hours(hours))" + (plan.wallClock(after: hours).flatMap(Numbers.clock).map { ", vers \($0)" } ?? "")
        }
        let start = Text("km \(Numbers.text(climb.startKm))").font(TextRole.secondary.font.weight(.semibold))
        let end = Text("  → \(Numbers.text(climb.endKm))").font(TextRole.caption.font).foregroundStyle(Palette.muted)
        return VStack(alignment: .leading, spacing: Space.xxs) {
            PlanColumns {
                HStack(alignment: .firstTextBaseline, spacing: Space.xs) {
                    Text("\(number) ·").figure(.inline).foregroundStyle(Palette.muted)
                    Text("\(start)\(end)")
                }
            } trailing: {
                Text("+\(Int(climb.gainM)) m").figure(.medium)
            }
            PlanColumns {
                Text("\(Numbers.text(length)) km à \(Int(climb.gradePct.rounded())) %\(steep)").textRole(.caption).foregroundStyle(Palette.muted)
            } trailing: {
                if let footText { Text(footText).textRole(.caption).foregroundStyle(Palette.muted) }
            }
        }
        .padding(.vertical, Space.s)
        .accessibilityElement(children: .combine)
    }
}
