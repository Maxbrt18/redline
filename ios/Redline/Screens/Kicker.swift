import RedlineKit
import SwiftUI

/// The small line above a hero: a date, a week, a race.
struct Kicker: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote.weight(.semibold))
            .opacity(0.82)
    }
}

extension String {
    /// The first letter in capital, as a sentence starts.
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
