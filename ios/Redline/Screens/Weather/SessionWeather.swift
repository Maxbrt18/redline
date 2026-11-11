import RedlineKit
import SwiftUI

/// The sky as a two-tone symbol: a grey cloud, a yellow sun, blue rain, an indigo moon.
/// The system's multicolour clouds are white and vanish on paper. Colour stays within the
/// weather; elsewhere it keeps its meaning.
struct SkySymbol: View {
    let name: String

    init(sky: Sky) {
        name = Self.name(sky)
    }

    init(name: String) {
        self.name = name
    }

    var body: some View {
        let (primary, secondary) = colours
        Image(systemName: name)
            .symbolRenderingMode(.palette)
            .foregroundStyle(primary, secondary)
            .accessibilityHidden(true)
    }

    private var colours: (Color, Color) {
        switch name {
        case "sun.max.fill", "sunrise.fill": (.orange, .orange)
        case "moon.stars.fill": (.indigo, .indigo)
        case "cloud.sun.fill": (Palette.faint, .orange)
        case "cloud.moon.fill": (Palette.faint, .indigo)
        case "cloud.rain.fill", "cloud.bolt.rain.fill": (Palette.faint, .blue)
        case "cloud.snow.fill": (Palette.faint, .cyan)
        default: (Palette.faint, Palette.faint)
        }
    }

    static func name(_ sky: Sky) -> String {
        switch sky.look {
        case .sun: "sun.max.fill"
        case .moon: "moon.stars.fill"
        case .partly: sky.day ? "cloud.sun.fill" : "cloud.moon.fill"
        case .cloud: "cloud.fill"
        case .fog: "cloud.fog.fill"
        case .rain: "cloud.rain.fill"
        case .snow: "cloud.snow.fill"
        case .storm: "cloud.bolt.rain.fill"
        }
    }
}

/// The forecasts already received, kept in memory for a quarter of an hour: coming back
/// to a screen redraws them without a request and without a flicker.
@MainActor
private enum Skies {
    static var known: [String: (at: Date, forecast: Notebook.Forecast?)] = [:]

    static func forecast(for item: Notebook.SessionItem, session: NotebookSession?) async -> Notebook.Forecast? {
        let endpoint = Endpoint.weather(date: item.date, time: item.startTime, place: item.location)
        if let kept = known[endpoint.cacheKey], kept.at.timeIntervalSinceNow > -15 * 60 { return kept.forecast }
        guard case .signedIn(let client)? = session?.state else { return nil }
        guard let forecast = try? await client.get(endpoint) else { return nil }
        known[endpoint.cacheKey] = (.now, forecast)
        return forecast
    }

    static func cached(_ item: Notebook.SessionItem) -> Notebook.Forecast? {
        known[Endpoint.weather(date: item.date, time: item.startTime, place: item.location).cacheKey]?.forecast
    }
}

/// Before leaving: the sky and the felt temperature at the start, the night against the
/// session, and the watch's heart-rate alert. Weather arrives after the screen and its
/// absence breaks nothing.
struct SessionWeather: View {
    enum Size {
        /// Today's session: the sky on two lines, then the night and the watch.
        case full
        /// Another day's session: one line.
        case line
    }

    let item: Notebook.SessionItem
    var size = Size.full
    @Environment(\.notebook) private var session
    @State private var forecast: Notebook.Forecast?
    @State private var settled = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let forecast, let first = forecast.hours.first {
                switch size {
                case .full: full(forecast, first: first)
                case .line: line(forecast, first: first)
                }
            } else if !settled, size == .full {
                // The block's room is kept while it loads, so nothing below is pushed.
                Color.clear.frame(height: 40)
            }
            if size == .full, let cap = item.targets.hrCapBpm {
                PrepRow(symbol: "applewatch") {
                    Text("Alerte FC de la montre sur ") + Text("\(cap)").bold()
                }
            }
        }
        .task(id: item.id) {
            forecast = Skies.cached(item)
            forecast = await Skies.forecast(for: item, session: session)
            settled = true
        }
    }

    @ViewBuilder private func full(_ forecast: Notebook.Forecast, first: Notebook.ForecastHour) -> some View {
        let sky = Sky(first), last = forecast.hours.last ?? first
        let words = sky.wet ? sky.word : sky.word + ((first.rain ?? 0) >= 20 ? ", \(Sky.rainWord(first.rain))" : ", sec")
        let later = [first.wind >= 25 ? "Vent fort \(first.wind) km/h" : "Vent \(first.wind) km/h",
                     last.hour != first.hour ? "\(last.temp)° à \(last.hour) h" : nil].compactMap { $0 }
        PrepRow(sky: sky) {
            HStack(alignment: .center, spacing: 10) {
                Text("\(first.temp)°").font(.figure(30))
                VStack(alignment: .leading, spacing: 1) {
                    Text("Ressenti \(first.feels)° · \(words)").font(.subheadline)
                    Text(later.joined(separator: " · ")).font(.caption).foregroundStyle(Palette.muted)
                }
            }
        }
        if let night = forecast.night(start: item.startTime, minutes: item.targets.durationCapMin) {
            PrepRow(sky: night.sunrise ? "sunrise.fill" : "moon.stars.fill") { Text(night.text) }
        }
    }

    private func line(_ forecast: Notebook.Forecast, first: Notebook.ForecastHour) -> some View {
        let sky = Sky(first)
        return PrepRow(sky: sky) {
            VStack(alignment: .leading, spacing: 1) {
                Text("\(first.temp)°, ressenti \(first.feels)° · \(sky.word)").font(.subheadline)
                if let sunset = forecast.sunset.flatMap(Numbers.clock) {
                    Text("Nuit à \(sunset)").font(.caption).foregroundStyle(Palette.muted)
                }
            }
        }
    }
}

/// One line of the departure sheet: a symbol on a pill, then its words.
private struct PrepRow<Words: View>: View {
    let symbol: String
    /// A sky's symbol, in its colours; otherwise the glyph is ink.
    var sky = false
    @ViewBuilder var words: Words

    init(symbol: String, @ViewBuilder words: () -> Words) {
        self.symbol = symbol
        self.words = words()
    }

    init(sky symbol: String, @ViewBuilder words: () -> Words) {
        self.symbol = symbol
        self.sky = true
        self.words = words()
    }

    init(sky: Sky, @ViewBuilder words: () -> Words) {
        self.init(sky: SkySymbol.name(sky), words: words)
    }

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if sky {
                    SkySymbol(name: symbol)
                } else {
                    Image(systemName: symbol).foregroundStyle(Palette.ink)
                }
            }
            .font(.system(size: 15))
            .frame(width: 34, height: 34)
            .background(Palette.panel, in: .circle)
            .accessibilityHidden(true)
            words.font(.subheadline)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A few words in a line of what is coming: the sky and the temperature at the start.
struct InlineWeather: View {
    let item: Notebook.SessionItem
    @Environment(\.notebook) private var session
    @State private var forecast: Notebook.Forecast?

    var body: some View {
        Group {
            if let first = forecast?.hours.first {
                let sky = Sky(first)
                HStack(spacing: 3) {
                    SkySymbol(sky: sky)
                    Text("\(first.temp)°, \(sky.word)" + (item.startTime == nil && first.hour < 12 ? " le matin" : ""))
                }
            }
        }
        .task(id: item.id) {
            forecast = Skies.cached(item)
            forecast = await Skies.forecast(for: item, session: session)
        }
    }
}
