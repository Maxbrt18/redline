import RedlineKit
import SwiftUI

/// The week's seven days as bars: done solid, planned in dots, today's letter in the red
/// line. On the track the bars turn white. Seven columns cannot grow far: the words stop at
/// the first accessibility size, and the bars keep their height whatever the text size.
struct WeekBars: View {
    let days: [Notebook.StripDay]
    var height: CGFloat = 52
    /// Under each letter, the day's session in a word (or « repos »).
    var abbreviations = false
    @Environment(\.onTrack) private var onTrack

    var body: some View {
        let peak = max(1, days.map { max($0.doneKm ?? 0, $0.plannedKm ?? 0) }.max() ?? 1)
        HStack(alignment: .bottom, spacing: 6) {
            ForEach(days, id: \.date) { day in
                VStack(spacing: Space.xxs) {
                    Text(label(day)).font(.caption2.weight(.semibold))
                        .foregroundStyle(day.doneKm != nil ? ink : quiet)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    bar(day, peak: peak).frame(height: height, alignment: .bottom)
                    Text(day.letter).font(.caption2.weight(day.today ? .bold : .regular))
                        .foregroundStyle(day.today ? (onTrack ? Palette.onTrack : Palette.accent) : muted)
                        .underline(day.today && onTrack)
                    if abbreviations {
                        Text(day.abbr.isEmpty ? (day.rest ? "repos" : "—") : day.abbr)
                            .font(.caption2).foregroundStyle(quiet).lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
                .frame(maxWidth: .infinity)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
        .accessibilityHidden(true)
    }

    private var ink: Color { onTrack ? Palette.onTrack : Palette.ink }
    private var muted: Color { onTrack ? Palette.onTrack.opacity(0.8) : Palette.muted }
    private var quiet: Color { onTrack ? Palette.onTrack.opacity(0.75) : Palette.muted }

    private func label(_ day: Notebook.StripDay) -> String {
        if let done = day.doneKm, done > 0 { return Numbers.text(done) }
        if let planned = day.plannedKm { return Numbers.text(planned) }
        return day.plannedLabel ?? ""
    }

    @ViewBuilder private func bar(_ day: Notebook.StripDay, peak: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: Radius.mark)
        if let done = day.doneKm, done > 0 {
            shape.fill(onTrack ? Palette.onTrack : Palette.data).frame(width: 18, height: max(4, height * done / peak))
        } else if day.plannedKm != nil || day.plannedLabel != nil {
            DotPattern(color: onTrack ? Palette.onTrack.opacity(0.85) : Palette.data.opacity(0.75), spacing: 4, radius: 1.05)
                .frame(width: 18, height: day.plannedKm.map { max(4, height * $0 / peak) } ?? height / 2)
                .clipShape(shape)
        } else {
            shape.fill(onTrack ? Palette.onTrack.opacity(0.5) : Palette.ghost).frame(width: 18, height: 2)
        }
    }
}
