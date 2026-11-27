import SwiftUI

/// The island surface. Hover is intentionally *not* handled here: AppKit
/// tracking-area callbacks can fire while `NSHostingView` is swapping the
/// hovered hierarchy, so `NotchPanelController` owns pointer tracking and
/// defers every expand/collapse one runloop turn.
struct NotchView: View {
    @ObservedObject var model: NotchModel
    let controller: NotchPanelController

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var composerFocused: Bool
    @State private var mascotDrag = CGSize.zero
    @StateObject private var voice = VoiceInput()

    private var metrics: IslandMetrics { model.metrics }

    var body: some View {
        ZStack(alignment: .top) {
            Color.clear
            VStack(spacing: IslandMetrics.bannerGap) {
                island
                if !model.expanded, let banner = model.banner {
                    BannerView(
                        banner: banner,
                        metrics: metrics,
                        onOpen: { openFromClick() },
                        onDismiss: { model.dismissBanner() }
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .animation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.9), value: model.banner)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .environment(\.colorScheme, .dark)
        .onAppear {
            voice.onFinal = { text in
                model.draft = text
                model.send()
            }
        }
        .onChange(of: voice.isRecording) { _, recording in
            model.listening = recording
            model.mascot = recording ? .listening : (model.approvals.isEmpty ? .idle : .waiting)
        }
        .onChange(of: composerFocused) { _, focused in
            model.composerFocused = focused
            controller.debugLog("composerFocused=\(focused)")
            if focused { controller.cancelCollapse() }
        }
        .onChange(of: model.draft) { _, text in
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                controller.cancelCollapse()
            }
        }
        .onChange(of: model.expanded) { _, expanded in
            if !expanded {
                composerFocused = false
                model.composerFocused = false
            }
        }
        .onChange(of: model.focusComposerTick) { _, _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) {
                guard model.expanded else { return }
                composerFocused = true
            }
        }
        .onExitCommand { controller.collapse() }
    }

    // MARK: - Island shell

    private var island: some View {
        let expanded = model.expanded
        let width = expanded ? metrics.expandedWidth : metrics.compactWidth
        let height = expanded ? metrics.expandedHeight : metrics.notchHeight
        let topRadius: CGFloat = expanded ? 15 : (metrics.hasNotch ? 6 : 10)
        let bottomRadius: CGFloat = expanded ? 30 : max(12, height * 0.42)
        let shape = IslandSilhouette(topFlare: topRadius, bottomRadius: bottomRadius)

        return ZStack(alignment: .top) {
            IslandBackground(expanded: expanded, inset: metrics.expandedContentInset)

            if expanded {
                expandedContent
                    .transition(.opacity)
            } else {
                compactContent
                    .transition(.opacity)
            }
        }
        .frame(width: width, height: height, alignment: .top)
        .clipShape(shape)
        .overlay {
            shape.stroke(
                LinearGradient(
                    colors: expanded
                        ? [.white.opacity(0.24), .white.opacity(0.03), .white.opacity(0.14)]
                        : [.white.opacity(0.10), .white.opacity(0.02), .white.opacity(0.05)],
                    startPoint: .top,
                    endPoint: .bottom
                ),
                lineWidth: 0.9
            )
        }
        .compositingGroup()
        .shadow(
            color: .black.opacity(expanded ? 0.58 : 0.34),
            radius: expanded ? 28 : 13,
            y: expanded ? 14 : 6
        )
        .contentShape(shape)
        .animation(
            reduceMotion ? nil : .spring(response: 0.42, dampingFraction: 0.82),
            value: model.expanded
        )
    }

    // MARK: - Compact

