/// The sky of a forecast hour, from its WMO code (Open-Meteo): a look and a word, by day
/// and by night. Weather serves two decisions only: what to wear, and whether night falls
/// during the session.
public struct Sky: Hashable, Sendable {
    public enum Look: Hashable, Sendable {
        case sun, moon, partly, cloud, fog, rain, snow, storm
    }

    public let look: Look
    public let word: String
    /// Rain, snow or a storm: the word already says it is wet.
    public let wet: Bool
    public let day: Bool

    public init(code: Int?, day: Bool, rain: Int?) {
        self.day = day
        let chance = rain ?? 0
        guard let code else {
            // An old forecast without a code only tells about rain.
            (look, word, wet) = (chance >= 50 ? .rain : day ? .sun : .moon, Self.rainWord(rain), true)
            return
        }
        (look, word, wet) = switch code {
        case 95...: (.storm, "orage", true)
        case 71...77, 85, 86: (.snow, "neige", true)
        case 80...: (.rain, "averses", true)
        case 61...: (.rain, "pluie", true)
        case 51...: (.rain, "bruine", true)
        case 45, 48: (.fog, "brouillard", false)
        case 3: (.cloud, "couvert", false)
        case 2: (.partly, "éclaircies", false)
        case 1: (day ? .sun : .moon, "peu nuageux", false)
        default: (day ? .sun : .moon, day ? "ensoleillé" : "ciel clair", false)
        }
    }

    public init(_ hour: Notebook.ForecastHour) {
        self.init(code: hour.code, day: hour.day, rain: hour.rain)
    }

    public init(_ hour: Notebook.PassageHour) {
        self.init(code: hour.code, day: hour.day, rain: hour.rain)
    }

    public static func rainWord(_ chance: Int?) -> String {
        switch chance ?? 0 {
        case 50...: "pluie probable"
        case 20...: "pluie possible"
        default: "sec"
        }
    }
}

public extension Notebook.Forecast {
    /// Night, stated relative to the start: before, during or after the session. `minutes`
    /// is the session's planned duration, when it has one. Nil without a sunset.
    func night(start: String?, minutes: Int?) -> (sunrise: Bool, text: String)? {
        guard let sunset, let set = Self.hours(sunset), let setClock = Numbers.clock(sunset) else { return nil }
        guard let start, let begin = Self.hours(start) else { return (false, "Nuit à \(setClock).") }
        if let sunrise, let rise = Self.hours(sunrise), begin < rise, let riseClock = Numbers.clock(sunrise) {
            return (true, "Départ avant le jour : il se lève à \(riseClock).")
        }
        if begin >= set { return (false, "Départ de nuit : elle tombe à \(setClock).") }
        if let minutes {
            let end = begin + Double(minutes) / 60
            if end > set { return (false, "Nuit à \(setClock) : elle tombe pendant ta séance.") }
            let margin = Int(((set - end) * 60).rounded())
            return (false, "Nuit à \(setClock) : tu rentres \(margin >= 60 ? "plus d’une heure" : "\(margin) min") avant.")
        }
        let left = Int(((set - begin) * 60).rounded())
        return (false, "Nuit à \(setClock), \(left / 60) h \(String(format: "%02d", left % 60)) après ton départ.")
    }

    private static func hours(_ text: String) -> Double? {
        guard let match = text.firstMatch(of: /(\d{2}):(\d{2})/), let hours = Double(match.1), let minutes = Double(match.2)
        else { return nil }
        return hours + minutes / 60
    }
}
