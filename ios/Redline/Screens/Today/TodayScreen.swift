import RedlineKit
import SwiftUI

/// Today: the day's session in the head, the day's thread, what is coming, the landmarks.
struct TodayScreen: View {
    let session: NotebookSession
    /// Leads to another tab: the week's landmark to Week, the race's to Race.
    let openTab: (NotebookTab) -> Void

    var body: some View {
        NotebookScreen(title: NotebookTab.today.title, session: session, model: ScreenModel(.today)) { today in
            if let (kicker, item) = Self.hero(today) {
                TodayHero(kicker: kicker, item: item)
            } else {
                Kicker(text: Day(today.date)?.spelled().capitalizedFirst ?? today.date)
            }
        } content: { today in
            TodayContent(today: today, openTab: openTab)
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
    let openTab: (NotebookTab) -> Void

    var body: some View {
        let items = today.sessions.filter { $0.kind != "rest" }
        let hero = TodayScreen.hero(today)?.1
        let working = !items.isEmpty || !today.extra.isEmpty
        // A rest day already shows its next day in full: the list of what is coming skips it.
        let shown = working ? nil : RestDay.shownDay(today)
        VStack(alignment: .leading, spacing: Space.section) {
            VStack(alignment: .leading, spacing: Space.m) {
                if ["awaiting", "stale"].contains(today.plan.status) {
                    Label(today.plan.message, systemImage: "hourglass")
                        .textRole(.secondary)
                        .padding(Space.m)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Palette.panel, in: .rect(cornerRadius: Radius.surface))
                }
                if working {
                    DayThread(items: items, extra: today.extra, hero: hero, strength: today.strength,
                              asks: today.asks, date: today.date)
                } else {
                    RestDay(today: today, hero: hero)
                }
            }
            AheadList(items: today.ahead.filter { $0.date != today.date && $0.date != shown }, tomorrow: today.tomorrow.date)
            Landmarks(today: today, openTab: openTab)
        }
        .padding(.bottom, Space.l)
    }
}

/// A rest day: one line for today, then the next day that holds something.
private struct RestDay: View {
    let today: Notebook.TodayView
    let hero: Notebook.SessionItem?

    /// The day shown in full under today's rest: tomorrow when it holds a session,
    /// otherwise the next day that does.
    static func shownDay(_ today: Notebook.TodayView) -> String? {
        let next = today.tomorrow
        return next.sessions.contains { $0.kind != "rest" } ? next.date : next.next.first?.date
    }

    var body: some View {
        let next = today.tomorrow
        let real = next.sessions.filter { $0.kind != "rest" }
        VStack(alignment: .leading, spacing: Space.m) {
            // The day's questions come before the rest.
            DayThread(items: [], extra: [], asks: today.asks, date: today.date)
            if today.plan.status == "invalid" {
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text("Programme en préparation").textRole(.heading)
                    Text(today.plan.message).textRole(.secondary).foregroundStyle(Palette.muted)
                }
            } else {
                Text("\(Text("Repos aujourd’hui.").bold()) Rien à faire.").textRole(.body)
            }
            if !real.isEmpty {
                DayHeading(date: next.date, prefix: "Demain")
                DayThread(items: real, extra: [], hero: hero, today: false)
            } else if next.plan == "invalid" {
                Text("Le programme de la semaine arrive avec le bilan.").textRole(.secondary).foregroundStyle(Palette.muted)
            } else if let first = next.next.first {
                DayHeading(date: first.date, prefix: nil)
                DayThread(items: next.next, extra: [], hero: hero, today: false)
            }
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
            .font(TextRole.caption.font.weight(.semibold))
            .foregroundStyle(Palette.muted)
            .padding(.top, Space.xs)
    }
}
