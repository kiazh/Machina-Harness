import SwiftUI

enum MascotState: String, CaseIterable {
    case idle
    case blink
    case thinking
    case processing
    case surprised
    case happy
    case waiting
    case sleeping
    case listening
}

/// The notch face: a polished silver disc with a soft drop shadow, an inner
/// rim, a specular highlight, and a small set of expressive eye states.
struct MascotView: View {
    let state: MascotState
    var size: CGFloat = 26
    /// Calm mode keeps the expressive eyes and discrete blinks/gazes but skips
    /// the continuous floating/breathing loops. Used in the notch where the
    /// face is always visible and idle CPU matters.
    var calm: Bool = false
    /// Paused during island morphs and other full-surface animations.
    var idlePaused: Bool = false

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var closed = false
    @State private var spinning = false
    @State private var look = CGSize.zero
    @State private var floating = false
    @State private var breathing = false
    @State private var gaze = CGSize.zero
    @State private var zphase = false
    @State private var sparkle = false
    @State private var currentState: MascotState = .idle
    @State private var headTilt: Double = 0
    @State private var breath = false
    @State private var bounce: CGFloat = 0
    @State private var squish: CGFloat = 1
    @State private var blinkTask: Task<Void, Never>?
    @State private var gazeTask: Task<Void, Never>?
    @State private var flourishTask: Task<Void, Never>?

    private var eyeWidth: CGFloat { size * 0.115 }
    private var eyeHeight: CGFloat { size * 0.20 }
    private var eyeGap: CGFloat { size * 0.16 }

