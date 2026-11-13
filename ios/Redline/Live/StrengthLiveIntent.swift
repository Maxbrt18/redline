import AppIntents

// Shared with the widget extension, which draws the buttons; the system runs the intent
// in the app's process, where the session lives.

/// What the Live Activity's buttons do: only what is decided without looking. Banking a
/// set takes a count, so it stays in the app.
enum StrengthLiveAction: String, Sendable {
    case pause, resume, skipRest, cancelStart
}

/// Where the app takes the buttons' actions; installed at launch, never set in the extension.
@MainActor
enum StrengthLiveActions {
    static var perform: (@MainActor @Sendable (StrengthLiveAction) async -> Void)?

    static func run(_ action: StrengthLiveAction) async {
        await perform?(action)
    }
}

/// One button of the strength session's Live Activity.
struct StrengthLiveIntent: LiveActivityIntent {
    static let title: LocalizedStringResource = "Séance de renfo"
    static let isDiscoverable = false

    @Parameter(title: "Action")
    var action: String

    init() {}

    init(_ action: StrengthLiveAction) {
        self.action = action.rawValue
    }

    func perform() async throws -> some IntentResult {
        if let action = StrengthLiveAction(rawValue: action) {
            await StrengthLiveActions.run(action)
        }
        return .result()
    }
}
