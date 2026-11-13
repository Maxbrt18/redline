import ActivityKit
import AppIntents
import RedlineKit
import SwiftUI
import WidgetKit

/// The guided strength session on the lock screen and in the Dynamic Island. Three floors
/// that never move: what runs now, a line, then what comes next. Everything is ink; red
/// is the effort, the main action and a rest that ran out.
struct StrengthLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: StrengthActivityAttributes.self) { context in
            LockScreen(live: Live(context.state, stale: context.isStale))
                .activityBackgroundTint(Palette.bg)
                .activitySystemActionForegroundColor(Palette.ink)
        } dynamicIsland: { context in
            let live = Live(context.state, stale: context.isStale)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { IslandLeading(live: live) }
                DynamicIslandExpandedRegion(.trailing) { IslandTrailing(live: live) }
                DynamicIslandExpandedRegion(.bottom) { IslandBottom(live: live) }
            } compactLeading: {
                LiveSymbol(live: live, size: 20)
            } compactTrailing: {
                CompactValue(live: live)
            } minimal: {
                LiveSymbol(live: live, size: 24)
            }
            .keylineTint(live.tint)
        }
    }
}

/// The state as drawn: the stale flag turns a finished rest into a call to go on.
struct Live {
    let state: StrengthLiveState
    let stale: Bool

    init(_ state: StrengthLiveState, stale: Bool) {
        self.state = state
        self.stale = stale && state.staleDate != nil
    }

    /// A rest ran out with the app away: it counts the delay since.
    var overdue: Bool { stale && state.phase == .rest }
    /// A timed set ran out with the app away: the app asks what was done.
    var ranOut: Bool { stale && state.phase != .rest }
    var counting: Bool { state.counts && !stale }
    var frozen: Bool { state.pausedLeft != nil || ranOut }

    var tint: Color {
        if overdue { return Palette.accent }
        if frozen { return Palette.muted }
        return state.phase == .work ? Palette.accent : Palette.ink
    }

    var label: String {
        if overdue { return "Repos fini" }
        if ranOut { return "À confirmer" }
        return state.label
    }

    var detail: String {
        if overdue { return "à toi" }
        if ranOut { return "dans l’app" }
        return state.detail
    }

    var lead: String? { overdue ? "À toi" : state.lead }

    /// The countdown's dates, once ordered: a range never runs backwards.
    var interval: ClosedRange<Date>? {
        guard let end = state.endsAt else { return nil }
        return min(state.countdownStart ?? end, end)...end
    }

    var actions: [LiveButton] {
        guard !stale, !state.interrupted else { return [] }
        let paused = state.pausedLeft != nil
        switch state.phase {
        case .rest: return [paused ? .resume : .pause, .skipRest]
        case .work where state.endsAt != nil: return [paused ? .resume : .pause]
        case .prepare: return [.cancelStart]
        default: return []
        }
    }
}

/// A button of the activity, run by the app through `StrengthLiveIntent`.
enum LiveButton: Hashable {
    case pause, resume, skipRest, cancelStart

    var action: StrengthLiveAction {
        switch self {
        case .pause: .pause
        case .resume: .resume
        case .skipRest: .skipRest
        case .cancelStart: .cancelStart
        }
    }

    var symbol: String {
        switch self {
        case .pause: "pause.fill"
        case .resume: "play.fill"
        case .skipRest: "forward.end.fill"
        case .cancelStart: "xmark"
        }
    }

    var words: String {
        switch self {
        case .pause: "Mettre en pause"
        case .resume: "Reprendre"
        case .skipRest: "Passer le repos"
        case .cancelStart: "Annuler le départ"
        }
    }

    var primary: Bool { self == .resume }
}

// MARK: - The figure

/// The one figure: a countdown the system moves on, the delay since a rest ran out, or a
/// still figure. A timer's text takes all the width it is given, so it gets its own.
struct LiveFigure: View {
    let live: Live
    let size: CGFloat
    var alignment: Alignment = .leading