    private var compactContent: some View {
        let wing: CGFloat = 52
        return HStack(spacing: 0) {
            HStack(spacing: 5) {
                mascot(size: 22, maxX: 42, maxY: 10)
                statusDot(size: 5)
            }
            .padding(.leading, 10)
            .frame(width: wing, alignment: .leading)

            Spacer(minLength: metrics.hasNotch ? metrics.notchWidth : 44)

            compactTrailing
                .padding(.trailing, 10)
                .frame(width: wing, alignment: .trailing)
        }
        .frame(height: metrics.notchHeight)
        .contentShape(Rectangle())
        .onTapGesture { openFromClick() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Maki, \(model.agentStateLabel)")
        .accessibilityHint("Click to open")
        .help("Maki · \(model.agentStateLabel)")
    }

    @ViewBuilder
    private var compactTrailing: some View {
        if !model.approvals.isEmpty {
            HStack(spacing: 4) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 9.5, weight: .semibold))
                Text("\(model.approvals.count)")
                    .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                    .monospacedDigit()
            }
            .foregroundStyle(IslandPalette.amber)
            .accessibilityLabel("\(model.approvals.count) approvals waiting")
        } else if model.busy, let start = model.turnStartedAt {
            HStack(spacing: 4) {
                ActivityOrb()
                ElapsedText(since: start, size: 9.5, color: .white.opacity(0.62))
            }
            .accessibilityLabel("Maki is working")
        } else {
            statusDot(size: 5)
        }
    }

    // MARK: - Expanded

    private var expandedContent: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: metrics.expandedContentInset)
            header
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 0.8)
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 6)

            transcript

            composer
                .padding(.horizontal, 16)
                .padding(.top, 8)
            footer
                .padding(.horizontal, 16)
                .padding(.top, 7)
                .padding(.bottom, 12)
        }
        .frame(width: metrics.expandedWidth, height: metrics.expandedHeight, alignment: .top)
        .simultaneousGesture(
            TapGesture().onEnded { controller.activate() }
        )
    }

    private var header: some View {
        HStack(spacing: 10) {
            mascot(size: 38, maxX: 60, maxY: 20)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(model.status?.profile.name ?? "Maki")
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)
                    if model.busy {
                        ActivityOrb()
                    }
                }
                HStack(spacing: 5) {
                    Text(subtitle)
                        .font(.system(size: 10.5, weight: .regular, design: .rounded))
                        .foregroundStyle(.white.opacity(0.52))
                        .lineLimit(1)
                    if model.busy, let start = model.turnStartedAt {
                        Text("·")
                            .font(.system(size: 10.5))
                            .foregroundStyle(.white.opacity(0.28))
                        ElapsedText(since: start, size: 10, color: .white.opacity(0.72))
                    }
                }
            }

            Spacer(minLength: 4)

            IconButton(
                systemName: model.muted ? "speaker.slash.fill" : "speaker.wave.1.fill",
                label: model.muted ? "Unmute sounds" : "Mute sounds"
            ) {
                model.toggleMute()
            }
            IconButton(systemName: "pause.fill", label: "Pause resident") {
                model.pause()
            }
            IconButton(systemName: "chevron.up", label: "Collapse island") {
                controller.collapse()
            }
            IconButton(systemName: "xmark", label: "Hide Maki") {
                controller.hide()
            }
        }
        .padding(.horizontal, 16)
        .frame(height: 46)
    }

    private var subtitle: String {
        if !model.approvals.isEmpty {
            return model.approvals.count == 1
                ? "Waiting for your approval"
                : "\(model.approvals.count) approvals waiting"
        }
        if model.listening { return "Listening…" }
        if model.connection != "live" { return "Offline · demo" }
        guard let status = model.status else { return "Connected" }
        return "\(status.profile.autonomyMode) · \(status.coreMemoryCount) facts · live"
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 9) {
                    if model.messages.isEmpty && model.approvals.isEmpty {
                        emptyState
                    }
                    ForEach(model.messages) { line in
                        message(line)
                    }
                    if !model.approvals.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Waiting for you")
                                .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                                .foregroundStyle(.white.opacity(0.5))
                                .padding(.top, 2)
                            ForEach(Array(model.approvals.prefix(3).enumerated()), id: \.element.id) { index, approval in
                                ApprovalCard(
                                    approval: approval,
                                    primary: index == 0,
                                    onApprove: {
                                        controller.resolveApproval(approval, verdict: "approved")
                                    },
                                    onDeny: {
                                        controller.resolveApproval(approval, verdict: "denied")
                                    }
                                )
                            }
                        }
                        .padding(.top, 2)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 4)
            }
            .scrollIndicators(.hidden)
            .onChange(of: model.messages.count) { _, _ in
                scrollToBottom(proxy)
            }
            .onChange(of: model.approvals.count) { _, _ in
                scrollToBottom(proxy)
            }
        }
        .frame(maxHeight: .infinity)
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.65))
                Text("Say hi to Maki")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white.opacity(0.88))
            }
            Text("Ask about your screen, queue a task, or just chat. Maki lives in the notch.")
                .font(.system(size: 11, design: .rounded))
                .foregroundStyle(.white.opacity(0.42))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 14)
        .padding(.bottom, 6)
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
                        .fill(line.fromUser ? Color.white.opacity(0.90) : Color.white.opacity(0.075))
                )
            if !line.fromUser { Spacer(minLength: 30) }
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
                    .accessibilityLabel("Message Maki")

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
                .accessibilityLabel(voice.isRecording ? "Stop dictation" : "Dictate")
                .help("Push to talk")

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
                .accessibilityLabel("Send screenshot")
                .help("Send a screenshot")

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
                .accessibilityLabel("Send message")
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
                Button("Start") {
                    GatewayLauncher.shared.ensureRunning()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { model.refresh() }
                }
                .buttonStyle(.plain)
                .font(.system(size: 10, weight: .semibold, design: .rounded))
                .foregroundStyle(.white.opacity(0.8))
                .accessibilityLabel("Start the gateway")
            }
            Spacer()
            Text("esc close")
                .font(.system(size: 10, design: .rounded))
                .foregroundStyle(.white.opacity(0.3))
        }
    }

    // MARK: - Small pieces

    private func statusDot(size: CGFloat) -> some View {
        Circle()
            .fill(
                model.connection == "live"
                    ? Color(red: 0.38, green: 0.86, blue: 0.58)
                    : Color.orange
            )
            .frame(width: size, height: size)
            .shadow(color: (model.connection == "live" ? Color.green : Color.orange).opacity(0.4), radius: 3)
            .accessibilityLabel(model.connection == "live" ? "Connected" : "Offline")
    }

    private func mascot(size: CGFloat, maxX: CGFloat, maxY: CGFloat) -> some View {
        MascotView(state: model.mascot, size: size)
            .offset(mascotDrag)
            .gesture(
                DragGesture(minimumDistance: 2)
                    .onChanged { value in
                        mascotDrag = CGSize(
                            width: max(-maxX, min(maxX, value.translation.width)),
                            height: max(-maxY, min(maxY, value.translation.height))
                        )
                    }
                    .onEnded { _ in
                        withAnimation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 0.55)) {
                            mascotDrag = .zero
                        }
                        model.pet()
                    }
            )
            .accessibilityLabel("Maki")
            .accessibilityHint("Drag to pet")
            .help("Say hi")
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.18)) {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }

    private func openFromClick() {
        controller.debugLog("swiftui compact tap")
        // One runloop turn: SwiftUI gesture handling finishes before the
        // island hierarchy is swapped by the controller.
        DispatchQueue.main.async {
            controller.expand(activate: true)
        }
    }
}

