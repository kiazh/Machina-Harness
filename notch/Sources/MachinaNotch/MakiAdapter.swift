import AppKit
import SwiftUI

// MARK: - Maki adapter for ported NotchAgents views

enum AgentKind: String, CaseIterable, Identifiable {
    case maki

    var id: String { rawValue }
    var title: String { "Maki" }

    /// Four-stop silver palette: light, core, deep, highlight.
    var gradient: [Color] {
        [Color(hex: 0xF7F8FA), Color(hex: 0xC3CBD5), Color(hex: 0x5A6572), Color(hex: 0xFFFFFF)]
    }

    var accent: Color { gradient[1] }
}

enum AgentStatus: Equatable {
    case offline, idle, thinking, tool, waiting

    var label: String {
        switch self {
        case .offline: return "Offline"
        case .idle: return "Idle"
        case .thinking: return "Thinking"
        case .tool: return "Running tool"
        case .waiting: return "Needs you"
        }
    }

    var isBusy: Bool { self == .thinking || self == .tool }
}

struct LogEntry: Identifiable, Equatable {
    let id = UUID()
    let tool: String
    let detail: String?
    let date: Date
    var done = false
}

struct DiffLine: Identifiable {
    enum Kind: Equatable { case addition, removal, context }
    let id = UUID()
    let text: String
    let kind: Kind
}

struct IslandAlert: Identifiable, Equatable {
    enum Style: Equatable { case done, question }

    let id: UUID
    let kind: AgentKind
    let style: Style
    let title: String
    let message: String
}

struct PendingPermission: Identifiable {
    let id: String
    let approval: ResidentApproval
    let tool: String
    let summary: String
    let diff: [DiffLine]
    let previewTruncated: Bool

    init(_ approval: ResidentApproval) {
        id = approval.id
        self.approval = approval
        tool = approval.operation.replacingOccurrences(of: "_", with: " ")
        summary = approval.target
        let lines = Self.lines(for: approval)
        diff = Array(lines.prefix(6))
        previewTruncated = approval.previewTruncated || lines.count > 6
    }

    private static func lines(for approval: ResidentApproval) -> [DiffLine] {
        if let unified = approval.diffText {
            var inHunk = false
            let rows = unified.components(separatedBy: "\n").compactMap { line -> DiffLine? in
                if line.hasPrefix("diff --git ") { inHunk = false; return nil }
                if line.hasPrefix("@@") { inHunk = true; return nil }
                guard inHunk else { return nil }
                if line.hasPrefix("+") {
                    return DiffLine(text: String(line.dropFirst()), kind: .addition)
                }
                if line.hasPrefix("-") {
                    return DiffLine(text: String(line.dropFirst()), kind: .removal)
                }
                if line.hasPrefix(" ") {
                    return DiffLine(text: String(line.dropFirst()), kind: .context)
                }
                return line.isEmpty ? nil : DiffLine(text: line, kind: .context)
            }
            if !rows.isEmpty { return rows }
        }
        if let preview = approval.previewText {
            return preview.components(separatedBy: "\n")
                .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                .map { DiffLine(text: $0, kind: .context) }
        }
        return []
    }
}

/// Single-agent state shaped like NotchAgents' `Agent`, fed by NotchModel.
struct MakiAgent: Identifiable {
    let kind: AgentKind = .maki
    var id: AgentKind { kind }
    var live = true
    var status: AgentStatus = .idle
    var tool: String?
    var detail: String?
    var turnStart: Date?
    var lastTurn: TimeInterval?
    var pending: PendingPermission?
    var log: [LogEntry] = []
    var mascot: MascotState = .idle
    var model: String?
    var cwd: String?
    var contextUsed = 0
    var contextWindow = 0
    var cost: Double?
    var tokens = 0

    var contextFraction: Double {
        contextWindow > 0 ? min(1, Double(contextUsed) / Double(contextWindow)) : 0
    }
}

typealias Agent = MakiAgent
typealias AgentStore = NotchModel

extension NotchModel {
    /// The ported island views read this facade. There is intentionally only
    /// one agent: Maki.
    var makiAgent: MakiAgent {
        let status: AgentStatus
        if connection != "live" {
            status = .offline
        } else if !approvals.isEmpty {
            status = .waiting
        } else if busy {
            status = .thinking
        } else {
            status = .idle
        }

        let pending = approvals.first.map(PendingPermission.init)
        return MakiAgent(
            live: connection == "live",
            status: status,
            tool: pending?.tool,
            detail: pending?.summary,
            turnStart: turnStartedAt,
            lastTurn: lastTurnDuration,
            pending: pending,
            mascot: mascot,
            model: connection == "live" ? "resident" : nil
        )
    }

    var liveAgents: [MakiAgent] { [makiAgent] }
    var listed: [MakiAgent] { [makiAgent] }
    var ordered: [MakiAgent] { [makiAgent] }

    var islandAlert: IslandAlert? {
        guard let banner else { return nil }
        return IslandAlert(
            id: banner.id,
            kind: .maki,
            style: banner.style == .done ? .done : .question,
            title: banner.title,
            message: banner.message
        )
    }

    func decide(_ agent: MakiAgent, allow: Bool) {
        guard let pending = agent.pending else { return }
        resolve(pending.approval, verdict: allow ? "approved" : "denied")
    }

    func dismissAlert() {
        dismissBanner()
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

let amber = Color(hex: 0xF5B53F)
let amberPalette = [
    Color(hex: 0xF7C25A),
    Color(hex: 0xEE8F2E),
    Color(hex: 0xC8551F),
    Color(hex: 0xFFE3A3),
]
let makiGreen = Color(hex: 0x6FDDA0)
