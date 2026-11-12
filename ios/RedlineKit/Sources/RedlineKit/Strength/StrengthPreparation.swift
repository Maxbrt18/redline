import Foundation

/// What to get ready before the session, and what changed since the last one of the
/// same routine.
public struct StrengthPreparation: Hashable, Sendable {
    public enum Item: Hashable, Sendable {
        case equipment(String)
        /// Dumbbells to pick up: a load of zero kilograms is body weight and asks for none.
        case dumbbells(Notebook.StrengthLoad)
    }

    /// What differs in an exercise kept from the previous prescription.
    public enum Aspect: Hashable, Sendable {
        case dose, load, cue, equipment
    }

    public enum Change: Hashable, Sendable {
        /// Not in the previous prescription.
        case added(Notebook.StrengthExercise)
        case changed(Notebook.StrengthExercise, Set<Aspect>)
        /// In the previous prescription, not in today's; its name as it was then.
        case removed(name: String)
    }

    /// Without repeats, in the order of the exercises; none from an exercise taken out.
    public let items: [Item]
    /// Nil without a previous prescription; empty when it was the same.
    public let changes: [Change]?

    public init(run: StrengthRun, previous: [String: JSONValue]?) {
        var items: [Item] = []
        for (index, exercise) in run.exercises.enumerated() where !run.step(index).gated {
            var own = exercise.equipment.map(Item.equipment)
            if let load = exercise.load, load.kg > 0 {
                own.append(.dumbbells(load))
            }
            items += own.filter { !items.contains($0) }
        }
        self.items = items
        changes = previous.map { Self.changes(run.exercises, previous: $0) }
    }

    /// Exercises are matched by slug. The previous prescription is read as stored: an
    /// older one may lack a key, which then counts as a change.
    private static func changes(_ exercises: [Notebook.StrengthExercise], previous: [String: JSONValue]) -> [Change] {
        guard case .array(let listed)? = previous["exercises"] else { return [] }
        let before = listed.compactMap { value -> [String: JSONValue]? in
            if case .object(let object) = value { return object }
            return nil
        }
        var bySlug: [String: [String: JSONValue]] = [:]
        for exercise in before {
            if case .string(let slug)? = exercise["slug"] {
                bySlug[slug] = exercise
            }
        }
        var changes: [Change] = []
        for exercise in exercises {
            guard let old = bySlug[exercise.slug] else {
                changes.append(.added(exercise))
                continue
            }
            let now = json(exercise)
            var aspects: Set<Aspect> = []
            if old["dose"] != now["dose"] { aspects.insert(.dose) }
            if (old["load"] ?? .null) != (now["load"] ?? .null) { aspects.insert(.load) }
            if old["cue"] != now["cue"] { aspects.insert(.cue) }
            if (old["equipment"] ?? .array([])) != (now["equipment"] ?? .array([])) { aspects.insert(.equipment) }
            if !aspects.isEmpty {
                changes.append(.changed(exercise, aspects))
            }
        }
        let slugs = Set(exercises.map(\.slug))
        for exercise in before {
            guard case .string(let slug)? = exercise["slug"], !slugs.contains(slug) else { continue }
            if case .string(let name)? = exercise["name"] {
                changes.append(.removed(name: name))
            } else {
                changes.append(.removed(name: slug))
            }
        }
        return changes
    }

    private static func json(_ exercise: Notebook.StrengthExercise) -> [String: JSONValue] {
        guard let data = try? JSONEncoder().encode(exercise),
              let object = try? JSONDecoder().decode([String: JSONValue].self, from: data)
        else { return [:] }
        return object
    }
}
