import RedlineKit
import SwiftUI

/// What the Plan screen says of a cycle, its weeks, its key sessions and its risks.
enum PlanWords {
    private static let french = Locale(identifier: "fr_FR")

    static func kind(_ kind: Notebook.PlanChange.Kind) -> String {
        switch kind {
        case .preparation: "Préparation"
        case .general: "Générale"
        case .recovery: "Récupération"
        case .rebuild: "Reprise"
        case .unknown(let value): value
        }
    }

    static func title(of cycle: Notebook.PlanCycle) -> String {
        switch cycle.kind {
        case .preparation: "Préparation"
        case .general: "Préparation générale, \(level(cycle.level))"
        case .recovery: "Récupération"
        case .rebuild: "Reprise"
        case .unknown(let value): value
        }
    }

    static func level(_ level: Notebook.PlanCycle.Level?) -> String {
        switch level {
        case .progress: "progresser"
        case .maintain: "maintenir"
        case .light: "détente"
        case .unknown(let value): value
        case nil: ""
        }
    }

    /// `Semi des Berges, dimanche 6 décembre`.
    static func race(_ race: Notebook.PlanRace) -> String {
        guard let date = Day(race.date) else { return race.name }
        let day = date.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(french).weekday(.wide).day().month(.wide))
        return "\(race.name), \(day)"
    }

    static func phase(_ phase: Notebook.PlanWeek.Phase) -> String {
        switch phase {
        case .recovery: "récupération"
        case .development: "développement"
        case .specific: "spécifique"
        case .taper: "affûtage"
        case .general: "générale"
        case .rebuild: "reprise"
        case .unknown(let value): value
        }
    }

    static func candidate(_ slug: String) -> String {
        switch slug {
        case "cautious": "prudent"
        case "balanced": "équilibré"
        case "specific": "spécifique"
        default: slug
        }
    }

    /// `1er décembre`, `6 avril`.
    static func day(_ text: String) -> String {
        guard let day = Day(text) else { return text }
        let formatted = day.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(french).day().month(.wide))
        return day.day == 1 ? formatted.replacingOccurrences(of: "1 ", with: "1er ", options: .anchored) : formatted
    }

    static func weekName(_ label: String) -> String {
        "Semaine \(Int(label.suffix(2)) ?? 0)"
    }

    static func headline(of week: Notebook.PlanWeek) -> String {
        let name = weekName(week.week)
        if week.state == .current { return "\(name), en cours" }
        if week.deload { return "\(name), décharge" }
        return "\(name), \(phase(week.phase))"
    }

    /// `[1] Seuil 3 × 10:00, à 4:12–4:24/km`; the number is what a session links to.
    static func key(_ key: Notebook.PlanKey, numbered: Bool = true, paces: Bool = true) -> String {
        let head = numbered ? "[\(key.position)] " : ""
        if key.role == .test { return head + "Test 1 200 m puis 3 600 m à fond" }
        let star = key.role == .flagship ? "★ " : ""
        let name = Notebook.kinds[key.workout]?.name ?? key.workout
        var text = head + star + name
        if let reps = key.reps, let seconds = key.repSeconds {
            text += " \(reps) × \(Numbers.duration(Double(seconds)))"
        }
        guard paces else { return text }
        if let pace = key.paceS, pace.count == 2 {
            text += ", à \(Numbers.pace(pace[1]))–\(Numbers.pace(pace[0]))/km"
        } else if let rpe = key.rpe, rpe.count == 2 {
            text += ", effort \(Numbers.text(rpe[0], digits: 0))–\(Numbers.text(rpe[1], digits: 0))"
        } else if key.paceS == nil && key.rpe == nil {
            text += ", allure à calibrer"
        }
        return text
    }

    static func risk(_ risk: Notebook.PlanRisk) -> String {
        let value = Numbers.text(risk.value), reference = Numbers.text(risk.reference)
        switch risk.kind {
        case .windowShort: return "Fenêtre de \(value) semaines pour \(reference) au minimum."
        case .specificShort: return "Spécifique de \(value) semaines pour \(reference) demandées."
        case .timeBound: return "Le temps disponible plafonne le pic à \(value) km : la rampe aurait permis \(reference)."
        case .arrivalUnreached:
            let name = Notebook.kinds[risk.workout]?.name ?? risk.workout
            return "\(name) finit à \(value) cran\(risk.value > 1 ? "s" : "") de son arrivée."
        case .belowDomain: return "Plus haut volume tenu \(value) km, sous le domaine du gabarit (\(reference) km)."
        case .unknown(let value): return value
        }
    }

    static func next(of cycle: Notebook.PlanCycle) -> String {
        switch cycle.next {
        case .block: "Ensuite : le bloc suivant, écrit le dernier dimanche de celui-ci."
        case .recovery: "Ensuite : récupération après la course, puis préparation générale."
        case .general: "Ensuite : préparation générale."
        case .unknown(let value): value
        }
    }

    /// The phases of a run of weeks, in order, with their length.
    static func runs(of weeks: [Notebook.PlanWeek]) -> [(phase: Notebook.PlanWeek.Phase, count: Int)] {
        var found: [(phase: Notebook.PlanWeek.Phase, count: Int)] = []
        for week in weeks {
            if let last = found.last, last.phase == week.phase {
                found[found.count - 1].count += 1
            } else {
                found.append((week.phase, 1))
            }
        }
        return found
    }

    /// `semaines 8 à 12`, or `semaine 13`.
    static func span(of weeks: [Notebook.PlanWeek]) -> String {
        let numbers = weeks.compactMap { Int($0.week.suffix(2)) }
        guard let first = numbers.first, let last = numbers.last else { return "" }
        return first == last ? "semaine \(first)" : "semaines \(first) à \(last)"
    }
}
