import SwiftUI

/// The notebook's theme: the phone's, or one pinned here, kept on this device.
enum Theme: String, CaseIterable {
    case auto, light, dark

    static let storageKey = "theme"

    var colorScheme: ColorScheme? {
        switch self {
        case .auto: nil
        case .light: .light
        case .dark: .dark
        }
    }

    var label: LocalizedStringKey {
        switch self {
        case .auto: "Auto"
        case .light: "Clair"
        case .dark: "Sombre"
        }
    }
}
