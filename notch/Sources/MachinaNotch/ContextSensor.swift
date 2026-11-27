import AppKit
import ApplicationServices

/// Best-effort frontmost-app context. Window title and selected text need the
/// Accessibility permission; when it is absent the sensor simply returns less.
@MainActor
enum ContextSensor {
    static func snapshot() -> [String: String] {
        guard let app = NSWorkspace.shared.frontmostApplication else { return [:] }
        var result: [String: String] = [
            "app": app.localizedName ?? "",
            "bundle": app.bundleIdentifier ?? "",
        ]
        guard AXIsProcessTrusted() else { return result }

        let application = AXUIElementCreateApplication(app.processIdentifier)
        if let window = copy(application, kAXFocusedWindowAttribute) {
            if let title = string(window, kAXTitleAttribute), !title.isEmpty {
                result["window"] = title
            }
        }
        let system = AXUIElementCreateSystemWide()
        if let focused = copy(system, kAXFocusedUIElementAttribute) {
            if let selection = string(focused, kAXSelectedTextAttribute), !selection.isEmpty {
                result["selection"] = String(selection.prefix(2_000))
            }
        }
        return result
    }

    private static func copy(_ element: AXUIElement, _ attribute: String) -> AXUIElement? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
              let value else { return nil }
        return (value as! AXUIElement)
    }

    private static func string(_ element: AXUIElement, _ attribute: String) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else {
            return nil
        }
        return value as? String
    }
}
