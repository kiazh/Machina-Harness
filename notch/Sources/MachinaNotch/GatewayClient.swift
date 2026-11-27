import Foundation

struct ResidentProfile: Decodable {
    let key: String
    let name: String
    let persona: String
    let autonomyMode: String
    let sharedMemory: Bool
    let paused: Bool
}

struct ResidentGrant: Decodable, Identifiable {
    let id: String
    let maxActions: Int?
    let actionsUsed: Int
    let expiresAt: String?
    let reason: String

    enum CodingKeys: String, CodingKey {
        case id, reason
        case maxActions = "maxActions"
        case actionsUsed = "actionsUsed"
        case expiresAt = "expiresAt"
    }
}

struct ResidentApproval: Decodable, Identifiable {
    var id: String { request_id }
    let request_id: String
    let session_id: String
    let turn_id: String?
    let agent_id: String
    let operation: String
    let target: String
    let digest: String
    let status: String
    let created_at: String?
}

struct ResidentHistoryEntry: Decodable {
    let role: String
    let content: String
}

private struct ResidentHistoryPayload: Decodable {
    let entries: [ResidentHistoryEntry]
}

private struct ResidentPromptResult: Decodable {
    let turnId: String
}

struct ResidentStatus: Decodable {
    let profile: ResidentProfile
    let sessionId: String
    let pendingApprovals: Int
    let grants: [ResidentGrant]
    let coreMemoryCount: Int
}

private struct EmptyResult: Decodable {}

private struct RPCErrorPayload: Decodable {
    let message: String?
}

private struct RPCEnvelope<T: Decodable>: Decodable {
    let result: T?
    let error: RPCErrorPayload?
}

enum GatewayError: LocalizedError {
    case noToken
    case http(Int)
    case rpc(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .noToken: return "No MACHINA_API_KEY or ~/.machina/notch.token"
        case .http(let code): return "Gateway HTTP \(code)"
        case .rpc(let message): return message
        case .invalidResponse: return "Invalid gateway response"
        }
    }
}

/// Small JSON-RPC client for the local Machina API.
///
/// The notch is a client of the same gateway as the TUI; it never owns the
/// engine. Resident methods are local-terminal capabilities, so the default
/// token is the notch token written by the resident setup.
struct GatewayClient {
    var baseURL = URL(string: "http://127.0.0.1:8000")!

    private func apiKey() -> String? {
        let value = ProcessInfo.processInfo.environment["MACHINA_API_KEY"] ?? ""
        return value.isEmpty ? nil : value
    }

    private func notchToken() -> String? {
        let path = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".machina/notch.token")
        guard let value = try? String(contentsOf: path, encoding: .utf8) else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func call<T: Decodable>(_ method: String, params: [String: Any] = [:]) async throws -> T {
        let key = apiKey()
        let notch = notchToken()
        guard key != nil || notch != nil else { throw GatewayError.noToken }
        var request = URLRequest(url: baseURL.appendingPathComponent("rpc"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let key {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
        if let notch {
            request.setValue(notch, forHTTPHeaderField: "X-Machina-Notch")
        }
        request.httpBody = try JSONSerialization.data(
            withJSONObject: ["method": method, "params": params]
        )
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw GatewayError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw GatewayError.http(http.statusCode) }
        let envelope = try JSONDecoder().decode(RPCEnvelope<T>.self, from: data)
        if let error = envelope.error {
            throw GatewayError.rpc(error.message ?? "RPC failed")
        }
        guard let result = envelope.result else { throw GatewayError.invalidResponse }
        return result
    }

    func status() async throws -> ResidentStatus {
        try await call("resident.status")
    }

    func approvals() async throws -> [ResidentApproval] {
        struct Payload: Decodable { let approvals: [ResidentApproval] }
        let payload: Payload = try await call("resident.approval.list")
        return payload.approvals
    }

    func resolve(requestId: String, sessionId: String, verdict: String) async throws {
        let _: EmptyResult = try await call(
            "resident.approval.resolve",
            params: ["requestId": requestId, "sessionId": sessionId, "verdict": verdict]
        )
    }

    func prompt(sessionId: String, text: String) async throws -> String {
        let result: ResidentPromptResult = try await call(
            "resident.prompt",
            params: ["sessionId": sessionId, "text": text]
        )
        return result.turnId
    }

    func context(_ payload: [String: String]) async throws {
        let _: EmptyResult = try await call("resident.context", params: payload)
    }

    func history(sessionId: String, limit: Int = 20) async throws -> [ResidentHistoryEntry] {
        let payload: ResidentHistoryPayload = try await call(
            "session.history",
            params: ["sessionId": sessionId, "limit": limit]
        )
        return payload.entries
    }
}
