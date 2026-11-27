import AppKit
import Foundation
import SwiftUI

/// A transient notification that drops below the notch. Completion and
/// attention banners are silent data; the view decides whether to show them.
struct NotchBanner: Identifiable, Equatable {
    enum Style: Equatable {
        case done
        case attention
    }

    let id = UUID()
    let style: Style
    let title: String
    let message: String
}

@MainActor
final class NotchModel: ObservableObject {
    @Published var expanded = false
    @Published var metrics = IslandMetrics()
    @Published var status: ResidentStatus?
    @Published var approvals: [ResidentApproval] = []
    @Published var messages: [ChatLine] = []
    @Published var mascot: MascotState = .idle
    @Published var connection = "demo"
    @Published var draft = ""
    @Published var busy = false
    @Published var muted = SoundPlayer.shared.muted
    @Published var listening = false
    @Published var composerFocused = false
    @Published var turnStartedAt: Date?
    @Published var lastTurnDuration: TimeInterval?
    @Published var banner: NotchBanner?
    /// Bumped by the controller when a click (not a hover) opens the island.
    @Published var focusComposerTick = 0

    struct ChatLine: Identifiable {
        let id = UUID()
        let text: String
        let fromUser: Bool
    }

    private let gateway = GatewayClient()
    private var timer: Timer?
    private var contextTimer: Timer?
    private var bannerWorkItem: DispatchWorkItem?
    private var knownApprovalIDs: Set<String> = []
    private var hasLoadedApprovals = false

    // MARK: - Polling

