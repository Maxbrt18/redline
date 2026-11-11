import RedlineKit
import SwiftUI

/// Today: the day's session in the head, the day's thread, what is coming, the landmarks.
struct TodayScreen: View {
    let session: NotebookSession
    let openWeek: () -> Void

    var body: some View {
        NotebookScreen(title: NotebookTab.today.title, session: session, model: ScreenModel(.today)) { today in
            if let (kicker, item) = Self.hero(today) {
                TodayHero(kicker: kicker, item: item)
            } else {
                Kicker(text: Day(today.date)?.spelled().capitalizedFirst ?? today.date)
            }
        } content: { today in
            TodayContent(today: today, openWeek: openWeek)
        }
    }

    /// The session the head carries: the run still to do; else the run done today; else
    /// the next run.
    static func hero(_ today: Notebook.TodayView) -> (String, Notebook.SessionItem)? {
        let items = today.sessions.filter { $0.kind != "rest" }
        if let main = items.first(where: { !$0.isStrength && !$0.isDone && $0.status != "held" }) {
            return ("Au programme", main)
        }
        if let ran = items.first(where: { !$0.isStrength && $0.isDone && $0.runFact != nil }) {
            return ("Fait", ran)
        }
        let later = today.tomorrow.sessions + today.tomorrow.next + today.ahead
        guard let next = later.first(where: { $0.date > today.date && !$0.isStrength && $0.kind != "rest" && !$0.isDone })
        else { return nil }
        let kicker = next.date == today.tomorrow.date ? "Demain" : Day(next.date)?.weekdayAndNumber.capitalizedFirst ?? next.date
        return (kicker, next)
    }
}

private struct TodayContent: View {
    let today: Notebook.TodayView
    let openWeek: () -> Void

    var body: some View {
        let items = today.sessions.filter { $0.kind != "rest" }
        let hero = TodayScreen.hero(today)?.1
        VStack(alignment: .leading, spacing: 0) {
            if ["awaiting", "stale"].contains(today.plan.status) {
                Label(today.plan.message, systemImage: "hourglass")
                    .font(.subheadline)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Palette.panel, in: .rect(cornerRadius: 12))
                    .padding(.bottom, 20)
            }
            if !items.isEmpty || !today.extra.isEmpty {
                DayThread(items: items, extra: today.extra, hero: hero, strength: today.strength)
                AheadList(items: today.ahead.filter { $0.date != today.date }, tomorrow: today.tomorrow.date)
            } else {
                RestDay(today: today, hero: hero)
            }
            Landmarks(today: today, openWeek: openWeek)
        }
        .padding(.bottom, 24)
    }
}

/// A rest day: one line for today, then the next day that holds something.
private struct RestDay: View {
    let today: Notebook.TodayView
    let hero: Notebook.SessionItem?

    var body: some View {
        let next = today.tomorrow
        let real = next.sessions.filter { $0.kind != "rest" }
        let shown = !real.isEmpty ? next.date : next.next.first?.date
        VStack(alignment: .leading, spacing: 18) {
            if today.plan.status == "invalid" {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Programme en préparation").font(.title3.weight(.semibold))
                    Text(today.plan.message).font(.subheadline).foregroundStyle(Palette.muted)
                }
            } else {
                (Text("Repos aujourd’hui.").bold() + Text(" Rien à faire.")).font(.body)
            }
            if !real.isEmpty {
                DayHeading(date: next.date, prefix: "Demain")
                DayThread(items: real, extra: [], hero: hero, today: false)
            } else if next.plan == "invalid" {
                Text("Le programme de la semaine arrive avec le bilan.").font(.subheadline).foregroundStyle(Palette.muted)
            } else if let first = next.next.first {
                DayHeading(date: first.date, prefix: nil)
                DayThread(items: next.next, extra: [], hero: hero, today: false)
            }
            AheadList(items: today.ahead.filter { $0.date != today.date && $0.date != shown }, tomorrow: next.date)
        }
    }
}

/// « Demain · jeudi 12 », above another day's thread.
private struct DayHeading: View {
    let date: String
    let prefix: String?

    var body: some View {
        let day = Day(date)?.weekdayAndNumber ?? date
        Text(prefix.map { "\($0) · \(day)" } ?? day.capitalizedFirst)
            .font(.footnote.weight(.semibold))
            .foregroundStyle(Palette.muted)
            .padding(.top, 8)
    }
}