// MARK: - Background

private struct IslandBackground: View {
    let expanded: Bool
    let inset: CGFloat

    var body: some View {
        ZStack {
            Color.black

            if expanded {
                VisualEffectView(material: .hudWindow, blendingMode: .behindWindow)
                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.42), location: 0),
                        .init(color: .black.opacity(0.70), location: 0.22),
                        .init(color: .black.opacity(0.82), location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
                RadialGradient(
                    colors: [Color(white: 0.92).opacity(0.10), .clear],
                    center: UnitPoint(x: 0.16, y: 0.10),
                    startRadius: 0,
                    endRadius: 260
                )
                .blendMode(.plusLighter)

                // Keep the strip behind the camera housing black so the
                // hardware cutout and our surface read as one piece.
                VStack(spacing: 0) {
                    LinearGradient(
                        colors: [.black, .black.opacity(0.55), .clear],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .frame(height: max(inset + 8, 36))
                    Spacer(minLength: 0)
                }
                .allowsHitTesting(false)
            } else {
                LinearGradient(
                    colors: [.black, Color(white: 0.018)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                LinearGradient(
                    colors: [.white.opacity(0.035), .clear],
                    startPoint: .top,
                    endPoint: .center
                )
            }
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Activity

private struct ActivityOrb: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulsing = false

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Color(white: 0.94))
                    .frame(width: 2.5, height: 2.5)
                    .opacity(reduceMotion ? 0.9 : (pulsing ? 1 : 0.32))
                    .scaleEffect(reduceMotion ? 1 : (pulsing ? 1 : 0.55))
                    .animation(
                        reduceMotion
                            ? nil
                            : .easeInOut(duration: 0.55)
                                .repeatForever(autoreverses: true)
                                .delay(Double(index) * 0.13),
                        value: pulsing
                    )
            }
        }
        .frame(width: 12, height: 12)
        .onAppear {
            guard !reduceMotion else { return }
            pulsing = true
        }
        .accessibilityHidden(true)
    }
}

