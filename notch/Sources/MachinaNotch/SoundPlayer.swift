import AVFoundation
import Foundation

enum SoundCue {
    case chirp
    case happy
    case alert
    case boop
    case sleep
    case wake
}

/// Tiny synthesized, non-verbal cues. No speech, no samples to ship, no
/// always-on microphone. Default volume is deliberately quiet.
@MainActor
final class SoundPlayer {
    static let shared = SoundPlayer()

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let sampleRate = 44_100.0
    private var started = false

    var muted: Bool {
        get { UserDefaults.standard.bool(forKey: "machina.notch.muted") }
        set { UserDefaults.standard.set(newValue, forKey: "machina.notch.muted") }
    }

    private init() {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: nil)
        engine.mainMixerNode.outputVolume = 0.16
    }

    func play(_ cue: SoundCue) {
        guard !muted else { return }
        do {
            if !started {
                try engine.start()
                started = true
            }
            if !player.isPlaying { player.play() }
            player.scheduleBuffer(buffer(for: cue), at: nil, options: .interrupts)
        } catch {
            // A missing audio route is never a reason to break the notch.
        }
    }

    private func buffer(for cue: SoundCue) -> AVAudioPCMBuffer {
        let notes: [(Double, Double, Double)] // frequency, start, duration
        switch cue {
        case .chirp:
            notes = [(880, 0.00, 0.07), (1_320, 0.08, 0.09)]
        case .happy:
            notes = [(784, 0.00, 0.09), (988, 0.10, 0.09), (1_318, 0.20, 0.14)]
        case .alert:
            notes = [(660, 0.00, 0.10), (880, 0.13, 0.12)]
        case .boop:
            notes = [(420, 0.00, 0.12), (300, 0.10, 0.16)]
        case .sleep:
            notes = [(392, 0.00, 0.22)]
        case .wake:
            notes = [(523, 0.00, 0.10)]
        }
        let duration = max(0.25, (notes.map { $0.1 + $0.2 }.max() ?? 0.25) + 0.05)
        let frames = AVAudioFrameCount(duration * sampleRate)
        let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buffer.frameLength = frames
        guard let data = buffer.floatChannelData?[0] else { return buffer }
        for index in 0..<Int(frames) { data[index] = 0 }
        for (frequency, start, length) in notes {
            let begin = Int(start * sampleRate)
            let count = min(Int(length * sampleRate), Int(frames) - begin)
            guard count > 0 else { continue }
            for offset in 0..<count {
                let progress = Double(offset) / Double(count)
                let envelope = sin(.pi * progress) * 0.9
                let sample = sin(2 * .pi * frequency * Double(offset) / sampleRate) * envelope
                data[begin + offset] += Float(sample * 0.8)
            }
        }
        return buffer
    }
}
