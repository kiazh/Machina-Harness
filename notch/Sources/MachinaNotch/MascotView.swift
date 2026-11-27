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
}

/// The notch face: a polished silver disc with a soft drop shadow, an inner
/// rim, a specular highlight, and a small set of expressive eye states.
struct MascotView: View {
    let state: MascotState
    var size: CGFloat = 26

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var closed = false
    @State private var spinning = false
    @State private var look = CGSize.zero
    @State private var floating = false
    @State private var breathing = false
    @State private var gaze = CGSize.zero
    @State private var zphase = false
    @State private var sparkle = false

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
            y: floating ? -size * 0.018 : size * 0.012
        )
        .scaleEffect(breathing ? 1.022 : 0.992)
        .rotationEffect(.degrees(state == .thinking ? 2.0 : 0))
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
            if !reduceMotion {
                spinning = true
                withAnimation(.easeInOut(duration: 4.6).repeatForever(autoreverses: true)) {
                    floating = true
                }
                withAnimation(.easeInOut(duration: 2.8).repeatForever(autoreverses: true)) {
                    breathing = true
                }
                withAnimation(.easeInOut(duration: 1.8).repeatForever(autoreverses: true)) {
                    zphase = true
                }
                scheduleBlink()
                scheduleGaze()
            }
        }
        .onChange(of: state) { _, newValue in
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

    private func scheduleGaze() {
        guard !reduceMotion else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + Double.random(in: 2.4...5.2)) {
            guard state == .idle || state == .waiting else {
                scheduleGaze()
                return
            }
            let x = CGFloat.random(in: -0.045...0.045) * size
            let y = CGFloat.random(in: -0.02...0.03) * size
            withAnimation(.easeInOut(duration: 0.55)) { gaze = CGSize(width: x, height: y) }
            scheduleGaze()
        }
    }

    private func scheduleBlink() {
        guard !reduceMotion else { return }
        let delay = Double.random(in: 3.2...5.8)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            guard state == .idle || state == .waiting else {
                scheduleBlink()
                return
            }
            withAnimation(.easeInOut(duration: 0.10)) { closed = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.13) {
                withAnimation(.easeInOut(duration: 0.12)) { closed = false }
                scheduleBlink()
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
