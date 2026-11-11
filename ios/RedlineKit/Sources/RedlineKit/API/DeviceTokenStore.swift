import Foundation
import Security

/// Where the device token lives between launches.
public protocol DeviceTokenStore: Sendable {
    func read() -> String?
    func save(_ token: String) throws
    func delete()
}

/// The keychain, readable after the first unlock and never synced off the device: the
/// token is this device's, and a widget may read it while the phone is locked.
public struct KeychainDeviceTokenStore: DeviceTokenStore {
    private let service = "redline.notebook.device"
    private let account: String

    /// One token per server, so that an instance built for another host never sends it.
    public init(server: Server) {
        account = server.baseURL.absoluteString
    }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    public func read() -> String? {
        var item: CFTypeRef?
        let search = query.merging([kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]) { $1 }
        guard SecItemCopyMatching(search as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data
        else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func save(_ token: String) throws {
        delete()
        let item = query.merging([
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]) { $1 }
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }

    public func delete() {
        SecItemDelete(query as CFDictionary)
    }
}

public struct KeychainError: Error, Equatable {
    public let status: OSStatus
}