    var body: some View {
        Group {
            if live.overdue, let end = live.state.endsAt {
                HStack(spacing: 0) {
                    Text(verbatim: "+")
                    Text(timerInterval: end...Date.distantFuture, countsDown: false)
                }
                .frame(width: size * 1.75, alignment: alignment)
            } else if live.counting, let interval = live.interval {
                Text(timerInterval: interval, countsDown: true)
                    .frame(width: size * 1.45, alignment: alignment)
            } else if let since = live.state.startedAt, live.state.endsAt == nil, live.state.pausedLeft == nil {
                Text(timerInterval: since...Date.distantFuture, countsDown: false)
                    .frame(width: size * 1.45, alignment: alignment)
            } else {
                Text(live.state.figure)
            }
        }
        .font(.figure(size))
        .monospacedDigit()
        .lineLimit(1)
        .minimumScaleFactor(0.6)
        // The face's line is far taller than its digits: the box hugs the digits, so the
        // line below sits where the eye expects it.
        .frame(height: size * 0.82)
        .foregroundStyle(live.overdue ? Palette.accent : live.frozen ? Palette.muted : Palette.ink)
        .multilineTextAlignment(alignment == .trailing ? .trailing : .leading)
    }
}

// MARK: - The line

/// A countdown's line empties; without a timer, the line is the session, set by set.
struct LiveLine: View {
    let live: Live

    var body: some View {
        Group {
            if live.counting, let interval = live.interval {
                ProgressView(timerInterval: interval, countsDown: true) {
                    EmptyView()
                } currentValueLabel: {
                    EmptyView()
                }
                .progressViewStyle(.linear)
                .tint(live.state.phase == .work ? Palette.accent : Palette.data)
            } else if let left = live.state.pausedLeft, let total = live.state.total, total > 0, !live.stale {
                ProgressView(value: min(left, Double(total)), total: Double(total))
                    .progressViewStyle(.linear)
                    .tint(Palette.muted)
            } else if live.state.endsAt != nil || live.state.startedAt != nil {
                Capsule().fill(Palette.ghost).frame(height: 4)
            } else {
                LiveMarks(state: live.state)
            }
        }
        .accessibilityHidden(true)
    }
}

/// One mark per set, grouped by exercise; a set with two sides fills by halves.
struct LiveMarks: View {
    let state: StrengthLiveState

    var body: some View {
        HStack(spacing: 7) {
            ForEach(Array(state.sets.enumerated()), id: \.offset) { exercise, sets in
                HStack(spacing: 3) {
                    ForEach(Array(sets.enumerated()), id: \.offset) { set, fill in
                        let current = state.exercise == exercise && state.set == set
                        Capsule()
                            .fill(current ? Palette.muted : Palette.ghost)
                            .overlay(alignment: .leading) {
                                GeometryReader { proxy in
                                    Capsule().fill(Palette.ink).frame(width: proxy.size.width * fill)
                                }
                            }
                    }
                }
                .frame(maxWidth: .infinity)
                .layoutPriority(Double(sets.count))
            }
        }
        .frame(height: 4)
    }
}

// MARK: - Words

/// What runs now, beside the figure: « REPOS » over « sur 1:00 ».
struct LiveLabel: View {
    let live: Live

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(live.label.uppercased())
                .font(.caption.weight(.semibold))
                .tracking(0.8)
                .foregroundStyle(live.overdue ? Palette.accent : Palette.ink)
            if !live.detail.isEmpty {
                Text(live.detail).font(.footnote).foregroundStyle(Palette.muted)
            }
        }
        .lineLimit(1)
    }
}

/// What comes next, under the line: the exercise, then its set and its dose.
struct LiveNext: View {
    let live: Live

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Group {
                if let lead = live.lead {
                    Text("\(Text("\(lead) · ").fontWeight(.regular).foregroundStyle(Palette.muted))\(live.state.title)")
                } else {
                    Text(live.state.title)
                }
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Palette.ink)
            Text(live.state.subtitle).font(.footnote).foregroundStyle(Palette.muted)
        }
        .lineLimit(1)
        .truncationMode(.tail)
    }
}

/// The buttons, each a round target of `size` points.
struct LiveButtons: View {
    let live: Live
    let size: CGFloat