    func start() {
        GatewayLauncher.shared.ensureRunning()
        guard timer == nil else {
            refresh()
            return
        }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        contextTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.sendContext() }
        }
        sendContext()
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        contextTimer?.invalidate()
        contextTimer = nil
    }

    private func sendContext() {
        guard connection == "live" else { return }
        let snapshot = ContextSensor.snapshot()
        guard !snapshot.isEmpty else { return }
        Task { @MainActor in
            try? await gateway.context(snapshot)
        }
    }

    func refresh() {
        Task { @MainActor in
            do {
                let status = try await gateway.status()
                let residentApprovals = try await gateway.approvals()
                var approvals = residentApprovals

                // `approvals.list` carries the reviewable proposal and content
                // preview; `resident.approval.list` is the canonical list.
                // Merge by request id so richer cards render when available.
                if let rich = try? await gateway.richApprovals(sessionId: status.sessionId) {
                    let byRequest = Dictionary(uniqueKeysWithValues: rich.map { ($0.request_id, $0) })
                    approvals = residentApprovals.map { byRequest[$0.request_id] ?? $0 }
                    for extra in rich where !approvals.contains(where: { $0.request_id == extra.request_id }) {
                        approvals.append(extra)
                    }
                }

                let previousCount = self.approvals.count
                let previousIDs = self.knownApprovalIDs
                let freshIDs = Set(approvals.map(\.request_id)).subtracting(previousIDs)
                let wasFirstLoad = !self.hasLoadedApprovals

                self.status = status
                self.approvals = approvals
                self.connection = "live"
                self.mascot = listening
                    ? .listening
                    : (approvals.isEmpty ? (busy ? .thinking : .idle) : .waiting)
                self.knownApprovalIDs = Set(approvals.map(\.request_id))
                self.hasLoadedApprovals = true

                if approvals.count > previousCount {
                    SoundPlayer.shared.play(.alert)
                }
                if !freshIDs.isEmpty && !wasFirstLoad {
                    self.showBanner(
                        NotchBanner(
                            style: .attention,
                            title: "Maki",
                            message: approvals.first.map(Self.attentionMessage(for:)) ?? "Needs you"
                        )
                    )
                }
            } catch {
                if self.status == nil { self.loadDemo() }
                if self.connection != "offline" { SoundPlayer.shared.play(.boop) }
                self.connection = "offline"
                self.mascot = .sleeping
            }
        }
    }

    // MARK: - Turns

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        messages.append(ChatLine(text: text, fromUser: true))
        SoundPlayer.shared.play(.chirp)

        guard let sessionId = status?.sessionId, connection == "live" else {
            messages.append(
                ChatLine(
                    text: "The resident is offline. Start the gateway with `machina serve`, then try again.",
                    fromUser: false
                )
            )
            mascot = .sleeping
            return
        }

        let started = Date()
        turnStartedAt = started
        busy = true
        mascot = .thinking
        Task { @MainActor in
            var completed = false
            do {
                _ = try await gateway.prompt(sessionId: sessionId, text: text)
                let lastAssistant = messages.last(where: { !$0.fromUser })?.text
                for _ in 0..<90 {
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    let entries = try await gateway.history(sessionId: sessionId)
                    guard let reply = entries.first(where: { $0.role == "assistant" })?.content,
                          !reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                          reply != lastAssistant else { continue }
                    messages.append(ChatLine(text: reply, fromUser: false))
                    mascot = .happy
                    SoundPlayer.shared.play(.happy)
                    completed = true
                    break
                }
            } catch {
                messages.append(
                    ChatLine(text: "I couldn't reach the resident: \(error.localizedDescription)", fromUser: false)
                )
                mascot = .surprised
                SoundPlayer.shared.play(.boop)
            }
            finishTurn(startedAt: started, completed: completed)
        }
    }

    func sendScreenshot() {
        guard let sessionId = status?.sessionId, connection == "live" else {
            messages.append(
                ChatLine(text: "Start the gateway before capturing the screen.", fromUser: false)
            )
            mascot = .sleeping
            return
        }
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        draft = ""
        let started = Date()
        turnStartedAt = started
        busy = true
        mascot = .processing
        Task { @MainActor in
            var completed = false
            do {
                let image = try await ScreenCapture.jpeg()
                messages.append(
                    ChatLine(
                        text: text.isEmpty ? "What do you see on my screen?" : text,
                        fromUser: true
                    )
                )
                _ = try await gateway.prompt(
                    sessionId: sessionId,
                    text: text.isEmpty ? "What do you see on my screen?" : text,
                    image: image
                )
                let baseline = messages.last(where: { !$0.fromUser })?.text
                for _ in 0..<90 {
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    let entries = try await gateway.history(sessionId: sessionId)
                    guard let reply = entries.first(where: { $0.role == "assistant" })?.content,
                          !reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                          reply != baseline else { continue }
                    messages.append(ChatLine(text: reply, fromUser: false))
                    mascot = .happy
                    SoundPlayer.shared.play(.happy)
                    completed = true
                    break
                }
            } catch {
                messages.append(
                    ChatLine(text: "Screen capture failed: \(error.localizedDescription)", fromUser: false)
                )
                mascot = .surprised
                SoundPlayer.shared.play(.boop)
            }
            finishTurn(startedAt: started, completed: completed)
        }
    }

    private func finishTurn(startedAt: Date, completed: Bool) {
        turnStartedAt = nil
        busy = false
        if approvals.isEmpty {
            mascot = completed ? .happy : .idle
        } else {
            mascot = .waiting
        }
        guard completed else { return }
        let elapsed = max(0, Date().timeIntervalSince(startedAt))
        lastTurnDuration = elapsed
        showBanner(
            NotchBanner(
                style: .done,
                title: "Maki",
                message: "Done in \(Self.durationLabel(elapsed))"
            )
        )
    }

    func pet() {
        mascot = .happy
        SoundPlayer.shared.play(.happy)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            guard let self, !self.busy else { return }
            self.mascot = self.listening ? .listening : (self.approvals.isEmpty ? .idle : .waiting)
        }
    }

    // MARK: - Approvals

    func resolve(_ approval: ResidentApproval, verdict: String) {
        Task { @MainActor in
            do {
                try await gateway.resolve(
                    requestId: approval.request_id,
                    sessionId: approval.session_id,
                    verdict: verdict
                )
                approvals.removeAll { $0.id == approval.id }
                knownApprovalIDs.remove(approval.request_id)
                mascot = approvals.isEmpty
                    ? (verdict == "approved" ? .happy : .idle)
                    : .waiting
                SoundPlayer.shared.play(verdict == "approved" ? .happy : .boop)
            } catch {
                connection = "offline"
            }
        }
    }

    // MARK: - Banners and sound

    func showBanner(_ banner: NotchBanner) {
        guard !muted else { return }
        bannerWorkItem?.cancel()
        self.banner = banner
        let id = banner.id
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.banner?.id == id else { return }
            self.banner = nil
            self.bannerWorkItem = nil
        }
        bannerWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 4.5, execute: work)
    }

    func dismissBanner() {
        bannerWorkItem?.cancel()
        bannerWorkItem = nil
        banner = nil
    }

    func pause() {
        status = nil
        approvals = []
        dismissBanner()
        messages.append(ChatLine(text: "Resident paused. Grants are unchanged.", fromUser: false))
        mascot = .sleeping
        SoundPlayer.shared.play(.sleep)
    }

    func toggleMute() {
        muted.toggle()
        SoundPlayer.shared.muted = muted
        if muted {
            dismissBanner()
        } else {
            SoundPlayer.shared.play(.chirp)
        }
    }

    // MARK: - Menu summary

    var agentStateLabel: String {
        if connection != "live" { return "Offline" }
        if status?.profile.paused == true { return "Paused" }
        if !approvals.isEmpty { return approvals.count == 1 ? "Waiting for you" : "\(approvals.count) approvals waiting" }
        if busy { return "Working" }
        if listening { return "Listening" }
        if mascot == .sleeping { return "Sleeping" }
        return "Idle"
    }

    var daemonLabel: String {
        guard let daemon = status?.daemon else { return "Unknown" }
        guard daemon.running else { return "Stopped" }
        guard let seconds = daemon.intervalSeconds, seconds > 0 else { return "Running" }
        return "Running · every \(Self.intervalLabel(seconds))"
    }

    nonisolated static func durationLabel(_ interval: TimeInterval) -> String {
        let seconds = max(0, Int(interval.rounded()))
        if seconds >= 3600 {
            return "\(seconds / 3600)h \(seconds % 3600 / 60)m"
        }
        if seconds >= 60 {
            return "\(seconds / 60)m \(seconds % 60)s"
        }
        return "\(seconds)s"
    }

    nonisolated static func elapsedLabel(_ interval: TimeInterval) -> String {
        let seconds = max(0, Int(interval))
        if seconds >= 3600 {
            return String(format: "%d:%02d:%02d", seconds / 3600, seconds % 3600 / 60, seconds % 60)
        }
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    nonisolated private static func intervalLabel(_ seconds: Int) -> String {
        if seconds >= 3600 { return "\(seconds / 3600)h" }
        if seconds >= 60 { return "\(seconds / 60)m" }
        return "\(seconds)s"
    }

    nonisolated private static func attentionMessage(for approval: ResidentApproval) -> String {
        let operation = approval.operation.replacingOccurrences(of: "_", with: " ")
        return operation.isEmpty ? "Needs you" : "Needs you · \(operation)"
    }

    private func loadDemo() {
        status = ResidentStatus(
            profile: ResidentProfile(
                key: "default",
                name: "Maki",
                persona: "",
                autonomyMode: "auto-safe",
                sharedMemory: true,
                paused: false
            ),
            sessionId: "demo",
            pendingApprovals: 1,
            grants: [],
            coreMemoryCount: 4
        )
        approvals = [
            ResidentApproval(
                request_id: "demo-approval",
                session_id: "demo",
                turn_id: nil,
                agent_id: "harness",
                operation: "send_email",
                target: "gmail:draft:quarterly-update",
                digest: "demo",
                status: "pending",
                created_at: nil
            )
        ]
        if messages.isEmpty {
            messages = [
                ChatLine(text: "Morning. I drafted the quarterly update and queued it for you.", fromUser: false)
            ]
        }
    }
}