    var body: some View {
        ZStack {
            face
            eyes
            if state == .processing {
                processingRing
            }
        }
        .frame(width: size, height: size)
        .offset(
            x: state == .sleeping ? 0 : (floating ? size * 0.045 : -size * 0.045),
            y: (floating ? -size * 0.018 : size * 0.012)
                + bounce
                + (breath ? -size * 0.006 : 0)
        )
        .scaleEffect((breathing ? 1.022 : 0.992) * squish)
        .scaleEffect(x: breath ? 1.012 : 1, y: breath ? 0.988 : 1, anchor: .bottom)
        .rotationEffect(.degrees((state == .thinking ? 2.0 : 0) + headTilt))
        .overlay(alignment: .center) {
            if state == .happy {
                blush
            }
            if state == .surprised {
                SurpriseBurst(size: size)
            }
            if state == .sleeping {
                SleepingZs(size: size, phase: zphase)
            }
            if state == .happy {
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.20, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.82))
                    .offset(x: size * 0.34, y: -size * 0.34)
                    .scaleEffect(sparkle ? 1.0 : 0.4)
                    .opacity(sparkle ? 0.9 : 0.0)
            }
        }
        .onAppear {
            currentState = state
            if !reduceMotion {
                spinning = true
                if !calm {
                    withAnimation(.easeInOut(duration: 4.6).repeatForever(autoreverses: true)) {
                        floating = true
                    }
                    withAnimation(.easeInOut(duration: 2.8).repeatForever(autoreverses: true)) {
                        breathing = true
                    }
                    withAnimation(.easeInOut(duration: 1.8).repeatForever(autoreverses: true)) {
                        zphase = true
                    }
                }
                startIdleLoops()
            }
        }
        .onDisappear {
            stopIdleLoops()
        }
        .onChange(of: idlePaused) { _, paused in
            if paused {
                stopIdleLoops()
                withAnimation(nil) {
                    gaze = .zero
                    headTilt = 0
                    breath = false
                    bounce = 0
                    squish = 1
                    closed = false
                }
            } else if !reduceMotion {
                startIdleLoops()
            }
        }
        .onChange(of: state) { oldValue, newValue in
            currentState = newValue
            withAnimation(.spring(response: 0.28, dampingFraction: 0.7)) {
                look = newValue == .thinking
                    ? CGSize(width: size * 0.045, height: -size * 0.035)
                    : newValue == .waiting
                        ? CGSize(width: size * 0.035, height: 0)
                        : .zero
                sparkle = newValue == .happy
            }
            if newValue != .happy {
                sparkle = false
            }
            if newValue == .happy {
                triggerHappyBounce()
            } else if newValue == .listening {
                triggerListeningPerk()
            } else if oldValue == .listening {
                withAnimation(.easeInOut(duration: 0.35)) {
                    headTilt = 0
                    squish = 1
                }
            }
        }
    }

    private var face: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            Color(white: 0.99),
                            Color(white: 0.82),
                            Color(white: 0.52),
                        ],
                        center: .init(x: 0.32, y: 0.24),
                        startRadius: 0,
                        endRadius: size * 0.92
                    )
                )
            Circle()
                .strokeBorder(
                    LinearGradient(
                        colors: [.white.opacity(0.95), .black.opacity(0.38)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: max(1, size * 0.028)
                )
            Circle()
                .stroke(Color.black.opacity(0.30), lineWidth: max(1, size * 0.05))
                .blur(radius: max(0.4, size * 0.035))
                .offset(y: size * 0.035)
                .mask(Circle())
            Ellipse()
                .fill(.white.opacity(0.32))
                .frame(width: size * 0.46, height: size * 0.15)
                .blur(radius: max(0.3, size * 0.015))
                .offset(x: -size * 0.10, y: -size * 0.30)
        }
        .shadow(color: .black.opacity(0.55), radius: size * 0.16, x: 0, y: size * 0.10)
    }

    private var eyes: some View {
        HStack(spacing: eyeGap) {
            eye
            eye
        }
        .offset(x: look.width + gaze.width, y: look.height + gaze.height)
    }

    @ViewBuilder
    private var eye: some View {
        switch state {
        case .happy:
            HappyEye()
                .stroke(Color.black.opacity(0.82), style: StrokeStyle(lineWidth: max(1.1, size * 0.045), lineCap: .round))
                .frame(width: eyeWidth * 1.5, height: eyeHeight * 0.7)
        case .sleeping:
            Capsule()
                .fill(Color.black.opacity(0.72))
                .frame(width: eyeWidth * 1.45, height: max(1, size * 0.035))
        case .blink:
            Capsule()
                .fill(Color.black.opacity(0.78))
                .frame(width: eyeWidth * 1.3, height: max(1, size * 0.04))
        case .surprised:
            Capsule()
                .fill(Color.black.opacity(0.85))
                .frame(width: eyeWidth * 1.25, height: eyeHeight * 1.35)
        case .waiting:
            Capsule()
                .fill(Color.black.opacity(0.82))
                .frame(width: eyeWidth, height: eyeHeight * 0.8)
        case .listening:
            Capsule()
                .fill(Color.black.opacity(0.88))
                .frame(width: eyeWidth * 1.2, height: eyeHeight * 1.25)
        default:
            Capsule()
                .fill(Color.black.opacity(0.86))
                .frame(width: eyeWidth, height: closed ? max(1, size * 0.04) : eyeHeight)
                .overlay(alignment: .topLeading) {
                    if !closed {
                        Circle()
                            .fill(.white.opacity(0.75))
                            .frame(width: size * 0.035, height: size * 0.035)
                            .offset(x: eyeWidth * 0.18, y: eyeHeight * 0.16)
                    }
                }
        }
    }

    private var processingRing: some View {
        Circle()
            .trim(from: 0, to: 0.23)
            .stroke(
                AngularGradient(colors: [.white.opacity(0.15), .white.opacity(0.95)], center: .center),
                style: StrokeStyle(lineWidth: max(1.4, size * 0.045), lineCap: .round)
            )
            .rotationEffect(.degrees(spinning ? 360 : 0))
            .animation(
                reduceMotion ? nil : .linear(duration: 1.15).repeatForever(autoreverses: false),
                value: spinning
            )
            .padding(1)
    }

    private var blush: some View {
        HStack(spacing: size * 0.36) {
            Ellipse()
                .fill(Color(red: 1.0, green: 0.58, blue: 0.58).opacity(0.22))
                .frame(width: size * 0.13, height: size * 0.07)
            Ellipse()
                .fill(Color(red: 1.0, green: 0.58, blue: 0.58).opacity(0.22))
                .frame(width: size * 0.13, height: size * 0.07)
        }
        .offset(y: size * 0.13)
    }

    private func startIdleLoops() {
        guard !reduceMotion, !idlePaused else { return }
        guard ProcessInfo.processInfo.environment["MACHINA_NOTCH_CALM_IDLE"] != "1" else { return }
        startBlinkLoop()
        startGazeLoop()
        startFlourishLoop()
    }

    private func stopIdleLoops() {
        blinkTask?.cancel()
        gazeTask?.cancel()
        flourishTask?.cancel()
        blinkTask = nil
        gazeTask = nil
        flourishTask = nil
    }

    private func startBlinkLoop() {
        blinkTask?.cancel()
        blinkTask = Task { @MainActor in
            while !Task.isCancelled {
                let delay = UInt64(Double.random(in: 3.8...6.5) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: delay)
                if Task.isCancelled { return }
                guard currentState != .sleeping, currentState != .happy else { continue }
                await blinkOnce()
                if Task.isCancelled { return }
                if Bool.random() {
                    try? await Task.sleep(nanoseconds: 140_000_000)
                    if Task.isCancelled { return }
                    await blinkOnce()
                }
            }
        }
    }

    @MainActor
    private func blinkOnce() async {
        withAnimation(.easeInOut(duration: 0.10)) { closed = true }
        try? await Task.sleep(nanoseconds: 120_000_000)
        if Task.isCancelled { return }
        withAnimation(.easeInOut(duration: 0.13)) { closed = false }
    }

    private func startGazeLoop() {
        gazeTask?.cancel()
        gazeTask = Task { @MainActor in
            while !Task.isCancelled {
                let delay = UInt64(Double.random(in: 2.6...6.0) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: delay)
                if Task.isCancelled { return }
                guard currentState != .sleeping, currentState != .happy else { continue }

                let target = gazeTarget(for: currentState)
                withAnimation(.spring(response: 0.24, dampingFraction: 0.82)) {
                    gaze = target
                }
                try? await Task.sleep(nanoseconds: UInt64(Double.random(in: 0.35...1.1) * 1_000_000_000))
                if Task.isCancelled { return }
                withAnimation(.easeInOut(duration: Double.random(in: 0.35...0.6))) {
                    gaze = .zero
                }
            }
        }
    }

    private func gazeTarget(for state: MascotState) -> CGSize {
        switch state {
        case .thinking:
            return CGSize(width: (Bool.random() ? -1 : 1) * size * 0.025, height: -size * 0.030)
        case .processing:
            return CGSize(width: 0, height: -size * 0.012)
        case .listening:
            return CGSize(width: 0, height: -size * 0.008)
        case .waiting:
            return CGSize(width: size * 0.010, height: size * 0.012)
        case .happy, .sleeping:
            return .zero
        default:
            return CGSize(
                width: CGFloat.random(in: -0.055...0.055) * size,
                height: CGFloat.random(in: -0.028...0.030) * size
            )
        }
    }

    private func startFlourishLoop() {
        // The small compact/header faces only get cheap eye saccades and
        // blinks. Whole-face breath/head-tilt flourishes run on the larger
        // hero face so the notch's idle CPU stays low.
        guard size >= 30 else { return }
        flourishTask?.cancel()
        flourishTask = Task { @MainActor in
            while !Task.isCancelled {
                let delay = UInt64(Double.random(in: 9.0...14.0) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: delay)
                if Task.isCancelled { return }

                switch currentState {
                case .sleeping:
                    await breatheSlowly()
                case .idle, .blink, .surprised, .processing:
                    if Bool.random() {
                        await headTiltFlourish()
                    } else {
                        await breatheSlowly()
                    }
                case .thinking:
                    await headTiltFlourish(angle: -1.6)
                case .listening:
                    await headTiltFlourish(angle: -2.2)
                case .waiting:
                    await breatheSlowly()
                case .happy:
                    break
                }
            }
        }
    }

    @MainActor
    private func headTiltFlourish(angle: Double? = nil) async {
        let target = angle ?? (Bool.random() ? 2.2 : -2.2)
        withAnimation(.spring(response: 0.35, dampingFraction: 0.72)) {
            headTilt = target
        }
        try? await Task.sleep(nanoseconds: UInt64(Double.random(in: 0.45...0.7) * 1_000_000_000))
        if Task.isCancelled { return }
        withAnimation(.easeInOut(duration: 0.45)) {
            headTilt = 0
        }
    }

    @MainActor
    private func breatheSlowly() async {
        withAnimation(.easeInOut(duration: 0.7)) { breath = true }
        try? await Task.sleep(nanoseconds: 800_000_000)
        if Task.isCancelled { return }
        withAnimation(.easeInOut(duration: 0.9)) { breath = false }
    }

    private func triggerHappyBounce() {
        guard !reduceMotion, !idlePaused else { return }
        withAnimation(.spring(response: 0.22, dampingFraction: 0.45)) {
            bounce = -size * 0.07
            squish = 1.07
            headTilt = 2.0
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 180_000_000)
            guard !idlePaused else { return }
            withAnimation(.spring(response: 0.32, dampingFraction: 0.55)) {
                bounce = 0
                squish = 1
                headTilt = 0
            }
        }
    }

    private func triggerListeningPerk() {
        guard !reduceMotion, !idlePaused else { return }
        withAnimation(.spring(response: 0.28, dampingFraction: 0.6)) {
            headTilt = -2.0
            squish = 1.04
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 550_000_000)
            guard currentState == .listening, !idlePaused else { return }
            withAnimation(.easeInOut(duration: 0.35)) {
                headTilt = 0
                squish = 1
            }
        }
    }
}

private struct SurpriseBurst: View {
    let size: CGFloat

    var body: some View {
        ZStack {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(.white.opacity(0.72))
                    .frame(width: size * 0.11, height: max(0.8, size * 0.022))
                    .offset(x: size * 0.33, y: -size * 0.30)
                    .rotationEffect(.degrees(Double(index - 1) * 26 - 18 + 30))
            }
        }
    }
}

private struct SleepingZs: View {
    let size: CGFloat
    let phase: Bool

    var body: some View {
        Text("z")
            .font(.system(size: size * 0.24, weight: .bold, design: .rounded))
            .foregroundStyle(.white.opacity(0.55))
            .offset(x: size * 0.34, y: phase ? -size * 0.42 : -size * 0.32)
            .opacity(phase ? 0.85 : 0.35)
            .scaleEffect(phase ? 1.0 : 0.8)
    }
}

private struct HappyEye: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.maxY),
            control: CGPoint(x: rect.midX, y: rect.minY)
        )
        return path
    }
}
