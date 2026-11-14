#if DEBUG
import ActivityKit
import Foundation
import RedlineKit

/// The strength session's Live Activity without a session: the design catalogue plays a
/// short session through every phase, or holds one phase still, so that the lock screen
/// and the Dynamic Island can be judged. Debug builds only.
@MainActor
enum StrengthLiveDemo {
    /// One step of the demonstration: what the activity shows, and for how long.
    struct Step {
        let name: String
        let seconds: Int
        let state: (Date) -> StrengthLiveState
    }

    /// The session the demonstration follows; a real session ends it when it starts.
    static let sid = "demo"
    private static var playing: Task<Void, Never>?

    /// A rehearsal of a session: the preparation, a timed set, a rest, a set to count, then
    /// the end. Each countdown is the one the step lasts.
    static let steps: [Step] = [
        Step(name: "Préparation", seconds: 5) { now in
            StrengthLiveState(phase: .prepare, endsAt: now + 5, total: 5, figure: "0:05", label: "Prépare-toi",
                              detail: "puis 15 s à tenir", title: "Gainage latéral", subtitle: "Série 1 sur 3, côté droit",
                              side: "D", sets: sets(done: 0), exercise: 0, set: 0)
        },
        Step(name: "Série chronométrée", seconds: 15) { now in
            StrengthLiveState(phase: .work, endsAt: now + 15, total: 15, figure: "0:15", label: "À tenir",
                              detail: "sur 15 s", title: "Gainage latéral", subtitle: "Série 1 sur 3, côté droit",
                              side: "D", sets: sets(done: 0), exercise: 0, set: 0)
        },
        Step(name: "Repos", seconds: 15) { now in
            StrengthLiveState(phase: .rest, endsAt: now + 15, total: 15, figure: "0:15", label: "Repos",
                              detail: "sur 15 s", lead: "Ensuite", title: "Fentes bulgares",
                              subtitle: "Série 1 sur 3, 10 à 12 répétitions", sets: sets(done: 3), exercise: 1, set: 0)
        },
        Step(name: "Série à compter", seconds: 8) { _ in
            StrengthLiveState(phase: .ready, figure: "10–12", label: "Droite", detail: "répétitions",
                              title: "Fentes bulgares", subtitle: "Série 1 sur 3", side: "D",
                              sets: sets(done: 3), exercise: 1, set: 0)
        },
        Step(name: "Fin de séance", seconds: 0) { _ in
            StrengthLiveState(phase: .done, figure: "26", label: "min", detail: "4 exercices",
                              title: "Séance terminée", subtitle: "Bilan envoyé au coach", sets: sets(done: 12))
        },
    ]

    /// Plays every step in turn, then leaves the end on screen until `stop`.
    static func play() {
        stop()
        playing = Task {
            for step in steps {
                show(step)
                guard step.seconds > 0 else { return }
                try? await Task.sleep(for: .seconds(step.seconds))
                if Task.isCancelled { return }
            }
        }
    }

    /// Shows one step and holds it.
    static func hold(_ step: Step) {
        stop()
        show(step)
    }

    /// Ends the demonstration's activity at once.
    static func stop() {
        playing?.cancel()
        playing = nil
        Task {
            for activity in Activity<StrengthActivityAttributes>.activities where activity.attributes.sid == sid {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    /// The activity is allowed: the system's setting is on.
    static var enabled: Bool { ActivityAuthorizationInfo().areActivitiesEnabled }

    private static func show(_ step: Step) {
        let state = step.state(.now)
        let content = ActivityContent(state: state, staleDate: state.staleDate)
        if Activity<StrengthActivityAttributes>.activities.contains(where: { $0.attributes.sid == sid }) {
            Task {
                for activity in Activity<StrengthActivityAttributes>.activities where activity.attributes.sid == sid {
                    await activity.update(content)
                }
            }
        } else {
            _ = try? Activity.request(attributes: StrengthActivityAttributes(sid: sid), content: content)
        }
    }

    /// Four exercises of three sets, the first `done` sets filled.
    private static func sets(done: Int) -> [[Double]] {
        (0..<4).map { exercise in (0..<3).map { set in exercise * 3 + set < done ? 1 : 0 } }
    }
}
#endif
