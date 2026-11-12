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
        VStack(alignment: .leading, spacing: 0) {
            if let pace {
                PaceStepper(pace: pace) { explored = $0 }
                Text(paceNote(pace)).font(.footnote).foregroundStyle(Palette.muted).padding(.top, 8)
            } else {
                Text("Allure à choisir avec le coach : les passages s’afficheront ensuite.")
                    .font(.footnote).foregroundStyle(Palette.muted)
            }
            SectionTitle(text: "Profil", meta: plan.source == .ign ? "altitude IGN" : "altitude du GPX")
            RaceProfile(plan: plan, full: true).padding(.top, 14)
            if let pace { passages(pace) }
            if !plan.climbs.isEmpty { ClimbList(plan: plan, pace: pace) }
            SectionTitle(text: "Parcours")
            Text(courseFacts).font(.footnote).foregroundStyle(Palette.muted).padding(.top, 10)
        }
        .padding(.bottom, 24)
    }

    private func paceNote(_ pace: Double) -> String {
        guard let chosen = plan.paceSPerKm, pace != chosen else {
            return "Allure retenue avec le coach. Le − / + explore sans rien enregistrer."
        }
        return "Allure retenue avec le coach : \(Numbers.pace(chosen))/km. Ici, tu explores : rien n’est enregistré."
    }

    /// In large, the time on the watch since the start; the time of day comes after
    /// « vers », so a passage never reads as two clocks.
    @ViewBuilder private func passages(_ pace: Double) -> some View {
        let timed = plan.passages(pace: pace)
        SectionTitle(text: "Passages", meta: plan.start.flatMap(Numbers.clock).map { "temps de course · départ \($0)" } ?? "temps de course")
        ForEach(Array(timed.enumerated()), id: \.offset) { index, passage in
            PassageRow(plan: plan, passage: passage, hour: hour(of: passage, at: index))
        }
        if let finish = timed.last {
            Text("Arrivée 9 fois sur 10 entre \(Numbers.hours(finish.nineInTen.lowerBound)) et \(Numbers.hours(finish.nineInTen.upperBound)) de course. Nuit, fatigue et terrain technique ne sont pas modélisés.")
                .font(.footnote).foregroundStyle(Palette.muted).padding(.top, 10)
        }
        if let start = plan.start, view.weather == nil, let opens = Day(String(start.prefix(10)))?.adding(-15) {
            SectionTitle(text: "Météo aux passages")
            Text("Prévision disponible vers le \(opens.spelled(weekday: false)) : ressenti, ciel et vent à chaque ravito, à l’heure du passage.")
                .font(.footnote).foregroundStyle(Palette.muted).padding(.top, 10)
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

/// The pace explored, five seconds at a time, between two glass buttons.
private struct PaceStepper: View {
    let pace: Double
    let change: (Double) -> Void
    private let step = 5.0

    var body: some View {
        HStack(spacing: 10) {
            button("Plus lent", symbol: "minus") { change(pace + step) }
            VStack(spacing: 2) {
                Text(Numbers.pace(pace)).font(.figure(40)).monospacedDigit().contentTransition(.numericText())
                Text("/km à plat équivalent").font(.caption).foregroundStyle(Palette.muted)
            }
            .frame(maxWidth: .infinity)
            button("Plus vite", symbol: "plus") { change(max(150, pace - step)) }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Palette.panel, in: .rect(cornerRadius: 18))
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

    /// Both glyphs in the same square, so that the two circles are the same size.
    private func button(_ label: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.title3.weight(.semibold)).frame(width: 30, height: 30)
        }
        .buttonStyle(.glass)
        .buttonBorderShape(.circle)
        .tint(Palette.ink)
        .accessibilityLabel(label)
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
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                (Text(passage.name).font(.subheadline.weight(.semibold))
                    + Text("  km \(Numbers.text(passage.km, digits: 0))").font(.footnote).foregroundStyle(Palette.muted))
                Spacer(minLength: 8)
                Text(Numbers.hours(passage.hours)).font(.figure(passage.finish ? 26 : 22)).monospacedDigit()
            }
            HStack(alignment: .firstTextBaseline) {
                Text(facts).font(.caption).foregroundStyle(Palette.muted)
                Spacer(minLength: 8)
                Text("\(Numbers.hours(passage.likely.lowerBound)) – \(Numbers.hours(passage.likely.upperBound))")
                    .font(.caption).foregroundStyle(Palette.muted).lineLimit(1).fixedSize()
            }
            if let hour {
                let sky = Sky(hour)
                HStack(spacing: 6) {
                    SkySymbol(sky: sky).font(.body)
                    Text("\(hour.temp)°, ressenti \(hour.feels)° · \(sky.word) · vent \(hour.wind) km/h").font(.footnote)
                }
                .padding(.top, 2)
            }
        }
        .padding(.vertical, 12)
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
                    .font(.footnote).foregroundStyle(Palette.muted).padding(.top, 10)
            }
        }
    }

    private func row(_ climb: Notebook.RouteClimb, number: Int) -> some View {
        let length = climb.endKm - climb.startKm
        let steep = climb.steepestPct >= climb.gradePct + 2
            ? " · jusqu’à \(Int(climb.steepestPct.rounded())) % sur \(Int((climb.steepestM / 50).rounded()) * 50) m" : ""
        let foot = pace.map { plan.foot(of: climb, pace: $0) }
        let footText = foot.map { hours in
            "pied à \(Numbers.hours(hours))" + (plan.wallClock(after: hours).flatMap(Numbers.clock).map { ", vers \($0)" } ?? "")
        }
        return VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(number)")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Palette.hRace)
                        .frame(width: 20, height: 20)
                        .background(Palette.hRace.opacity(0.18), in: .circle)
                        .alignmentGuide(.firstTextBaseline) { $0[.firstTextBaseline] }
                    (Text("km \(Numbers.text(climb.startKm))").font(.subheadline.weight(.semibold))
                        + Text("  → \(Numbers.text(climb.endKm))").font(.footnote).foregroundStyle(Palette.muted))
                }
                Spacer(minLength: 8)
                Text("+\(Int(climb.gainM)) m").font(.figure(22))
            }
            HStack(alignment: .firstTextBaseline) {
                Text("\(Numbers.text(length)) km à \(Int(climb.gradePct.rounded())) %\(steep)").font(.caption).foregroundStyle(Palette.muted)
                Spacer(minLength: 8)
                if let footText { Text(footText).font(.caption).foregroundStyle(Palette.muted).multilineTextAlignment(.trailing) }
            }
        }
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }
}
