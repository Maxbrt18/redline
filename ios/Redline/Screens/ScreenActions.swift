import SwiftUI

/// What a screen does once one of its questions is answered: it says so, then loads itself
/// again so that the next question takes its place. `NotebookScreen` sets it over everything
/// it draws; a view reads it from `\.answered` and calls it like a function.
nonisolated struct AnswerAction: Sendable {
    private let handler: @MainActor @Sendable (_ alert: Bool) async -> Void

    init(_ handler: @escaping @MainActor @Sendable (_ alert: Bool) async -> Void) {
        self.handler = handler
    }

    /// `alert` is true when the answer reached the coach, who replies in Messenger.
    @MainActor func callAsFunction(alert: Bool) async {
        await handler(alert)
    }

    /// The value outside any screen: the answer is kept by the server, but nothing moves on.
    static let unwired = AnswerAction { _ in
        assertionFailure("An answer was sent from a view that no NotebookScreen wraps: the screen does not reload.")
    }
}

/// Loads the screen again, after a change made from it. `NotebookScreen` sets it over
/// everything it draws; a view reads it from `\.reloadScreen` and calls it like a function.
nonisolated struct ReloadAction: Sendable {
    private let handler: @MainActor @Sendable () async -> Void

    init(_ handler: @escaping @MainActor @Sendable () async -> Void) {
        self.handler = handler
    }

    @MainActor func callAsFunction() async {
        await handler()
    }

    /// The value outside any screen: there is nothing to load again.
    static let unwired = ReloadAction {
        assertionFailure("A reload was asked from a view that no NotebookScreen wraps.")
    }
}

extension EnvironmentValues {
    @Entry var answered = AnswerAction.unwired
    @Entry var reloadScreen = ReloadAction.unwired
}