private struct ElapsedText: View {
    let since: Date
    var size: CGFloat = 10
    var color: Color = .white.opacity(0.65)

    var body: some View {
        TimelineView(.periodic(from: since, by: 1)) { context in
            Text(NotchModel.elapsedLabel(context.date.timeIntervalSince(since)))
                .font(.system(size: size, weight: .medium, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(color)
        }
    }
}

// MARK: - Banner

private struct BannerView: View {
    let banner: NotchBanner
    let metrics: IslandMetrics
    let onOpen: () -> Void
    let onDismiss: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 9) {
            Button(action: onOpen) {
                HStack(spacing: 9) {
                    MascotView(
                        state: banner.style == .done ? .happy : .waiting,
                        size: 25
                    )
                    VStack(alignment: .leading, spacing: 1) {
                        Text(banner.title)
                            .font(.system(size: 11.5, weight: .semibold, design: .rounded))
                            .foregroundStyle(.white.opacity(0.92))
                        Text(banner.message)
                            .font(.system(size: 10.5, design: .rounded))
                            .foregroundStyle(.white.opacity(0.56))
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: banner.style == .done ? "checkmark.circle.fill" : "hand.raised.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(banner.style == .done ? Color.green : IslandPalette.amber)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(banner.title): \(banner.message)")
            .accessibilityHint("Open Maki")

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 8.5, weight: .bold))
                    .foregroundStyle(.white.opacity(0.4))
                    .frame(width: 20, height: 20)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss notification")
        }
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .frame(width: IslandMetrics.bannerWidth, height: IslandMetrics.bannerHeight)
        .background(
            RoundedRectangle(cornerRadius: IslandMetrics.bannerHeight / 2, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [Color(white: 0.10), Color(white: 0.025)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .overlay(
                    RoundedRectangle(cornerRadius: IslandMetrics.bannerHeight / 2, style: .continuous)
                        .stroke(.white.opacity(0.10), lineWidth: 0.8)
                )
        )
        .compositingGroup()
        .shadow(color: .black.opacity(0.44), radius: 16, y: 8)
        .animation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.84), value: banner.id)
    }
}

// MARK: - Approvals

private struct ApprovalCard: View {
    let approval: ResidentApproval
    let primary: Bool
    let onApprove: () -> Void
    let onDeny: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(IslandPalette.amber)
                    .padding(.top, 1)

