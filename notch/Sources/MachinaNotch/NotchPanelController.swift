import AppKit
import ServiceManagement
import SwiftUI

private final class NotchPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class NotchPanelController: NSObject {
    private let panel: NotchPanel
    private let model = NotchModel()
    private var statusItem: NSStatusItem?
    private var collapseWorkItem: DispatchWorkItem?
    private var resignObserver: NSObjectProtocol?

    private let collapsedSize = NSSize(width: 190, height: 34)
    private let expandedSize = NSSize(width: 420, height: 560)

    override init() {
        panel = NotchPanel(
            contentRect: NSRect(origin: .zero, size: collapsedSize),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        super.init()
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovable = false
        panel.isMovableByWindowBackground = false
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = false
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.animationBehavior = .utilityWindow

        let hosting = NSHostingView(rootView: NotchView(model: model, controller: self))
        hosting.wantsLayer = true
        hosting.layer?.backgroundColor = NSColor.clear.cgColor
        hosting.layer?.isOpaque = false
        hosting.layer?.masksToBounds = true
        panel.contentView = hosting
        panel.contentView?.wantsLayer = true
        panel.contentView?.layer?.backgroundColor = NSColor.clear.cgColor

        installStatusItem()
        position(size: collapsedSize)
        applyCornerRadius(expanded: false)

        resignObserver = NotificationCenter.default.addObserver(
            forName: NSWindow.didResignKeyNotification,
            object: panel,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.model.expanded else { return }
                self.collapse()
            }
        }
    }

    func show() {
        panel.orderFrontRegardless()
        model.start()
    }

    func hide() {
        collapse()
        panel.orderOut(nil)
    }

    func expand(activate: Bool = true) {
        collapseWorkItem?.cancel()
        guard !model.expanded else {
            if activate { self.activate() }
            return
        }
        model.expanded = true
        applyCornerRadius(expanded: true)
        if activate {
            self.activate()
        } else {
            panel.orderFrontRegardless()
        }
        animate(to: expandedSize)
        SoundPlayer.shared.play(.wake)
    }

    func activate() {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }

    var isKey: Bool { panel.isKeyWindow }

    func collapse() {
        collapseWorkItem?.cancel()
        guard model.expanded else { return }
        model.expanded = false
        applyCornerRadius(expanded: false)
        animate(to: collapsedSize)
        panel.orderFrontRegardless()
    }

    func toggle() {
        model.expanded ? collapse() : expand()
    }

    func collapseSoonIfNotKey() {
        guard model.expanded, !panel.isKeyWindow else { return }
        collapseSoon()
    }

    func collapseSoon() {
        guard model.expanded else { return }
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.collapse() }
        }
        collapseWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45, execute: work)
    }

    private func applyCornerRadius(expanded: Bool) {
        let radius = expanded ? 26.0 : collapsedSize.height / 2
        panel.contentView?.layer?.cornerRadius = radius
        panel.contentView?.layer?.masksToBounds = true
    }

    private func animate(to size: NSSize) {
        let target = frame(for: size)
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.24
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().setFrame(target, display: true)
        }
    }

    private func position(size: NSSize) {
        panel.setFrame(frame(for: size), display: true)
    }

    private func frame(for size: NSSize) -> NSRect {
        let screen = NSScreen.main ?? NSScreen.screens.first
        guard let screen else {
            return NSRect(origin: .zero, size: size)
        }
        let visible = screen.frame
        return NSRect(
            x: visible.midX - size.width / 2,
            y: visible.maxY - size.height,
            width: size.width,
            height: size.height
        )
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "◉"
        let menu = NSMenu()
        let toggle = NSMenuItem(title: "Show Maki", action: #selector(toggleFromMenu), keyEquivalent: "")
        toggle.target = self
        menu.addItem(toggle)
        let hide = NSMenuItem(title: "Hide Maki", action: #selector(hideFromMenu), keyEquivalent: "")
        hide.target = self
        menu.addItem(hide)
        let login = NSMenuItem(title: "Launch at Login", action: #selector(toggleLoginItem), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Machina Notch", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        statusItem = item
    }

    @objc private func toggleFromMenu() {
        Task { @MainActor in
            if panel.isVisible {
                toggle()
            } else {
                show()
            }
        }
    }

    @objc private func hideFromMenu() {
        Task { @MainActor in self.hide() }
    }

    @objc private func toggleLoginItem() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            NSSound.beep()
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}
