import AVFoundation
import Foundation

enum SoundCue: CaseIterable {
    case chirp
    case happy
    case alert
    case boop
    case sleep
    case wake
}

/// Tiny synthesized, non-verbal cues. No speech, no samples to ship, no
/// always-on microphone. Default volume is deliberately quiet.
///
/// Sounds are rendered to in-memory WAV data and played through
/// `AVAudioPlayer`. The previous AVAudioEngine player node aborted on some
/// audio routes when `scheduleBuffer` was handed a format the engine had not
/// adopted; a data-backed player is route-independent and cannot throw an
/// Objective-C exception out of `play()`.
@MainActor
final class SoundPlayer {
    static let shared = SoundPlayer()

    private let sampleRate = 44_100.0
    private var cache: [SoundCue: Data] = [:]
    private var players: [AVAudioPlayer] = []

    var muted: Bool {
        get { UserDefaults.standard.bool(forKey: "machina.notch.muted") }
        set { UserDefaults.standard.set(newValue, forKey: "machina.notch.muted") }
    }

    private init() {
        for cue in SoundCue.allCases {
            cache[cue] = render(cue)
        }
    }

    func play(_ cue: SoundCue) {
        guard !muted else { return }
        players.removeAll { !$0.isPlaying }
        let data = cache[cue] ?? render(cue)
        cache[cue] = data
        guard let player = try? AVAudioPlayer(data: data) else { return }
        player.volume = 0.25
        player.prepareToPlay()
        player.play()
        players.append(player)
        if players.count > 4 { players.removeFirst() }
    }

    // MARK: - Synthesis

    private func render(_ cue: SoundCue) -> Data {
        let notes: [(frequency: Double, start: Double, duration: Double)]
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

        let length = max(0.25, (notes.map { $0.start + $0.duration }.max() ?? 0.2) + 0.07)
        let frames = Int(length * sampleRate)
        var samples = [Float](repeating: 0, count: frames)

        for note in notes {
            let begin = Int(note.start * sampleRate)
            let count = min(Int(note.duration * sampleRate), frames - begin)
            guard count > 0 else { continue }
            let attack = max(1, min(count, Int(0.005 * sampleRate)))
            for offset in 0..<count {
                let t = Double(offset) / sampleRate
                let progress = Double(offset) / Double(count)
                let rise = offset < attack ? Double(offset) / Double(attack) : 1
                let envelope = sin(.pi * progress) * rise
                let sample = sin(2 * .pi * note.frequency * t) * envelope
                samples[begin + offset] += Float(sample * 0.8)
            }
        }

        // Normalise to -6 dBFS, then fade the tail so endings never click.
        var peak: Float = 0.0001
        for sample in samples { peak = max(peak, abs(sample)) }
        let gain = 0.5 / peak
        let fadeFrames = max(1, Int(0.01 * sampleRate))
        for index in samples.indices {
            let tail: Float = index > frames - fadeFrames
                ? Float(frames - index) / Float(fadeFrames)
                : 1
            samples[index] = max(-1, min(1, samples[index] * gain * tail))
        }

        var pcm = [Int16]()
        pcm.reserveCapacity(frames)
        for sample in samples {
            pcm.append(Int16(sample * Float(Int16.max)))
        }
        return wav(pcm)
    }

    private func wav(_ pcm: [Int16]) -> Data {
        var data = Data()
        func u32(_ value: Int) {
            var little = UInt32(value).littleEndian
            data.append(Data(bytes: &little, count: 4))
        }
        func u16(_ value: Int) {
            var little = UInt16(value).littleEndian
            data.append(Data(bytes: &little, count: 2))
        }
        let byteCount = pcm.count * 2
        data.append(Data("RIFF".utf8)); u32(36 + byteCount)
        data.append(Data("WAVE".utf8))
        data.append(Data("fmt ".utf8)); u32(16); u16(1); u16(1)
        u32(Int(sampleRate)); u32(Int(sampleRate) * 2); u16(2); u16(16)
        data.append(Data("data".utf8)); u32(byteCount)
        pcm.withUnsafeBufferPointer { data.append(Data(buffer: $0)) }
        return data
    }
}
