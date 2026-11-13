import Foundation

public extension String {
    /// The first letter in capital, as a sentence starts.
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
