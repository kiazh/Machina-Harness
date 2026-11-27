import AVFoundation
import Foundation
import Speech

/// Push-to-talk dictation inspired by Handy: press, speak, release, and the
/// transcript lands in the composer. Recognition is on-device by default; the
/// microphone is never open unless the button is held.
@MainActor
final class VoiceInput: ObservableObject {
    @Published var isRecording = false
    @Published var transcript = ""
    @Published var error = ""

    var onFinal: ((String) -> Void)?

    private let recognizer = SFSpeechRecognizer(locale: .current)
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var finalDelivered = false

    func start() {
        guard !isRecording else { return }
        error = ""
        transcript = ""
        finalDelivered = false
        Task { @MainActor in
            guard await microphoneAllowed() else {
                error = "Microphone permission is required for dictation."
                return
            }
            guard await speechAllowed() else {
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

    func stop() {
        guard isRecording else { return }
        engine?.stop()
        engine?.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        isRecording = false
        task?.finish()
    }

    func cancel() {
        task?.cancel()
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
        input.installTap(onBus: 0, bufferSize: 1_024, format: format) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            self.error = "The microphone could not start."
            return
        }
        self.engine = engine
        self.request = request
        isRecording = true
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                if let result {
                    self.transcript = result.bestTranscription.formattedString
                    if result.isFinal { self.finish() }
                }
                if let error, !self.finalDelivered {
                    self.error = error.localizedDescription
                    self.finish()
                }
            }
        }
    }

    private func finish() {
        guard !finalDelivered else { return }
        finalDelivered = true
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        cleanup()
        if !text.isEmpty { onFinal?(text) }
    }

    private func cleanup() {
        engine?.stop()
        engine?.inputNode.removeTap(onBus: 0)
        engine = nil
        request = nil
        task = nil
        isRecording = false
    }

    private func microphoneAllowed() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            return await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: .audio) { granted in
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
                SFSpeechRecognizer.requestAuthorization { status in
                    continuation.resume(returning: status == .authorized)
                }
            }
        default:
            return false
        }
    }
}
