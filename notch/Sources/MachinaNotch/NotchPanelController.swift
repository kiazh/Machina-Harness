import AppKit
import ServiceManagement
import SwiftUI

@MainActor
final class NotchPanelController: NSObject {
    private let panel: NSPanel
    private let model = NotchModel()
    private var statusItem: NSStatusItem?
    private var collapseWorkItem: DispatchWorkItem?

    private let collapsedSize = NSSize(width: 236, height: 38)
    private let expandedSize = NSSize(width: 400, height: 560)

    override init() {
        panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: collapsedSize),
            styleMask: [.borderless, .nonactivatingPanel],
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
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.animationBehavior = .utilityWindow
        panel.contentView = NSHostingView(
            rootView: NotchView(model: model, controller: self)
        )
        installStatusItem()
        position(size: collapsedSize)
    }

    func show() {
        panel.orderFrontRegardless()
        model.start()
    }

    func expand() {
        collapseWorkItem?.cancel()
        guard !model.expanded else { return }
        model.expanded = true
        animate(to: expandedSize)
        SoundPlayer.shared.play(.wake)
    }

    func collapse() {
        collapseWorkItem?.cancel()
        guard model.expanded else { return }
        model.expanded = false
        animate(to: collapsedSize)
    }

    func toggle() {
        model.expanded ? collapse() : expand()
    }

    func collapseSoon() {
        guard model.expanded else { return }
        let work = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.collapse() }
        }
        collapseWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45, execute: work)
    }

    private func animate(to size: NSSize) {
        let target = frame(for: size)
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.22
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
        let notchTop = visible.maxY
        return NSRect(
            x: visible.midX - size.width / 2,
            y: notchTop - size.height - 1,
            width: size.width,
            height: size.height
        )
    }

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "◉"
        let menu = NSMenu()
        let toggle = NSMenuItem(title: "Show Buddy", action: #selector(toggleFromMenu), keyEquivalent: "")
        toggle.target = self
        menu.addItem(toggle)
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
