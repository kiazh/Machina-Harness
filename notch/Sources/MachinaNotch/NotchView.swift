import SwiftUI

struct NotchView: View {
    @ObservedObject var model: NotchModel
    let controller: NotchPanelController
    @FocusState private var composerFocused: Bool
    @State private var mascotDrag = CGSize.zero
    @StateObject private var voice = VoiceInput()

    var body: some View {
        Group {
            if model.expanded { expanded } else { collapsed }
        }
        .animation(.spring(response: 0.34, dampingFraction: 0.84), value: model.expanded)
        .onHover { hovering in
            if hovering {
                controller.expand(activate: false)
            } else {
                controller.collapseSoonIfNotKey()
            }
        }
        .onExitCommand { controller.collapse() }
        .onAppear {
            voice.onFinal = { text in
                model.draft = text
                model.send()
            }
        }
        .onChange(of: model.expanded) { _, expanded in
            guard expanded else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.28) {
                composerFocused = true
            }
        }
    }

    private var collapsed: some View {
        HStack(spacing: 8) {
            MascotView(state: model.mascot, size: 22)
                .offset(mascotDrag)
                .gesture(
                    DragGesture(minimumDistance: 2)
                        .onChanged { value in
                            mascotDrag = CGSize(
                                width: max(-42, min(42, value.translation.width)),
                                height: max(-10, min(10, value.translation.height))
                            )
                        }
                        .onEnded { _ in
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.55)) {
                                mascotDrag = .zero
                            }
                            model.pet()
                        }
                )
            Circle()
                .fill(model.connection == "live" ? Color.green : Color.orange)
                .frame(width: 6, height: 6)
            if model.approvals.count > 0 {
                Text("\(model.approvals.count)")
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(.white.opacity(0.92)))
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 34)
        .contentShape(Capsule(style: .continuous))
        .onTapGesture { controller.expand(activate: true) }
        .background(
            Capsule(style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [Color(white: 0.16), Color(white: 0.04)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .overlay(
                    Capsule(style: .continuous)
                        .stroke(
                            LinearGradient(
                                colors: [.white.opacity(0.28), .white.opacity(0.03)],
                                startPoint: .top,
                                endPoint: .bottom
                            ),
                            lineWidth: 0.8
                        )
                )
                .shadow(color: .black.opacity(0.55), radius: 8, y: 3)
        )
    }

    private var expanded: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Rectangle()
                .fill(.white.opacity(0.09))
                .frame(height: 1)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(model.messages) { line in
                        message(line)
                    }
                    if !model.approvals.isEmpty {
                        approvals
                    }
                }
                .padding(.vertical, 2)
            }
            composer
            footer
        }
        .padding(16)
        .frame(width: 420, height: 560, alignment: .top)
        .background(glass)
        .compositingGroup()
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .stroke(
                    LinearGradient(
                        colors: [.white.opacity(0.34), .white.opacity(0.04)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 0.8
                )
        )
        .shadow(color: .black.opacity(0.58), radius: 26, y: 12)
        .simultaneousGesture(
            TapGesture().onEnded { controller.activate() }
        )
    }

    private var glass: some View {
        ZStack {
            VisualEffectView(material: .hudWindow, blendingMode: .behindWindow)
            LinearGradient(
                colors: [Color.black.opacity(0.30), Color.black.opacity(0.62)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            MascotView(state: model.mascot, size: 42)
                .offset(mascotDrag)
                .gesture(
                    DragGesture(minimumDistance: 2)
                        .onChanged { value in
                            mascotDrag = CGSize(
                                width: max(-60, min(60, value.translation.width)),
                                height: max(-20, min(20, value.translation.height))
                            )
                        }
                        .onEnded { _ in
                            withAnimation(.spring(response: 0.35, dampingFraction: 0.55)) {
                                mascotDrag = .zero
                            }
                            model.pet()
                        }
                )
            VStack(alignment: .leading, spacing: 1) {
                Text(model.status?.profile.name ?? "Maki")
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                Text(subtitle)
                    .font(.system(size: 11, weight: .regular, design: .rounded))
                    .foregroundStyle(.white.opacity(0.55))
            }
            Spacer()
            if model.approvals.count > 0 {
                Text("\(model.approvals.count) waiting")
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Color(red: 0.96, green: 0.76, blue: 0.34)))
            }
            Button {
                model.toggleMute()
            } label: {
                Image(systemName: model.muted ? "speaker.slash.fill" : "speaker.wave.1.fill")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.55))
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
            Button {
                model.pause()
            } label: {
                Image(systemName: "pause.fill")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.white.opacity(0.65))
                    .frame(width: 24, height: 24)
                    .background(Circle().fill(.white.opacity(0.08)))
            }
            .buttonStyle(.plain)
            Button {
                controller.collapse()
            } label: {
                Image(systemName: "chevron.up")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.55))
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
            Button {
                controller.hide()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.white.opacity(0.45))
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
        }
    }

    private var subtitle: String {
        guard let status = model.status else { return model.connection == "live" ? "connected" : "offline" }
        let mode = status.profile.autonomyMode
        return "\(mode) · \(status.coreMemoryCount) facts · \(model.connection)"
    }

    private func message(_ line: NotchModel.ChatLine) -> some View {
        HStack {
            if line.fromUser { Spacer(minLength: 30) }
            Text(line.text)
                .font(.system(size: 12, weight: .regular, design: .rounded))
                .foregroundStyle(line.fromUser ? .black : .white.opacity(0.86))
                .padding(.horizontal, 11)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 13, style: .continuous)
                        .fill(line.fromUser ? Color.white.opacity(0.90) : Color.white.opacity(0.08))
                )
            if !line.fromUser { Spacer(minLength: 30) }
        }
    }

    private var approvals: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Waiting for you")
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(.white.opacity(0.55))
            ForEach(model.approvals.prefix(3)) { approval in
                VStack(alignment: .leading, spacing: 6) {
                    Text(approval.operation.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.9))
                    Text(approval.target)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(2)
                    HStack(spacing: 8) {
                        Button("Approve") { model.resolve(approval, verdict: "approved") }
                            .buttonStyle(GlossButton(tint: Color(red: 0.96, green: 0.82, blue: 0.48)))
                        Button("Deny") { model.resolve(approval, verdict: "denied") }
                            .buttonStyle(GlossButton(tint: .white.opacity(0.16)))
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .fill(.white.opacity(0.055))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .stroke(.white.opacity(0.08), lineWidth: 1)
                        )
                )
            }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            if voice.isRecording || !voice.transcript.isEmpty {
                Text(voice.transcript.isEmpty ? "Listening…" : voice.transcript)
                    .font(.system(size: 11, design: .rounded))
                    .foregroundStyle(.white.opacity(0.65))
                    .lineLimit(2)
            }
            if !voice.error.isEmpty {
                Text(voice.error)
                    .font(.system(size: 10, design: .rounded))
                    .foregroundStyle(.orange.opacity(0.9))
                    .lineLimit(2)
            }
            HStack(spacing: 8) {
            TextField("Ask Maki…", text: $model.draft)
                .textFieldStyle(.plain)
                .font(.system(size: 12, design: .rounded))
                .foregroundStyle(.white.opacity(0.9))
                .focused($composerFocused)
                .onSubmit { model.send() }
            Button {
                if voice.isRecording { voice.stop() } else { voice.start() }
            } label: {
                Image(systemName: voice.isRecording ? "mic.fill" : "mic")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(voice.isRecording ? Color(red: 1, green: 0.45, blue: 0.45) : .white.opacity(0.65))
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(.white.opacity(voice.isRecording ? 0.16 : 0.07)))
            }
            .buttonStyle(.plain)
            .onChange(of: voice.isRecording) { _, recording in
                model.listening = recording
                model.mascot = recording ? .listening : .idle
            }
            Button {
                model.sendScreenshot()
            } label: {
                Image(systemName: "camera.viewfinder")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.65))
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(.white.opacity(0.07)))
            }
            .buttonStyle(.plain)
            Button {
                model.send()
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.black)
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(.white.opacity(model.draft.isEmpty ? 0.25 : 0.9)))
            }
            .buttonStyle(.plain)
            .disabled(model.draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.white.opacity(0.07))
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(.white.opacity(0.08), lineWidth: 1)
                    )
            )
        }
    }

    private var footer: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(model.connection == "live" ? Color.green : Color.orange)
                .frame(width: 5, height: 5)
            Text(model.connection == "live" ? "Resident connected" : "Offline · demo")
                .font(.system(size: 10, design: .rounded))
                .foregroundStyle(.white.opacity(0.4))
            if model.connection != "live" {
                Button("Start") { GatewayLauncher.shared.ensureRunning() }
                    .buttonStyle(.plain)
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.8))
            }
            Spacer()
            Text("esc close")
                .font(.system(size: 10, design: .rounded))
                .foregroundStyle(.white.opacity(0.3))
        }
    }
}

private struct GlossButton: ButtonStyle {
    let tint: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 10, weight: .semibold, design: .rounded))
            .foregroundStyle(tint == .white.opacity(0.16) ? .white.opacity(0.8) : .black)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(
                Capsule()
                    .fill(tint)
                    .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 0.6))
            )
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}
