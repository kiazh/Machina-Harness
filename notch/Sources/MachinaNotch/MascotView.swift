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
        .onAppear {
            if !reduceMotion {
                spinning = true
                scheduleBlink()
            }
        }
        .onChange(of: state) { _, newValue in
            withAnimation(.spring(response: 0.28, dampingFraction: 0.7)) {
                look = newValue == .thinking ? CGSize(width: size * 0.035, height: -size * 0.025) : .zero
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
        .offset(look)
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
