import RedlineKit
import SwiftUI

extension Family {
    /// A program family (run, strength…) or a ledger type (easy, long…), reduced to its
    /// badge through the kinds table.
    init(kind: String, workout: String? = nil) {
        if kind == "long" || workout == "long" {
            self = .longRun
            return
        }
        switch Notebook.kinds[kind]?.family ?? kind {
        case "strength": self = .strength
        case "rest": self = .rest
        case "run", "test": self = .run
        default: self = .otherSport
        }
    }
}

extension Notebook {
    /// A kind's name as the athlete reads it; the slug when the table does not know it.
    static func kindName(_ kind: String) -> String { kinds[kind]?.name ?? kind }
}

extension Notebook.Targets {
    /// `6-7` for a band, `≤7` for a ceiling.
    var rpeRange: String? {
        guard let rpeMax else { return nil }
        if let rpeMin, rpeMin != rpeMax { return "\(rpeMin)-\(rpeMax)" }
        return "≤\(rpeMax)"
    }
}

extension Notebook.SessionItem {
    var family: Family { Family(kind: kind, workout: workout) }
    var isStrength: Bool { kind == "strength" }
    var isDone: Bool { status == "done" }

    /// A run's measures, when the session happened.
    var runFact: Notebook.Fact? {
        if case .fact(let fact) = fact { fact } else { nil }
    }

    var strengthFact: Notebook.StrengthFact? {
        if case .strengthFact(let fact) = fact { fact } else { nil }
    }

    /// What is planned, in one figure: `12 km`, `≤12 km`, `≤45 min`.
    var plannedValue: String {
        if let km = targets.distanceTargetKm { return "\(Numbers.text(km)) km" }
        if let km = targets.distanceCapKm { return "≤\(Numbers.text(km)) km" }
        if let minutes = targets.durationCapMin { return "≤\(minutes) min" }
        return ""
    }
}

extension Notebook.Fact {
    /// When the run started, as a clock: `7 h 12`.
    var startClock: String? { startedAt.flatMap(Numbers.clock) }

    var pace: String? {
        guard let km, km > 0, let durationMin else { return nil }
        return Numbers.pace(Double(durationMin) * 60 / km)
    }
}

extension Day {
    /// « jeudi 12 », as a thread names a day.
    var weekdayAndNumber: String {
        date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.wide).day())
    }
}