                VStack(alignment: .leading, spacing: 2) {
                    Text(approval.operation.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.90))
                    Text(approval.target)
                        .font(.system(size: 9.5, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.48))
                        .lineLimit(2)
                        .truncationMode(.middle)
                }

                Spacer(minLength: 4)

                if primary {
                    Text("⌘Y allow · ⌘N deny")
                        .font(.system(size: 8.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(.white.opacity(0.30))
                        .fixedSize()
                }
            }

            if let preview = approval.diffText ?? approval.previewText {
                ApprovalPreview(
                    text: preview,
                    isDiff: approval.diffText != nil,
                    truncated: approval.previewTruncated
                )
            }

            approvalButtons
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(.white.opacity(0.05))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(IslandPalette.amber.opacity(0.20), lineWidth: 0.8)
                )
        )
    }

    private var approvalButtons: some View {
        HStack(spacing: 6) {
            if primary {
                Button(action: onDeny) {
                    denyLabel
                }
                .buttonStyle(.plain)
                .keyboardShortcut("n", modifiers: .command)
                .accessibilityLabel("Deny \(approval.operation)")
            } else {
                Button(action: onDeny) {
                    denyLabel
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Deny \(approval.operation)")
            }

            if primary {
                Button(action: onApprove) {
                    approveLabel
                }
                .buttonStyle(.plain)
                .keyboardShortcut("y", modifiers: .command)
                .accessibilityLabel("Approve \(approval.operation)")
            } else {
                Button(action: onApprove) {
                    approveLabel
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Approve \(approval.operation)")
            }

            Spacer(minLength: 0)
        }
    }

    private var denyLabel: some View {
        Text("Deny")
            .font(.system(size: 10, weight: .semibold, design: .rounded))
            .foregroundStyle(.white.opacity(0.78))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(.white.opacity(0.10)))
            .overlay(Capsule().stroke(.white.opacity(0.08), lineWidth: 0.6))
    }

    private var approveLabel: some View {
        Text("Approve")
            .font(.system(size: 10, weight: .semibold, design: .rounded))
            .foregroundStyle(.black)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(IslandPalette.amber))
            .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 0.6))
    }
}

private struct ApprovalPreview: View {
    let text: String
    let isDiff: Bool
    let truncated: Bool

    private var lines: [String] {
        text.components(separatedBy: "\n")
            .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
                Text(isDiff ? "CHANGES" : "PREVIEW")
                    .font(.system(size: 8, weight: .semibold, design: .monospaced))
                    .tracking(0.7)
                    .foregroundStyle(.white.opacity(0.34))
                if truncated {
                    Text("TRUNCATED")
                        .font(.system(size: 8, weight: .medium, design: .monospaced))
                        .foregroundStyle(IslandPalette.amber.opacity(0.65))
                }
                Spacer(minLength: 0)
            }
            VStack(alignment: .leading, spacing: 0.5) {
                ForEach(Array(lines.prefix(6).enumerated()), id: \.offset) { _, line in
                    Text(display(line))
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(color(for: line))
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.black.opacity(0.38))
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(.white.opacity(0.055), lineWidth: 0.7)
                )
        )
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(isDiff ? "Changes" : "Content preview")")
    }

    private func display(_ line: String) -> String {
        if isDiff { return String(line.prefix(160)) }
        return "+ " + String(line.prefix(158))
    }

    private func color(for line: String) -> Color {
        if !isDiff { return Color(red: 0.56, green: 0.82, blue: 0.62).opacity(0.88) }
        if line.hasPrefix("@@") { return IslandPalette.amber.opacity(0.85) }
        if line.hasPrefix("+++") || line.hasPrefix("---") { return .white.opacity(0.35) }
        if line.hasPrefix("+") { return Color(red: 0.52, green: 0.86, blue: 0.61).opacity(0.9) }
        if line.hasPrefix("-") { return Color(red: 0.94, green: 0.52, blue: 0.50).opacity(0.88) }
        return .white.opacity(0.52)
    }
}

// MARK: - Buttons and palette

private struct IconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(0.58))
                .frame(width: 25, height: 25)
                .background(Circle().fill(.white.opacity(0.06)))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .help(label)
    }
}

private enum IslandPalette {
    static let amber = Color(red: 0.96, green: 0.76, blue: 0.34)
}
