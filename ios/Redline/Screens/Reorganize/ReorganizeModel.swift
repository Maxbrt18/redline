import Foundation
import Observation
import RedlineKit

/// The week being reorganised: what the athlete said of its days on this screen, and the
/// variants the engine computes for them. Each change asks again; an answer that arrives
/// after a newer question is dropped.
@Observable
final class ReorganizeModel {
    let week: String
    private(set) var view: Notebook.ReorganizeView?
    private(set) var failure: String?
    /// The days touched on this screen, by date.
    private(set) var touched: [String: StatedDay] = [:]
    /// The request to the coach, open below the variants.
    var asking = false
    var wish = "intensity"
    var context = ""
    private var question = 0

    init(week: String) {
        self.week = week
    }

    /// The day as known: touched here, else stated before or given by its habit.
    func known(_ day: Notebook.ReorganizeDay) -> StatedDay? {
        touched[day.date] ?? day.stated.map { StatedDay(date: day.date, state: $0) }
    }

    /// What the screen sends: the days touched, a day set back to as planned included, so
    /// that it clears a habit for this week.
    var stated: [StatedDay] { touched.values.sorted { $0.date < $1.date } }

    /// The days said to differ from the plan.
    var differing: [StatedDay] {
        (view?.days ?? []).compactMap(known).filter { $0.kind != .asPlanned }
    }

    func load(_ session: NotebookSession) async {
        guard case .signedIn(let client) = session.state else { return }
        question += 1
        let mine = question
        do {
            let answer = try await client.reorganize(week, days: stated)
            guard mine == question else { return }
            view = answer
            failure = nil
        } catch .signedOut(let message) {
            session.forget(saying: message.isEmpty ? nil : message)
        } catch {
            guard mine == question else { return }
            failure = NotebookSession.words(for: error)
        }
    }

    /// A day changed: the variants follow at once.
    func set(_ day: StatedDay, _ session: NotebookSession) async {
        touched[day.date] = day
        await load(session)
    }

    /// A detail the engine never reads (a sport's name): kept, nothing recomputed.
    func keep(_ day: StatedDay) {
        touched[day.date] = day
    }

    func forget(_ date: String) {
        touched[date] = nil
    }
}

/// Words shared by the reorganisation's screens.
enum DayWords {
    static let states: [(StatedDay.Kind, String)] = [
        (.asPlanned, "Comme prévu"), (.off, "Pas de course"), (.short, "Court seulement"), (.easy, "Facile seulement"),
        (.more, "Je peux en faire plus"), (.withOthers, "Avec d’autres"), (.otherSport, "Autre sport"),
    ]

    static func label(_ kind: StatedDay.Kind) -> String {
        states.first { $0.0 == kind }?.1 ?? kind.rawValue
    }

    static let paces: [(StatedDay.Pace, String)] = [(.easy, "Tranquille"), (.mixed, "Variable"), (.workout, "Séance")]

    static func pace(_ pace: StatedDay.Pace?) -> String {
        paces.first { $0.0 == pace }?.1 ?? ""
    }

    /// « 45 min », « 1 h 30 ».
    static func hours(_ minutes: Int) -> String {
        minutes >= 60 ? "\(minutes / 60) h" + (minutes % 60 > 0 ? String(format: " %02d", minutes % 60) : "") : "\(minutes) min"
    }

    /// « lun », « lun 6 ».
    static func weekday(_ date: String) -> String {
        Day(date)?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.abbreviated))
            .replacingOccurrences(of: ".", with: "") ?? date
    }

    static func day(_ date: String) -> String {
        weekday(date) + " \(Day(date)?.day ?? 0)"
    }

    /// The weekday's full name, « lundi ».
    static func weekdayName(_ date: String) -> String {
        Day(date)?.date.formatted(Date.FormatStyle(timeZone: .gmt).locale(Locale(identifier: "fr_FR")).weekday(.wide)) ?? date
    }

    /// The ISO weekday, 1 for Monday.
    static func isoWeekday(_ date: String) -> Int {
        guard let day = Day(date) else { return 1 }
        let sunday = Calendar(identifier: .gregorian).dateComponents(in: .gmt, from: day.date).weekday ?? 2
        return (sunday + 5) % 7 + 1
    }

    /// The details of a day as stated: « ≤ 45 min », « ~1 h, tranquille · si ça rentre ».
    static func line(_ day: StatedDay, habit: Bool = false) -> String {
        var words: String = switch day.kind {
        case .short: day.minutes.map { "≤ " + hours($0) } ?? ""
        case .withOthers:
            "~" + hours(day.minutes ?? 60) + ", " + pace(day.pace).lowercased() + (day.required == true ? " · obligatoire" : " · si ça rentre")
        case .otherSport:
            [day.sport, day.demanding == true ? "exigeant" : "léger", day.runToo == true ? "course possible" : nil]
                .compactMap { $0 }.joined(separator: " · ")
        default: ""
        }
        if habit, day.kind != .asPlanned { words = [words, "habitude"].filter { !$0.isEmpty }.joined(separator: " · ") }
        return words
    }
}
