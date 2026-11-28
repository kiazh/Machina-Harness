import AVFoundation
import Foundation
import Speech

/// Feeds audio buffers from CoreAudio's realtime thread into Speech. Isolated
/// in a Sendable box so the tap closure does not inherit `VoiceInput`'s
/// MainActor isolation (which Swift 6 checks on the audio thread and aborts).
private final class SpeechAudioFeeder: @unchecked Sendable {
    private let request: SFSpeechAudioBufferRecognitionRequest

    init(request: SFSpeechAudioBufferRecognitionRequest) {
        self.request = request
    }

    nonisolated func append(_ buffer: AVAudioPCMBuffer) {
        request.append(buffer)
    }
}

/// Push-to-talk dictation inspired by Handy: press, speak, release, and the
/// transcript lands in the composer. Recognition is on-device by default; the
/// microphone is never open unless the button is held.
@MainActor
final class VoiceInput: ObservableObject {
    @Published var isRecording = false
    @Published private(set) var isFinalizing = false
    @Published private var startupID: UUID?
    @Published var transcript = ""
    @Published var error = ""

    var onFinal: ((String) -> Void)?
    var onPartial: ((String) -> Void)?

    private let recognizer = SFSpeechRecognizer(locale: .current)
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var finalDelivered = false
    private var tapInstalled = false
    private var finalization: Task<Void, Never>?
    private var recognitionID = UUID()

    var isStarting: Bool { startupID != nil }

    func start() {
        guard !isRecording, !isStarting, !isFinalizing else { return }
        let id = UUID()
        startupID = id
        error = ""
        transcript = ""
        finalDelivered = false
        Task { @MainActor in
            defer { if startupID == id { startupID = nil } }
            let microphone = await microphoneAllowed()
            guard startupID == id else { return }
            guard microphone else {
                error = "Microphone permission is required for dictation."
                return
            }
            let speech = await speechAllowed()
            guard startupID == id else { return }
            guard speech else {
                error = "Speech recognition permission is required for dictation."
                return
            }
            guard let recognizer, recognizer.isAvailable else {
                error = "Speech recognition is unavailable right now."
                return
            }
            if recognizer.supportsOnDeviceRecognition {
                // On-device only keeps the privacy promise and avoids audio upload.
                begin(recognizer: recognizer, onDevice: true)
            } else {
                error = "On-device dictation is unavailable for this language."
            }
        }
    }

    /// Release the microphone immediately and let recognition finish the audio.
    func stop() {
        if isStarting {
            cancel()
            return
        }
        guard isRecording, !isFinalizing else { return }
        isFinalizing = true
        isRecording = false
        stopAudio()
        request?.endAudio()
        task?.finish()
        finalization = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
            self?.keepPartial("Dictation didn’t finish. Review the draft before sending.")
        }
    }

    /// Abort recognition, discard the transcript, and tear everything down.
    func cancel() {
        startupID = nil
        guard isRecording || engine != nil else {
            transcript = ""
            return
        }
        finalDelivered = true
        task?.cancel()
        transcript = ""
        error = ""
        cleanup()
    }

    private func begin(recognizer: SFSpeechRecognizer, onDevice: Bool) {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = onDevice
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else {
            error = "No usable microphone input is available."
            return
        }
        let feeder = SpeechAudioFeeder(request: request)
        input.installTap(onBus: 0, bufferSize: 1_024, format: format) { @Sendable buffer, _ in
            feeder.append(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            tapInstalled = false
            self.error = "The microphone could not start."
            return
        }
        self.engine = engine
        self.request = request
        tapInstalled = true
        isRecording = true
        let id = UUID()
        recognitionID = id
        task = recognizer.recognitionTask(with: request) { @Sendable [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let isFinal = result?.isFinal ?? false
            let errorText = error?.localizedDescription
            Task { @MainActor in
                guard let self, self.recognitionID == id, !self.finalDelivered else { return }
                if let text {
                    self.transcript = text
                    if isFinal { self.finish() }
                }
                if let errorText, !self.finalDelivered {
                    self.keepPartial(errorText)
                }
            }
        }
    }

    private func finish() {
        guard !finalDelivered else { return }
        finalDelivered = true
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        request?.endAudio()
        task?.finish()
        cleanup()
        if !text.isEmpty { onFinal?(text) }
    }

    private func keepPartial(_ reason: String) {
        guard !finalDelivered else { return }
        finalDelivered = true
        error = reason
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        task?.cancel()
        cleanup()
        if !text.isEmpty { onPartial?(text) }
    }

    private func stopAudio() {
        engine?.stop()
        if tapInstalled, let engine {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        engine = nil
    }

    private func cleanup() {
        finalization?.cancel()
        finalization = nil
        stopAudio()
        request = nil
        task = nil
        isRecording = false
        isFinalizing = false
    }

    private func microphoneAllowed() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { @Sendable granted in
                    continuation.resume(returning: granted)
                }
            }
        default:
            return false
        }
    }

    private func speechAllowed() async -> Bool {
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { @Sendable status in
                    continuation.resume(returning: status == .authorized)
                }
            }
        default:
            return false
        }
    }
}
