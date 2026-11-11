import Foundation
import Testing

/// Decodes a fixture written by tests/ios_contract.py, encodes it back, and expects the
/// same JSON: a key the model does not declare would vanish on the way and fail here.
func expectRoundTrip<Value: Codable>(_ type: Value.Type, fixture name: String) throws {
    let url = try #require(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
    let original = try Data(contentsOf: url)
    let decoded = try JSONDecoder().decode(type, from: original)
    let encoded = try JSONEncoder().encode(decoded)
    let expected = try JSONSerialization.jsonObject(with: original) as? NSObject
    let actual = try JSONSerialization.jsonObject(with: encoded) as? NSObject
    #expect(expected != nil && expected == actual, "\(name).json changes on a round trip")
}
