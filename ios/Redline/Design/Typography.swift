import SwiftUI

/// The six roles a piece of text takes. Every one is a system text style, so all follow the
/// reader's text size; figures are the sixth role, in Barlow (`FigureRole`).
enum TextRole {
    /// A screen's title outside the track.
    case title
    /// A section's head.
    case heading
    /// What a line says.
    case body
    /// What a line adds: a session's title in a list, a detail.
    case secondary
    /// The smallest words: a weekday, a unit, a note under a row.
    case caption

    var font: Font {
        switch self {
        case .title: .title2.bold()
        case .heading: .title3.bold()
        case .body: .body
        case .secondary: .subheadline
        case .caption: .footnote
        }
    }
}

/// The five sizes of a figure. Each grows with a text style and stops at a cap, so that a
/// figure never breaks over several lines at the accessibility sizes; its unit and its
/// words keep growing.
enum FigureRole {
    /// The one figure of a screen read at arm's length: the guided strength session.
    case display
    /// The figure of a screen's head.
    case hero
    /// The figure of a section or a card.
    case large
    /// A figure leading a row: a day's number.
    case medium
    /// A figure inside a line of text.
    case inline

    var size: CGFloat {
        switch self {
        case .display: 120
        case .hero: 80
        case .large: 40
        case .medium: 22
        case .inline: 17
        }
    }

    var style: Font.TextStyle {
        switch self {
        case .display, .hero: .largeTitle
        case .large: .title
        case .medium: .title3
        case .inline: .body
        }
    }

    /// The largest text size the figure follows.
    var cap: DynamicTypeSize {
        switch self {
        case .display, .hero: .xxLarge
        case .large: .xxxLarge
        case .medium: .accessibility1
        case .inline: .accessibility3
        }
    }
}

/// Whether a figure is done (solid, full weight) or planned (lighter, in a quieter colour
/// chosen by the caller).
enum FigureState {
    case done, planned

    var weight: Font.Weight { self == .done ? .bold : .semibold }
}

extension View {
    /// Text in one of the six roles.
    func textRole(_ role: TextRole) -> some View {
        font(role.font)
    }

    /// A figure in Barlow at one of the four sizes, with tabular digits, capped in size.
    func figure(_ role: FigureRole, _ state: FigureState = .done) -> some View {
        font(.figure(role.size, weight: state.weight, relativeTo: role.style))
            .monospacedDigit()
            .lineLimit(1)
            .dynamicTypeSize(...role.cap)
    }
}

/// The spacing scale: every padding and stack spacing is one of these.
enum Space {
    static let xxs: CGFloat = 4
    static let xs: CGFloat = 8
    static let s: CGFloat = 12
    static let m: CGFloat = 16
    static let l: CGFloat = 24
    static let xl: CGFloat = 32
    /// The page's side margin: a screen's title, hero and content all start on it.
    static let gutter: CGFloat = 20
    /// Between two sections of a page, and from the head of a page to its first section.
    static let section: CGFloat = xl
    /// From the track, or a paper screen's head, to the page.
    static let pageTop: CGFloat = l
}

/// The two corner radii: a surface a finger presses, and a mark in a chart.
enum Radius {
    static let surface: CGFloat = 12
    static let mark: CGFloat = 4
}
