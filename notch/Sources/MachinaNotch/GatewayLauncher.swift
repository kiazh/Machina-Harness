import Foundation

/// Starts the local HTTP gateway when the notch is opened and no gateway is
/// listening. The notch is a client; this only ensures the brain is running.
@MainActor
final class GatewayLauncher {
    static let shared = GatewayLauncher()

    private var process: Process?
    private let base = URL(string: "http://127.0.0.1:8000/health")!

    func ensureRunning() {
        Task { @MainActor in
            if await reachable() { return }
            start()
        }
    }

    func reachable() async -> Bool {
        var request = URLRequest(url: base)
        request.timeoutInterval = 1.5
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            return false
        }
    }

    private func start() {
        guard process == nil || process?.isRunning == false else { return }
        let paths = [
            NSHomeDirectory() + "/.local/bin/machina",
            "/opt/homebrew/bin/machina",
            "/usr/local/bin/machina",
            FileManager.default.currentDirectoryPath + "/.venv/bin/machina",
        ]
        guard let executable = paths.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            return
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: executable)
        task.arguments = ["serve", "--port", "8000"]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
            process = task
        } catch {
            process = nil
        }
    }
}
