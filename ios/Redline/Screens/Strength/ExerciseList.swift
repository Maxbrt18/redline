import RedlineKit
import SwiftUI

/// The day's strength exercises, folded under their count.
struct ExerciseList: View {
    let exercises: [(name: String, dose: String)]

    init(session: Notebook.StrengthSession) {
        exercises = session.exercises.map { ($0.name, $0.dose.text) }
    }

    var body: some View {
        if !exercises.isEmpty {
            DisclosureGroup("\(exercises.count) exercice\(exercises.count > 1 ? "s" : "")") {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(exercises.enumerated()), id: \.offset) { _, exercise in
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text(exercise.name).textRole(.body)
                            if !exercise.dose.isEmpty {
                                Text(exercise.dose).textRole(.secondary).foregroundStyle(Palette.muted)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, Space.s)
                    }
                }
            }
            .tint(Palette.ink)
        }
    }

    /// The slug and name of each exercise of a prescription, to name a report's lines.
    static func slugsAndNames(_ session: [String: JSONValue]) -> [(String, String)] {
        guard case .array(let list)? = session["exercises"] else { return [] }
        return list.compactMap { item in
            guard case .object(let exercise) = item, case .string(let slug)? = exercise["slug"] else { return nil }
            if case .string(let name)? = exercise["name"] { return (slug, name) }
            return nil
        }
    }

    /// The exercises of a prescription: its name (or slug) and its dose in words.
    static func exercises(_ session: [String: JSONValue]) -> [(name: String, dose: String)] {
        guard case .array(let list)? = session["exercises"] else { return [] }
        return list.compactMap { item in
            guard case .object(let exercise) = item else { return nil }
            let name: String? = if case .string(let name)? = exercise["name"] { name } else if case .string(let slug)? = exercise["slug"] { slug } else { nil }
            var dose = ""
            if case .object(let parts)? = exercise["dose"], case .string(let text)? = parts["text"] { dose = text }
            return name.map { ($0, dose) }
        }
    }
}
