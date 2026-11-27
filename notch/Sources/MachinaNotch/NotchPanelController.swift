import AppKit
import Combine
import ServiceManagement
import SwiftUI

private final class NotchPanel: NSPanel {
    /// Key status is only granted once the island is expanded. Hovering must
    /// never steal focus from the frontmost app.
    ///
    /// AppKit reads this from outside a Swift task (menu tracking restores the
    /// previous key window via a delayed perform), so the override must be
    /// nonisolated or Swift 6 injects a MainActor executor check that crashes.
    nonisolated(unsafe) var acceptsKeyStatus = false

    nonisolated override var canBecomeKey: Bool { acceptsKeyStatus }
    nonisolated override var canBecomeMain: Bool { false }
}

private final class HoverTrackingView: NSView {
    nonisolated(unsafe) var onHoverEvent: (@Sendable (Bool) -> Void)?

    init(frame: NSRect, onHoverEvent: @escaping @Sendable (Bool) -> Void) {
        self.onHoverEvent = onHoverEvent
        super.init(frame: frame)
        let area = NSTrackingArea(
            rect: .zero,
            options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    // AppKit can deliver tracking callbacks outside a Swift task. These
    // overrides must be nonisolated; they only call a Sendable closure that
    // hops to the main runloop and never touches SwiftUI state themselves.
    nonisolated override func mouseEntered(with event: NSEvent) {
        onHoverEvent?(true)
    }

    nonisolated override func mouseExited(with event: NSEvent) {
        onHoverEvent?(false)
    }

    nonisolated override func mouseMoved(with event: NSEvent) {
        onHoverEvent?(true)
    }

    /// Keep clicks flowing to the SwiftUI hosting view underneath.
    nonisolated override func hitTest(_ point: NSPoint) -> NSView? {
        nil
    }
}

@MainActor
final class NotchPanelController: NSObject, NSMenuDelegate {
    private let panel: NotchPanel
    private let model = NotchModel()
    private var statusItem: NSStatusItem?
    private var statusMenu: NSMenu?
    private var screen: NSScreen?
    private var metrics = IslandMetrics()
    private var monitors: [Any] = []
    private var cancellables = Set<AnyCancellable>()
    private var expandWork: DispatchWorkItem?
    private var collapseWork: DispatchWorkItem?
    private var frameUpdateScheduled = false
    private var menuOpen = false
    private var suppressHover = false
    private var previousApp: NSRunningApplication?
    private var lastInsideIsland: Bool?
    private var lastBlockedReason = ""
    private var hoverEvaluationScheduled = false

    private let hoverExpandDelay: TimeInterval = 0.20
    private let hoverCollapseDelay: TimeInterval = 0.52
    private let debugEnabled = ProcessInfo.processInfo.environment["MACHINA_NOTCH_DEBUG"] == "1"

    func debugLog(_ message: String) {
        guard debugEnabled else { return }
        FileHandle.standardError.write(Data("[notch] \(message)\n".utf8))
    }

    override init() {
        let initialScreen = Self.preferredScreen()
        screen = initialScreen
        metrics = initialScreen.map(IslandMetrics.read(from:)) ?? IslandMetrics()
        panel = NotchPanel(
            contentRect: NSRect(origin: .zero, size: metrics.compactSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        super.init()

        configurePanel()
        model.metrics = metrics
        installStatusItem()
        observeModel()
        installMonitors()
        observeSystem()
        applyLayout()
        updateStatusItem()
    }

    // MARK: - Lifecycle

    func show() {
        applyLayout()
        NSApp.unhide(nil)
        panel.acceptsKeyStatus = false
        panel.orderFrontRegardless()
        updatePanelFrame()
        model.start()
        DispatchQueue.main.async { [weak self] in
            self?.scheduleHoverEvaluation()
        }
    }

    func hide() {
        collapse(restoreFocus: true)
        panel.orderOut(nil)
    }

    func toggle() {
        if model.expanded {
            collapse()
        } else {
            expand(activate: true)
        }
    }

    var isExpandedAndKey: Bool {
        model.expanded && panel.isKeyWindow
    }

    // MARK: - Panel

    private func configurePanel() {
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovable = false
        panel.isMovableByWindowBackground = false
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = false
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 3)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.animationBehavior = .none
        panel.acceptsMouseMovedEvents = true
        panel.ignoresMouseEvents = true

        let container = NSView(frame: NSRect(origin: .zero, size: metrics.compactSize))
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor.clear.cgColor

        let hosting = NSHostingView(rootView: NotchView(model: model, controller: self))
        hosting.frame = container.bounds
        hosting.autoresizingMask = [.width, .height]
        hosting.sizingOptions = []
        hosting.wantsLayer = true
        hosting.layer?.backgroundColor = NSColor.clear.cgColor
        container.addSubview(hosting)

        // Hover lives in AppKit, not SwiftUI. The tracking overlay sits above
        // the hosting view but returns nil from hitTest, so buttons still work.
        // Its callbacks only schedule a main-runloop evaluation.
        let tracking = HoverTrackingView(frame: container.bounds) { [weak self] _ in
            DispatchQueue.main.async { self?.scheduleHoverEvaluation() }
        }
        tracking.autoresizingMask = [.width, .height]
        container.addSubview(tracking)

        panel.contentView = container
        panel.contentView?.wantsLayer = true
        panel.contentView?.layer?.backgroundColor = NSColor.clear.cgColor
    }

    private func applyLayout() {
        screen = Self.preferredScreen()
        if let screen {
            metrics = IslandMetrics.read(from: screen)
        } else {
            metrics = IslandMetrics()
        }
        model.metrics = metrics
        updatePanelFrame()
        scheduleHoverEvaluation()
    }

    private func scheduleFrameUpdate() {
        guard !frameUpdateScheduled else { return }
        frameUpdateScheduled = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.frameUpdateScheduled = false
            self.updatePanelFrame()
            self.scheduleHoverEvaluation()
        }
    }

    private func updatePanelFrame() {
        guard let screen else { return }
        let target = metrics.windowFrame(
            in: screen,
            expanded: model.expanded,
            banner: model.banner != nil
        )
        if panel.frame.equalTo(target) { return }
        // Resize the window immediately and let SwiftUI spring the island
        // inside it. Animating the window frame itself during hover used to
        // re-enter the hosting view; the island's own animation is enough.
        panel.setFrame(target, display: true)
    }

    // MARK: - Expand / collapse

    func expand(activate: Bool = true) {
        expandWork?.cancel()
        expandWork = nil
        collapseWork?.cancel()
        collapseWork = nil

        guard !model.expanded else {
            debugLog("expand ignored (already expanded) activate=\(activate) key=\(panel.isKeyWindow) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")
            if activate { self.activate() }
            return
        }

        debugLog("expand activate=\(activate) key=\(panel.isKeyWindow) active=\(NSApp.isActive) ignores=\(panel.ignoresMouseEvents) composer=\(model.composerFocused) draft=\(!model.draft.isEmpty)")
        model.expanded = true
        suppressHover = false
        scheduleFrameUpdate()

        if activate {
            rememberFrontmost()
            panel.acceptsKeyStatus = true
            NSApp.activate(ignoringOtherApps: true)
            panel.makeKeyAndOrderFront(nil)
            model.focusComposerTick += 1
            SoundPlayer.shared.play(.wake)
        } else {
            // Hover expansion is deliberately non-key and non-activating. The
            // user can read the island, but approvals and shortcuts require a
            // click that grants key status.
            panel.acceptsKeyStatus = false
            panel.orderFrontRegardless()
        }
        DispatchQueue.main.async { [weak self] in
            self?.scheduleHoverEvaluation()
        }
    }

    func activate() {
        guard model.expanded else { return }
        debugLog("activate active=\(NSApp.isActive) key=\(panel.isKeyWindow) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")
        rememberFrontmost()
        panel.acceptsKeyStatus = true
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        debugLog("activated active=\(NSApp.isActive) key=\(panel.isKeyWindow) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")
    }

    func collapse(restoreFocus: Bool = true) {
        expandWork?.cancel()
        expandWork = nil
        collapseWork?.cancel()
        collapseWork = nil
        suppressHover = true

        guard model.expanded else {
            panel.ignoresMouseEvents = true
            return
        }

        debugLog("collapse restore=\(restoreFocus) key=\(panel.isKeyWindow) active=\(NSApp.isActive) composer=\(model.composerFocused) draft=\(!model.draft.isEmpty) menu=\(menuOpen)")
        model.expanded = false
        model.composerFocused = false
        panel.acceptsKeyStatus = false
        if panel.isKeyWindow { panel.resignKey() }
        panel.ignoresMouseEvents = true
        scheduleFrameUpdate()

        if restoreFocus {
            restorePreviousApp()
        }
    }

    func collapseSoonIfNotKey() {
        guard model.expanded, !panel.isKeyWindow else { return }
        collapseSoon()
    }

    func collapseSoon() {
        guard model.expanded else { return }
        scheduleCollapse()
    }

    func cancelCollapse() {
        collapseWork?.cancel()
        collapseWork = nil
    }

    /// Only resolves after the panel is expanded and key. If a click on an
    /// approval button is what granted key status, the decision is deferred by
    /// one runloop turn so it still runs against a key panel.
    func resolveApproval(_ approval: ResidentApproval, verdict: String) {
        guard model.expanded else { return }
        if panel.isKeyWindow {
            model.resolve(approval, verdict: verdict)
        } else {
            activate()
            DispatchQueue.main.async { [weak self] in
                guard let self, self.model.expanded, self.panel.isKeyWindow else { return }
                self.model.resolve(approval, verdict: verdict)
            }
        }
    }

    private var canAutoCollapse: Bool {
        guard !menuOpen else { return false }
        guard !panel.isKeyWindow else { return false }
        guard !model.composerFocused else { return false }
        guard model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        return true
    }

    private func scheduleExpand() {
        guard !model.expanded, expandWork == nil, !suppressHover else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.expandWork = nil
            guard self.panel.isVisible, let screen = self.screen else { return }
            let point = NSEvent.mouseLocation
            let island = self.metrics.islandRect(in: screen, expanded: false).insetBy(dx: -2, dy: -3)
            guard island.contains(point) else { return }
            self.expand(activate: false)
        }
        expandWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + hoverExpandDelay, execute: work)
    }

