import AppKit
import Foundation
import SwiftUI

@MainActor
final class NotchModel: ObservableObject {
    @Published var expanded = false
    @Published var status: ResidentStatus?
    @Published var approvals: [ResidentApproval] = []
    @Published var messages: [ChatLine] = []
    @Published var mascot: MascotState = .idle
    @Published var connection = "demo"
    @Published var draft = ""
    @Published var busy = false
    @Published var muted = SoundPlayer.shared.muted
    @Published var listening = false

    struct ChatLine: Identifiable {
        let id = UUID()
        let text: String
        let fromUser: Bool
    }

    private let gateway = GatewayClient()
    private var timer: Timer?
    private var contextTimer: Timer?

    func start() {
        GatewayLauncher.shared.ensureRunning()
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
                let approvals = try await gateway.approvals()
                let previous = self.approvals.count
                self.status = status
                self.approvals = approvals
                self.connection = "live"
                self.mascot = listening ? .listening : (approvals.isEmpty ? (busy ? .thinking : .idle) : .waiting)
                if approvals.count > previous { SoundPlayer.shared.play(.alert) }
            } catch {
                if self.status == nil { self.loadDemo() }
                if self.connection != "offline" { SoundPlayer.shared.play(.boop) }
                self.connection = "offline"
                self.mascot = .sleeping
            }
        }
    }

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

        busy = true
        mascot = .thinking
        Task { @MainActor in
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
                    break
                }
            } catch {
                messages.append(
                    ChatLine(text: "I couldn't reach the resident: \(error.localizedDescription)", fromUser: false)
                )
                mascot = .surprised
                SoundPlayer.shared.play(.boop)
            }
            busy = false
            if mascot != .waiting { mascot = .idle }
        }
    }

    func pet() {
        mascot = .happy
        SoundPlayer.shared.play(.happy)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            guard let self, !self.busy else { return }
            self.mascot = self.listening ? .listening : (self.approvals.isEmpty ? .idle : .waiting)
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
        busy = true
        mascot = .processing
        Task { @MainActor in
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
                    break
                }
            } catch {
                messages.append(
                    ChatLine(text: "Screen capture failed: \(error.localizedDescription)", fromUser: false)
                )
                mascot = .surprised
                SoundPlayer.shared.play(.boop)
            }
            busy = false
            if mascot != .waiting { mascot = .idle }
        }
    }

    func resolve(_ approval: ResidentApproval, verdict: String) {
        Task { @MainActor in
            do {
                try await gateway.resolve(
                    requestId: approval.request_id,
                    sessionId: approval.session_id,
                    verdict: verdict
                )
                approvals.removeAll { $0.id == approval.id }
                mascot = verdict == "approved" ? .happy : .idle
                SoundPlayer.shared.play(verdict == "approved" ? .happy : .boop)
            } catch {
                connection = "offline"
            }
        }
    }

    func pause() {
        status = nil
        approvals = []
        messages.append(ChatLine(text: "Resident paused. Grants are unchanged.", fromUser: false))
        mascot = .sleeping
        SoundPlayer.shared.play(.sleep)
    }

    func toggleMute() {
        muted.toggle()
        SoundPlayer.shared.muted = muted
        if !muted { SoundPlayer.shared.play(.chirp) }
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
