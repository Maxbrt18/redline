import RedlineKit
import SwiftUI

/// Today: the day's card at the head, which says where the day stands and holds the one
/// thing to do now, then the rest of the day, the week and the landmarks.
struct TodayScreen: View {
    let session: NotebookSession
    /// Leads to another tab: the week's strip to Week, the race to Race.
    let openTab: (NotebookTab) -> Void

    var body: some View {
        NotebookScreen(title: NotebookTab.today.title, session: session, model: ScreenModel(.today), card: true) { today in
            DayCard(today: today, layout: TodayLayout(today))
        } content: { today in
            TodayContent(today: today, layout: TodayLayout(today), openTab: openTab)
        }
    }
}