    private func scheduleCollapse() {
        guard model.expanded, collapseWork == nil else { return }
        guard canAutoCollapse else {
            let reason = "key=\(panel.isKeyWindow) menu=\(menuOpen) composer=\(model.composerFocused) draft=\(!model.draft.isEmpty) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")"
            if reason != lastBlockedReason {
                lastBlockedReason = reason
                debugLog("collapse blocked \(reason)")
            }
            return
        }
        lastBlockedReason = ""
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.collapseWork = nil
            guard self.model.expanded, self.canAutoCollapse else { return }
            guard let screen = self.screen else { return }
            let point = NSEvent.mouseLocation
            let island = self.metrics.islandRect(in: screen, expanded: true).insetBy(dx: -2, dy: -3)
            guard !island.contains(point) else { return }
            self.collapse(restoreFocus: false)
        }
        collapseWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + hoverCollapseDelay, execute: work)
    }

    // MARK: - Pointer tracking

    private func installMonitors() {
        let movedEvents: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged]

        // The tracking view needs mouse events, which a click-through window
        // does not receive. These event-driven monitors only wake a pointer
        // evaluation (no polling timer) so the window can toggle passthrough
        // when the cursor approaches the island.
        if let global = NSEvent.addGlobalMonitorForEvents(matching: movedEvents, handler: { [weak self] _ in
            DispatchQueue.main.async { self?.scheduleHoverEvaluation() }
        }) {
            monitors.append(global)
        }
        if let local = NSEvent.addLocalMonitorForEvents(matching: movedEvents, handler: { [weak self] event in
            DispatchQueue.main.async { self?.scheduleHoverEvaluation() }
            return event
        }) {
            monitors.append(local)
        }

        if let globalClick = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown], handler: { [weak self] _ in
            DispatchQueue.main.async { self?.handleMouseDown() }
        }) {
            monitors.append(globalClick)
        }
        if let localClick = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown], handler: { [weak self] event in
            DispatchQueue.main.async { self?.handleMouseDown() }
            return event
        }) {
            monitors.append(localClick)
        }
    }

    /// Coalesce the burst of tracking/monitor callbacks into one evaluation on
    /// the next runloop turn. This is the only place hover state is changed.
    private func scheduleHoverEvaluation() {
        guard !hoverEvaluationScheduled else { return }
        hoverEvaluationScheduled = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.hoverEvaluationScheduled = false
            self.evaluatePointer()
        }
    }

    private func evaluatePointer() {
        guard panel.isVisible, !NSApp.isHidden else {
            cancelCollapse()
            expandWork?.cancel()
            expandWork = nil
            return
        }
        guard let screen else { return }

        let point = NSEvent.mouseLocation
        let expanded = model.expanded
        let island = metrics.islandRect(in: screen, expanded: expanded)
            .insetBy(dx: -2, dy: -3)
        let banner = (!expanded && model.banner != nil)
            ? metrics.bannerRect(in: screen)
            : .null
        let insideIsland = island.contains(point)
        let insideBanner = banner.contains(point)
        let inside = insideIsland || insideBanner

        if insideIsland != lastInsideIsland {
            lastInsideIsland = insideIsland
            debugLog("pointer insideIsland=\(insideIsland) insideBanner=\(insideBanner) expanded=\(expanded) ignores=\(panel.ignoresMouseEvents) suppress=\(suppressHover) key=\(panel.isKeyWindow) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")
        }

        panel.ignoresMouseEvents = !inside
        if inside { NSCursor.arrow.set() }

        if expanded {
            if insideIsland {
                cancelCollapse()
            } else if !insideBanner {
                scheduleCollapse()
            }
        } else {
            if insideIsland && !suppressHover {
                scheduleExpand()
            } else {
                expandWork?.cancel()
                expandWork = nil
                if !insideIsland { suppressHover = false }
            }
        }
    }

    private func handleMouseDown() {
        guard let screen else { return }
        let point = NSEvent.mouseLocation
        debugLog("mouseDown at \(point) expanded=\(model.expanded) key=\(panel.isKeyWindow) ignores=\(panel.ignoresMouseEvents) front=\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")")

        if model.expanded {
            let island = metrics.islandRect(in: screen, expanded: true)
                .insetBy(dx: -2, dy: -3)
            if island.contains(point) {
                // A click inside grants key status before the SwiftUI button
                // action runs, so approvals, typing and shortcuts work on the
                // first click even after a non-activating hover expansion.
                if !panel.isKeyWindow { activate() }
            } else {
                collapse(restoreFocus: false)
            }
            return
        }

        let island = metrics.islandRect(in: screen, expanded: false)
            .insetBy(dx: -2, dy: -3)
        if island.contains(point) {
            // Mouse-down monitor, not a tracking-area callback. Still hop one
            // runloop turn so the pressed island can finish its event dispatch.
            DispatchQueue.main.async { [weak self] in
                self?.expand(activate: true)
            }
        }
    }

    // MARK: - Focus handoff

    private func rememberFrontmost() {
        guard !NSApp.isActive else { return }
        previousApp = NSWorkspace.shared.frontmostApplication
    }

    private func restorePreviousApp() {
        guard NSApp.isActive,
              let app = previousApp,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier
        else {
            previousApp = nil
            return
        }
        previousApp = nil
        app.activate(options: [])
    }

    // MARK: - System observers

    private func observeSystem() {
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.applyLayout() }
        }

        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            Task { @MainActor in
                guard let self else { return }
                guard app?.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
                if self.model.expanded, !self.menuOpen {
                    self.collapse(restoreFocus: false)
                }
            }
        }

        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.updatePanelFrame()
            }
        }
    }

    private func observeModel() {
        model.$expanded
            .removeDuplicates()
            .sink { [weak self] _ in self?.scheduleFrameUpdate() }
            .store(in: &cancellables)

        model.$banner
            .removeDuplicates()
            .sink { [weak self] _ in self?.scheduleFrameUpdate() }
            .store(in: &cancellables)

        model.$muted
            .removeDuplicates()
            .sink { [weak self] _ in self?.updateStatusItem() }
            .store(in: &cancellables)

        let signals: [AnyPublisher<Void, Never>] = [
            model.$approvals.map { _ in () }.eraseToAnyPublisher(),
            model.$busy.map { _ in () }.eraseToAnyPublisher(),
            model.$connection.map { _ in () }.eraseToAnyPublisher(),
            model.$status.map { _ in () }.eraseToAnyPublisher(),
        ]
        Publishers.MergeMany(signals)
            .sink { [weak self] _ in self?.updateStatusItem() }
            .store(in: &cancellables)
    }

    // MARK: - Status item

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        statusItem = item
        statusMenu = menu
    }

    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        let count = model.approvals.count
        button.title = count > 0 ? "◉\(count)" : "◉"
        button.toolTip = "Maki · \(model.agentStateLabel)"
        button.font = .systemFont(ofSize: 13, weight: .semibold)
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()

        let state = NSMenuItem(
            title: "Maki · \(model.agentStateLabel)",
            action: nil,
            keyEquivalent: ""
        )
        state.isEnabled = false
        menu.addItem(state)

        let approvalCount = model.approvals.count
        let approvalsItem = NSMenuItem(
            title: approvalCount == 0
                ? "No approvals waiting"
                : "\(approvalCount) approval\(approvalCount == 1 ? "" : "s") waiting",
            action: approvalCount > 0 ? #selector(reviewApprovals) : nil,
            keyEquivalent: ""
        )
        approvalsItem.target = self
        approvalsItem.isEnabled = approvalCount > 0
        menu.addItem(approvalsItem)

        let gateway = NSMenuItem(
            title: "Gateway · \(model.connection == "live" ? "connected" : "offline")",
            action: nil,
            keyEquivalent: ""
        )
        gateway.isEnabled = false
        menu.addItem(gateway)

        let daemon = NSMenuItem(
            title: "Daemon · \(model.daemonLabel)",
            action: nil,
            keyEquivalent: ""
        )
        daemon.isEnabled = false
        menu.addItem(daemon)
        menu.addItem(.separator())

        let sound = NSMenuItem(
            title: "Sound effects",
            action: #selector(toggleSound),
            keyEquivalent: ""
        )
        sound.target = self
        sound.state = model.muted ? .off : .on
        menu.addItem(sound)

        let login = NSMenuItem(
            title: "Launch at Login",
            action: #selector(toggleLoginItem),
            keyEquivalent: ""
        )
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)

        let visibility = NSMenuItem(
            title: panel.isVisible ? "Hide Maki" : "Show Maki",
            action: #selector(toggleFromMenu),
            keyEquivalent: ""
        )
        visibility.target = self
        menu.addItem(visibility)
        menu.addItem(.separator())

        let quit = NSMenuItem(
            title: "Quit Machina Notch",
            action: #selector(quit),
            keyEquivalent: "q"
        )
        quit.target = self
        menu.addItem(quit)
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuOpen = true
        cancelCollapse()
    }

    func menuDidClose(_ menu: NSMenu) {
        menuOpen = false
        scheduleHoverEvaluation()
    }

    @objc private func toggleFromMenu() {
        if panel.isVisible {
            if model.expanded {
                collapse()
            } else {
                hide()
            }
        } else {
            show()
        }
    }

    @objc private func reviewApprovals() {
        if !panel.isVisible { show() }
        expand(activate: true)
    }

    @objc private func toggleSound() {
        model.toggleMute()
        updateStatusItem()
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

    // MARK: - Screen selection

    private static func preferredScreen() -> NSScreen? {
        NSScreen.screens.first { $0.safeAreaInsets.top > 0 }
            ?? NSScreen.main
            ?? NSScreen.screens.first
    }
}
