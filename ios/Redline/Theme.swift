import SwiftUI
import UIKit

/// The notebook's theme: the phone's, or one pinned here, kept on this device.
enum Theme: String, CaseIterable {
    case auto, light, dark

    static let storageKey = "theme"

    /// Pins the windows' style, as the system setting would. A `preferredColorScheme`
    /// would also pin the status bar, which the track needs white even in light mode.
    func apply() {
        let style: UIUserInterfaceStyle = switch self {
        case .auto: .unspecified
        case .light: .light
        case .dark: .dark
        }
        for scene in UIApplication.shared.connectedScenes {
            for window in (scene as? UIWindowScene)?.windows ?? [] {
                window.overrideUserInterfaceStyle = style
            }
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
