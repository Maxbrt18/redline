import RedlineKit
import SwiftUI

/// The sky as a symbol in one ink, its layers told apart by the system's hierarchical
/// rendering: the weather keeps to the page's palette, and its words say what it is.
struct SkySymbol: View {
    let name: String

    init(sky: Sky) {
        name = Self.name(sky)
    }

    init(name: String) {
        self.name = name
    }

    var body: some View {
        Image(systemName: name)
            .symbolRenderingMode(.hierarchical)
            .foregroundStyle(Palette.muted)
            .accessibilityHidden(true)
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

/// Before leaving: the temperature, the wind and nightfall at the start, then the sky in
/// words. Weather arrives after the screen and its absence breaks nothing.
struct SessionWeather: View {
    enum Size {
        /// Today's session: three figures side by side, then a line of words.
        case full
        /// Another day's session: one line.
        case line
    }

    let item: Notebook.SessionItem
    var size = Size.full
    @Environment(\.notebook) private var session
    @State private var forecast: Notebook.Forecast?
    @State private var settled = false
    @ScaledMetric(relativeTo: .subheadline) private var placeholder: CGFloat = 88

    var body: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            if let forecast, let first = forecast.hours.first {
                switch size {
                case .full: full(forecast, first: first)
                case .line: line(forecast, first: first)
                }
            } else if !settled, size == .full {
                // The block's room is kept while it loads, so nothing below is pushed.
                Color.clear.frame(height: placeholder)
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
        let later = last.hour != first.hour ? "\(last.temp)° à \(last.hour) h" : nil
        let night = forecast.night(start: item.startTime, minutes: item.targets.durationCapMin)
        let dark = night?.sunrise == true ? forecast.sunrise : forecast.sunset
        let figures = [
            WeatherFigure(symbol: SkySymbol.name(sky), value: "\(first.temp)°", words: "ressenti \(first.feels)°"),
            WeatherFigure(symbol: "wind", value: "\(first.wind)", words: first.wind >= 25 ? "km/h, vent fort" : "km/h de vent"),
            dark.flatMap(Numbers.figureClock).map {
                WeatherFigure(symbol: night?.sunrise == true ? "sunrise.fill" : "moon.stars.fill", value: $0,
                              words: night?.sunrise == true ? "lever du jour" : "nuit")
            },
        ].compactMap { $0 }
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: Space.s) {
                ForEach(Array(figures.enumerated()), id: \.offset) { _, figure in figure }
            }
            VStack(alignment: .leading, spacing: Space.s) {
                ForEach(Array(figures.enumerated()), id: \.offset) { _, figure in figure }
            }
        }
        VStack(alignment: .leading, spacing: 2) {
            Text([words.capitalizedFirst, later].compactMap { $0 }.joined(separator: " · "))
            // With no start time, the night's sentence only repeats its figure.
            if item.startTime != nil, let night { Text(night.text) }
        }
        .textRole(.caption)
        .foregroundStyle(Palette.muted)
    }

    private func line(_ forecast: Notebook.Forecast, first: Notebook.ForecastHour) -> some View {
        let sky = Sky(first)
        return PrepRow(sky: sky) {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(first.temp)°, ressenti \(first.feels)° · \(sky.word)").textRole(.secondary)
                if let sunset = forecast.sunset.flatMap(Numbers.clock) {
                    Text("Nuit à \(sunset)").textRole(.caption).foregroundStyle(Palette.muted)
                }
            }
        }
    }
}

/// One figure of the departure sheet: its symbol and its value, its words under them.
private struct WeatherFigure: View {
    let symbol: String
    let value: String
    let words: String

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xxs) {
            HStack(spacing: Space.xxs) {
                SkySymbol(name: symbol).font(.subheadline)
                Text(value).figure(.large, .done)
            }
            Text(words).textRole(.caption).foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// One line of another day's weather: the sky's symbol in a column that follows the text
/// size, then its words.
private struct PrepRow<Words: View>: View {
    let symbol: String
    @ViewBuilder var words: Words
    @ScaledMetric(relativeTo: .body) private var side: CGFloat = 28

    init(sky: Sky, @ViewBuilder words: () -> Words) {
        self.symbol = SkySymbol.name(sky)
        self.words = words()
    }

    var body: some View {
        HStack(spacing: Space.s) {
            SkySymbol(name: symbol)
            .font(.body)
            .frame(width: side)
            .accessibilityHidden(true)
            words.textRole(.secondary)
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
                HStack(spacing: Space.xxs) {
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
