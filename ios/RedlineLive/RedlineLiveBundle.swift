import SwiftUI
import WidgetKit

/// The widget extension: for now, the strength session's Live Activity alone.
@main
struct RedlineLiveBundle: WidgetBundle {
    var body: some Widget {
        StrengthLiveActivity()
    }
}