    var body: some View {
        HStack(spacing: size > 40 ? 10 : 8) {
            ForEach(live.actions, id: \.self) { button in
                Button(intent: StrengthLiveIntent(button.action)) {
                    Image(systemName: button.symbol)
                        .font(.system(size: size * 0.38, weight: .semibold))
                        .foregroundStyle(button.primary ? Palette.bg : Palette.ink)
                        .frame(width: size, height: size)
                        .background(Circle().fill(button.primary ? Palette.accent : Palette.panel))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(button.words)
            }
        }
    }
}

// MARK: - Lock screen

struct LockScreen: View {
    let live: Live

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                LiveFigure(live: live, size: 56)
                LiveLabel(live: live)
                Spacer(minLength: 0)
                LiveButtons(live: live, size: 44)
            }
            .frame(minHeight: 46)
            LiveLine(live: live)
                .padding(.top, 14)
                .padding(.bottom, 11)
            LiveNext(live: live)
        }
        .padding(.horizontal, 18)
        .padding(.top, 16)
        .padding(.bottom, 15)
    }
}

// MARK: - Dynamic Island

/// Left of the camera, in a box that stops before it: the buttons, or what runs now.
struct IslandLeading: View {
    let live: Live

    var body: some View {
        Group {
            if !live.actions.isEmpty {
                LiveButtons(live: live, size: 38)
            } else if live.overdue {
                Circle().strokeBorder(Palette.accent, lineWidth: 3).frame(width: 34, height: 34)
            } else {
                LiveLabel(live: live)
            }
        }
        .frame(width: 94, alignment: .leading)
        .padding(.leading, 4)
    }
}

/// Right of the camera: the figure, which shrinks rather than pass under it.
struct IslandTrailing: View {
    let live: Live

    var body: some View {
        LiveFigure(live: live, size: 40, alignment: .trailing)
            .frame(width: 94, alignment: .trailing)
            .padding(.trailing, 4)
    }
}

struct IslandBottom: View {
    let live: Live

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            LiveLine(live: live)
            LiveNext(live: live)
        }
        .padding(.horizontal, 4)
        .padding(.top, 6)
    }
}

/// The compact island's left side and the minimal one: a ring that empties, the side, a
/// pause, a full ring once a rest ran out, a check at the end. One tint with the value.
struct LiveSymbol: View {
    let live: Live
    let size: CGFloat

    var body: some View {
        Group {
            if live.overdue {
                Circle().strokeBorder(live.tint, lineWidth: size / 8)
            } else if live.counting, let interval = live.interval {
                ProgressView(timerInterval: interval, countsDown: true) {
                    EmptyView()
                } currentValueLabel: {
                    EmptyView()
                }
                .progressViewStyle(.circular)
                .tint(live.tint)
            } else if live.frozen {
                Image(systemName: "pause.fill").font(.system(size: size * 0.6, weight: .bold))
            } else if live.state.phase == .done {
                Image(systemName: "checkmark.circle.fill").font(.system(size: size * 0.95))
            } else if let side = live.state.side {
                Text(side)
                    .font(.system(size: size * 0.6, weight: .bold))
                    .frame(width: size, height: size)
                    .background(Circle().fill(live.tint.opacity(0.22)))
            } else {
                Image(systemName: "figure.strengthtraining.traditional").font(.system(size: size * 0.7, weight: .semibold))
            }
        }
        .foregroundStyle(live.tint)
        .frame(width: size, height: size)
        .accessibilityLabel(live.label)
    }
}

/// The compact island's right side: the time, in the symbol's tint.
struct CompactValue: View {
    let live: Live

    var body: some View {
        Group {
            if live.overdue {
                Text("À toi")
            } else if live.counting, let interval = live.interval {
                Text(timerInterval: interval, countsDown: true)
                    .frame(width: 40, alignment: .trailing)
            } else if live.state.phase == .done {
                Text("\(live.state.figure) min")
            } else {
                Text(live.state.figure)
            }
        }
        .font(.system(size: 15, weight: .semibold))
        .monospacedDigit()
        .lineLimit(1)
        .foregroundStyle(live.tint)
        .multilineTextAlignment(.trailing)
    }
}
