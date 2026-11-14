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

/// The weather in the day's card, on one line: the sky with the temperature and the felt one
/// at the start, then when night falls. It takes the card's ink, and its absence breaks
/// nothing.
struct CardWeather: View {
    let item: Notebook.SessionItem
    @Environment(\.notebook) private var session
    @State private var forecast: Notebook.Forecast?

    var body: some View {
        // A stack, not a group: a group hands its modifiers to its children, and before the
        // forecast there is none to run the task.
        VStack(alignment: .leading, spacing: 0) {
            if let forecast, let first = forecast.hours.first {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Space.m) { sky(first); night(forecast) }
                    VStack(alignment: .leading, spacing: Space.xxs) { sky(first); night(forecast) }
                }
                .textRole(.secondary)
                .accessibilityElement(children: .combine)
            }
        }
        .task(id: item.id) {
            forecast = Skies.cached(item)
            forecast = await Skies.forecast(for: item, session: session)
        }
    }

    private func sky(_ first: Notebook.ForecastHour) -> some View {
        Label {
            Text("\(first.temp)°, ressenti \(first.feels)°")
        } icon: {
            Image(systemName: SkySymbol.name(Sky(first))).symbolRenderingMode(.hierarchical)
        }
    }

    @ViewBuilder private func night(_ forecast: Notebook.Forecast) -> some View {
        if let sunset = forecast.sunset.flatMap(Numbers.figureClock) {
            Label("Nuit à \(sunset)", systemImage: "moon.fill")
        }
    }
}
