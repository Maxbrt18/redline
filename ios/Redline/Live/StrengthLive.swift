import ActivityKit
import RedlineKit
import UIKit

/// The strength session's Live Activity, kept in step with the session: started at the
/// first gesture, updated at each change, ended when the session's screen closes. Its
/// buttons act on the session through `StrengthLiveIntent`.
@MainActor
enum StrengthLive {
    /// The session on screen, which the buttons act on first.
    static weak var model: StrengthModel?

    /// How long the finished session stays on the lock screen once its screen is closed.
    static let doneLingers: TimeInterval = 15 * 60

    /// Called once at launch, before any button can be pressed.
    static func install() {
        StrengthLiveActions.perform = { action in await handle(action) }
    }

    /// Starts, updates or leaves the activity so that it shows `run`; a single activity at
    /// a time, the one of the session on screen.
    static func sync(_ run: StrengthRun?) {
        guard let run, let state = StrengthLiveState(run: run, at: .now) else { return }
        let content = ActivityContent(state: state, staleDate: state.staleDate)
        let sid = run.session.sid
        let activities = Activity<StrengthActivityAttributes>.activities
        // An activity starts only while the app is in front, on a gesture of the athlete.
        if !activities.contains(where: { $0.attributes.sid == sid }), ActivityAuthorizationInfo().areActivitiesEnabled {
            _ = try? Activity.request(attributes: StrengthActivityAttributes(sid: sid), content: content)
        }
        Task { await settle(sid: sid, content: content) }
    }

    /// The session's screen closes: a finished session stays a while, anything else goes.
    static func end(_ run: StrengthRun?) {
        let state = run.flatMap { StrengthLiveState(run: $0, at: .now) }
        Task {
            for activity in Activity<StrengthActivityAttributes>.activities {
                if let state, state.phase == .done {
                    await activity.end(ActivityContent(state: state, staleDate: nil),
                                       dismissalPolicy: .after(.now + doneLingers))
                } else {
                    await activity.end(nil, dismissalPolicy: .immediate)
                }
            }
        }
    }

    /// Shows `content` on the session's activity and ends any other.
    private static func settle(sid: String, content: ActivityContent<StrengthLiveState>) async {
        for activity in Activity<StrengthActivityAttributes>.activities {
            if activity.attributes.sid != sid {
                await activity.end(nil, dismissalPolicy: .immediate)
            } else if activity.content.state != content.state || activity.content.staleDate != content.staleDate {
                await activity.update(content)
            }
        }
    }

    /// A button pressed on the lock screen or in the Dynamic Island. The session on screen
    /// takes it; without one, the app was closed and acts on what the device kept.
    static func handle(_ action: StrengthLiveAction) async {
        if let model {
            model.update { apply(action, to: &$0) }
            if UIApplication.shared.applicationState != .active {
                StrengthNotifier.withdraw()
                StrengthNotifier.schedule(for: model.run)
            }
            return
        }
        guard let sid = Activity<StrengthActivityAttributes>.activities.first?.attributes.sid,
              let server = try? Server(bundle: .main) else { return }
        let store = FileStrengthProgressStore(server: server)
        guard let opened = store.loadOpened(sid: sid) else { return }
        var run = StrengthRun(session: opened.view.session, zones: opened.view.zones, saved: store.load(sid: sid))
        apply(action, to: &run)
        try? store.save(run.progress, sid: sid)
        sync(run)
    }

    private static func apply(_ action: StrengthLiveAction, to run: inout StrengthRun) {
        let kind = run.progress.timer?.kind
        switch action {
        case .pause: run.pause(at: .now)
        case .resume: run.resume(at: .now)
        case .skipRest where kind == .rest, .cancelStart where kind == .prepare: run.stopTimer()
        case .skipRest, .cancelStart: break
        }
    }
}
