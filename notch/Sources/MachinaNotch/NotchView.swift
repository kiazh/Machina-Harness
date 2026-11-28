import AppKit
import SwiftUI

// This island UI is derived from NotchAgents (MIT).
// See THIRD_PARTY_NOTICES.md for the full notice.

// MARK: - Shape and motion

/// The island silhouette: flared top corners that blend into the menu bar,
/// straight sides, and rounded bottom corners. Both radii animate so the
/// compact and expanded states morph as one piece.
struct NotchShape: Shape {
    var topRadius: CGFloat
    var bottomRadius: CGFloat

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { .init(topRadius, bottomRadius) }
        set {
            topRadius = newValue.first
            bottomRadius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let top = topRadius
        let bottom = min(bottomRadius, (rect.height - top) / 2, (rect.width - 2 * top) / 2)
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addCurve(
            to: CGPoint(x: rect.minX + top, y: rect.minY + top),
            control1: CGPoint(x: rect.minX + top * 0.62, y: rect.minY),
            control2: CGPoint(x: rect.minX + top, y: rect.minY + top * 0.38)
        )
        path.addLine(to: CGPoint(x: rect.minX + top, y: rect.maxY - bottom))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX + top + bottom, y: rect.maxY),
            control: CGPoint(x: rect.minX + top, y: rect.maxY)
        )
        path.addLine(to: CGPoint(x: rect.maxX - top - bottom, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX - top, y: rect.maxY - bottom),
            control: CGPoint(x: rect.maxX - top, y: rect.maxY)
        )
        path.addLine(to: CGPoint(x: rect.maxX - top, y: rect.minY + top))
        path.addCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY),
            control1: CGPoint(x: rect.maxX - top, y: rect.minY + top * 0.38),
            control2: CGPoint(x: rect.maxX - top * 0.62, y: rect.minY)
        )
        path.closeSubpath()
        return path
    }
}

private enum IslandMode: Equatable {
    case compact, alert, expanded
}

private enum DashboardTab: String, CaseIterable {
    case now = "Now"
    case conversation = "Conversation"
}

enum IslandMotion {
    /// One curve drives the whole morph: shape radii, island frame, and clip.
    static let morph = Animation.spring(response: 0.42, dampingFraction: 0.84)
}

private enum Ink {
    static let primary = Color.white
    static let secondary = Color.white.opacity(0.6)
    static let tertiary = Color.white.opacity(0.38)
    static let hairline = Color.white.opacity(0.1)
}

private struct BlurFade: ViewModifier {
    let radius: CGFloat
    func body(content: Content) -> some View {
        content.blur(radius: radius)
    }
}

extension AnyTransition {
    static func blurFade(_ radius: CGFloat) -> AnyTransition {
        .modifier(active: BlurFade(radius: radius), identity: BlurFade(radius: 0))
            .combined(with: .opacity)
    }
}

// MARK: - Island

struct NotchView: View {
    @ObservedObject var model: NotchModel
    let controller: NotchPanelController

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var composerFocused: Bool
    @State private var mascotDrag = CGSize.zero
    @StateObject private var voice = VoiceInput()
    @State private var visualsSettled = false
    @State private var isMorphing = false
    @State private var morphToken = UUID()
    @State private var recordingStartedAt: Date?
    @State private var tab: DashboardTab = .now

    private var metrics: IslandMetrics { model.metrics }
    private var agent: MakiAgent { model.makiAgent }
    private var alert: IslandAlert? { model.islandAlert }

    private var mode: IslandMode {
        if model.expanded { return .expanded }
        if alert != nil { return .alert }
        return .compact
    }

    private var islandSize: CGSize {
        switch mode {
        case .compact: return metrics.compactSize
        case .alert: return metrics.alertSize
        case .expanded: return metrics.expandedSize
        }
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.clear
            island
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .environment(\.colorScheme, .dark)
        .onAppear {
            voice.onFinal = { text in
                model.draft = text
                model.send()
            }
            voice.onPartial = { text in
                model.draft = [model.draft, text].filter { !$0.isEmpty }.joined(separator: " ")
            }
        }
        .onChange(of: voice.isRecording) { _, recording in
            controller.debugLog("voice recording=\(recording) starting=\(voice.isStarting) finalizing=\(voice.isFinalizing)")
            model.listening = recording
            recordingStartedAt = recording ? Date() : nil
            model.mascot = recording
                ? .listening
                : (model.approvals.isEmpty ? .idle : .waiting)
        }
        .onChange(of: voice.isFinalizing) { _, finalizing in
            controller.debugLog("voice finalizing=\(finalizing) recording=\(voice.isRecording)")
        }
        .onChange(of: composerFocused) { _, focused in
            model.composerFocused = focused
            if focused { controller.cancelCollapse() }
        }
        .onChange(of: model.draft) { _, text in
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                controller.cancelCollapse()
            }
        }
        .onChange(of: model.expanded) { _, expanded in
            isMorphing = true
            visualsSettled = false
            if expanded {
                tab = .now
                visualsSettled = false
            } else {
                visualsSettled = false
                composerFocused = false
                model.composerFocused = false
            }
            let token = UUID()
            morphToken = token
            let settleDelay = reduceMotion ? 0.02 : 0.52
            DispatchQueue.main.asyncAfter(deadline: .now() + settleDelay) {
                guard morphToken == token else { return }
                isMorphing = false
                if expanded {
                    visualsSettled = true
                }
            }
        }
        .onChange(of: model.banner) { _, _ in
            // The alert drop-down is also a single morph; pause idle face
            // animations while its shape changes.
            isMorphing = true
            let token = UUID()
            morphToken = token
            DispatchQueue.main.asyncAfter(deadline: .now() + (reduceMotion ? 0.02 : 0.45)) {
                guard morphToken == token else { return }
                isMorphing = false
            }
        }
        .onExitCommand {
            controller.debugLog("exitCommand recording=\(voice.isRecording) starting=\(voice.isStarting) finalizing=\(voice.isFinalizing)")
            if voice.isRecording || voice.isStarting {
                voice.stop()
            } else {
                controller.collapse()
            }
        }
        .onDisappear {
            voice.cancel()
        }
    }

    private var island: some View {
        let size = islandSize
        let big = mode != .compact
        let shape = NotchShape(
            topRadius: big ? 14 : 6,
            bottomRadius: mode == .expanded ? 30 : mode == .alert ? 24 : metrics.notchHeight * 0.36
        )
        let palette = leadPalette
        let workColors = workColors
        let meshActive = agent.status.isBusy || agent.status == .waiting

        return ZStack(alignment: .top) {
            shape
                .fill(.black)

            if mode == .compact, let workColors, agent.status.isBusy, !isMorphing {
                WorkingAura(
                    colors: workColors,
                    notch: CGSize(width: metrics.notchWidth, height: metrics.notchHeight),
                    wing: max(0, (metrics.compactWidth - metrics.notchWidth) / 2),
                    shape: shape
                )
            }

            if mode != .compact, let palette, visualsSettled {
                DarkMeshBackdrop(
                    palette: palette,
                    active: meshActive,
                    paused: reduceMotion || !meshActive
                )
                .transition(.opacity.animation(.easeOut(duration: 0.18)))
            }

            shape
                .stroke(
                    LinearGradient(
                        stops: [
                            .init(color: .white.opacity(0), location: 0),
                            .init(color: .white.opacity(big ? 0.1 : 0), location: 0.35),
                            .init(color: .white.opacity(big ? 0.2 : 0), location: 1),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    ),
                    lineWidth: 0.75
                )

            if big, let palette {
                shape
                    .stroke(
                        LinearGradient(
                            colors: [.clear, palette[1], palette[3], palette[0], .clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        ),
                        lineWidth: 1
                    )
                    .mask(
                        LinearGradient(
                            stops: [
                                .init(color: .clear, location: 0.72),
                                .init(color: .black, location: 1),
                            ],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .opacity(0.7)
            }

            content
        }
        .frame(width: size.width, height: size.height, alignment: .top)
        .clipShape(shape)
        .contentShape(shape)

    }

    @ViewBuilder
    private var content: some View {
        switch mode {
        case .compact:
            compactContent
        case .alert:
            if let alert {
                alertContent(alert)
            }
        case .expanded:
            if visualsSettled {
                expandedContent
                    .transition(
                        .opacity.animation(
                            reduceMotion ? nil : .easeOut(duration: 0.16)
                        )
                    )
            }
        }
    }

    // MARK: - Theme

    private var leadPalette: [Color]? {
        if let alert {
            return alert.style == .question ? amberPalette : donePalette
        }
        if agent.status == .waiting { return amberPalette }
        return AgentKind.maki.gradient
    }

    private var workColors: [Color]? {
        guard agent.status.isBusy else { return nil }
        return [Color(hex: 0xFFFFFF), Color(hex: 0xD6DEE8), Color(hex: 0xFFE3A3), Color(hex: 0x8C98A6)]
    }

    private var donePalette: [Color] {
        [Color(hex: 0xA8E7C0), Color(hex: 0x5FCB86), Color(hex: 0x2E7D52), Color(hex: 0xE7FFF0)]
    }

    // MARK: - Compact

    private var compactContent: some View {
        let wing = max(0, (metrics.compactWidth - metrics.notchWidth) / 2)
        return HStack(spacing: 0) {
            HStack(spacing: 5) {
                mascot(size: 22, maxX: 42, maxY: 10)
                statusDot(size: 4.5)
            }
            .padding(.leading, 10)
            .frame(width: wing, alignment: .leading)

            Spacer(minLength: metrics.notchWidth)

            compactTrailing
                .padding(.trailing, 10)
                .frame(width: wing, alignment: .trailing)
        }
        .frame(height: metrics.notchHeight)
        .contentShape(Rectangle())
        .onTapGesture { openFromClick() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Maki, \(agent.status.label)")
        .accessibilityHint("Click to open")
        .help("Maki · \(agent.status.label)")
    }

    @ViewBuilder
    private var compactTrailing: some View {
        if let pending = agent.pending {
            HStack(spacing: 3) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 9.5, weight: .semibold))
                if model.approvals.count > 1 {
                    Text("\(model.approvals.count)")
                        .font(.system(size: 9, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                }
            }
            .foregroundStyle(amber)
            .accessibilityLabel("\(pending.tool) needs approval")
        } else if agent.status.isBusy {
            Group {
                if let start = agent.turnStart {
                    ElapsedText(since: start, pitch: 1.25, colors: [.white, .white.opacity(0.7)])
                } else if reduceMotion {
                    DotText(text: "···", pitch: 1.3, colors: [.white.opacity(0.75)])
                } else {
                    DotSpinner(color: .white)
                        .frame(width: 10, height: 10)
                }
            }
            .transition(.blurFade(4))
        } else {
            statusDot(size: 5)
        }
    }

    // MARK: - Alert

    private func alertContent(_ alert: IslandAlert) -> some View {
        let palette = alert.style == .question ? amberPalette : donePalette
        return VStack(spacing: 0) {
            Color.clear.frame(height: metrics.notchHeight)
            HStack(spacing: 12) {
                MakiGlyph(
                    mascot: alert.style == .done ? .happy : .waiting,
                    size: 30,
                    idlePaused: isMorphing
                )
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Image(systemName: alert.style == .done ? "checkmark.circle.fill" : "questionmark.bubble.fill")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(alert.style == .done ? makiGreen : amber)
                        Text(alert.title)
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundStyle(Ink.primary)
                    }
                    Text(alert.message)
                        .font(.system(size: 11.5, design: .rounded))
                        .foregroundStyle(Ink.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 8)
                Button("Open") { openFromClick() }
                    .buttonStyle(GradientPill(palette: palette))
                Button {
                    model.dismissBanner()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(QuietButton())
                .accessibilityLabel("Dismiss notification")
            }
            .padding(.horizontal, 22)
            .frame(height: IslandMetrics.alertHeight)
        }
        .frame(width: islandSize.width, height: islandSize.height, alignment: .top)
    }

    // MARK: - Expanded

    private var expandedContent: some View {
        VStack(spacing: 0) {
            header
            statusStrip
            tabPicker
            dashboardScroll
            composer
                .padding(.horizontal, 22)
                .padding(.top, 6)
            footer
                .padding(.horizontal, 22)
                .padding(.top, 7)
                .padding(.bottom, 12)
        }
        .frame(width: metrics.expandedWidth, height: metrics.expandedHeight, alignment: .top)
        .simultaneousGesture(TapGesture().onEnded { controller.activate() })
    }

    // MARK: Header and status

    private var sideWidth: CGFloat {
        max(60, (metrics.expandedWidth - metrics.notchWidth) / 2)
    }

    private var header: some View {
        HStack(spacing: 0) {
            HStack(spacing: 6) {
                // The one mascot in the expanded surface.
                MakiGlyph(mascot: agent.mascot, size: 20, idlePaused: isMorphing)
                Text("Maki")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(Ink.primary.opacity(0.85))
                statusDot(size: 4)
            }
            .padding(.leading, 16)
            .frame(width: sideWidth, alignment: .leading)

            Spacer(minLength: metrics.notchWidth)

            HStack(spacing: 0) {
                Button {
                    model.toggleMute()
                } label: {
                    Image(systemName: model.muted ? "speaker.slash.fill" : "speaker.wave.1.fill")
                }
                .buttonStyle(QuietButton())
                .help(model.muted ? "Unmute sounds" : "Mute sounds")
                .accessibilityLabel(model.muted ? "Unmute sounds" : "Mute sounds")

                Button {
                    model.pause()
                } label: {
                    Image(systemName: "pause.fill")
                }
                .buttonStyle(QuietButton())
                .help("Pause resident")
                .accessibilityLabel("Pause resident")

                Button {
                    controller.collapse()
                } label: {
                    Image(systemName: "chevron.up")
                }
                .buttonStyle(QuietButton())
                .help("Collapse island")
                .accessibilityLabel("Collapse island")

                Button {
                    controller.hide()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(QuietButton())
                .help("Hide Maki")
                .accessibilityLabel("Hide Maki")
            }
            .padding(.trailing, 12)
            .frame(width: sideWidth, alignment: .trailing)
        }
        .frame(height: metrics.notchHeight)
    }

    private var memory: Int {
        model.status?.coreMemoryCount ?? 0
    }

    private var statusHeadline: String {
        if model.connection != "live" { return "Sleeping" }
        if agent.status == .waiting { return "Waiting" }
        if agent.status.isBusy { return "Working" }
        if model.listening || voice.isRecording { return "Listening" }
        return "Ready"
    }

    private var statusSubtitle: String {
        if !model.approvals.isEmpty {
            return model.approvals.count == 1
                ? "One approval needs you"
                : "\(model.approvals.count) approvals need you"
        }
        if agent.status.isBusy {
            return "Turn in progress"
        }
        if model.listening || voice.isRecording {
            return "Hold the mic to talk, or press Stop"
        }
        if model.connection != "live" {
            return "Resident offline"
        }
        if let last = agent.lastTurn {
            return "Standing by · last turn \(NotchModel.durationLabel(last))"
        }
        return "Autonomous loop standing by"
    }

    private var statusStrip: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 7) {
                    Text(statusHeadline)
                        .font(.system(size: 16, weight: .semibold, design: .rounded))
                        .tracking(-0.2)
                        .foregroundStyle(Ink.primary)
                    if agent.status.isBusy, let start = agent.turnStart {
                        ElapsedText(
                            since: start,
                            pitch: 1.65,
                            colors: [amberPalette[3], amberPalette[0]]
                        )
                    }
                }
                HStack(spacing: 5) {
                    Text(statusSubtitle)
                        .font(.system(size: 10.5, design: .rounded))
                        .foregroundStyle(Ink.secondary)
                        .lineLimit(1)
                    if let next = model.nextHeartbeat {
                        Text("·")
                            .foregroundStyle(Ink.tertiary)
                        HeartbeatBadge(date: next)
                    } else if model.connection == "live" {
                        Text("·")
                            .foregroundStyle(Ink.tertiary)
                        Text(model.daemonLabel.lowercased())
                            .font(.system(size: 9.5, design: .rounded))
                            .foregroundStyle(Ink.tertiary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if model.connection == "live" {
                ZStack {
                    ContextRing(
                        fraction: min(1, Double(memory) / 12.0),
                        colors: agent.kind.gradient,
                        lineWidth: 3
                    )
                    VStack(spacing: 2) {
                        DotText(
                            text: "\(memory)",
                            pitch: 1.7,
                            colors: [.white, .white.opacity(0.7)]
                        )
                        Text("mem")
                            .font(.system(size: 8, weight: .medium, design: .rounded))
                            .foregroundStyle(Ink.tertiary)
                    }
                }
                .frame(width: 38, height: 38)
                .help("Core memory facts")
            }
        }
        .padding(.horizontal, 22)
        .padding(.top, 8)
        .padding(.bottom, 6)
    }

    // MARK: Tabs and dashboard

    private var tabPicker: some View {
        HStack(spacing: 3) {
            ForEach(DashboardTab.allCases, id: \.self) { item in
                Button {
                    withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) {
                        tab = item
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: item == .now ? "sparkles" : "bubble.left.and.bubble.right")
                            .font(.system(size: 9, weight: .semibold))
                        Text(item.rawValue)
                            .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                    }
                    .foregroundStyle(tab == item ? Ink.primary : Ink.tertiary)
                    .padding(.horizontal, 9)
                    .frame(height: 24)
                    .background(
                        Capsule().fill(tab == item ? .white.opacity(0.11) : .clear)
                    )
                }
                .buttonStyle(.plain)
                .accessibilityLabel(item.rawValue)
            }
            Spacer(minLength: 6)
            if !model.approvals.isEmpty && tab != .now {
                Button {
                    tab = .now
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "hand.raised.fill")
                            .font(.system(size: 8.5, weight: .semibold))
                        Text("\(model.approvals.count) waiting")
                            .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                    }
                    .foregroundStyle(amber)
                    .padding(.horizontal, 8)
                    .frame(height: 22)
                    .background(Capsule().fill(amber.opacity(0.12)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View approvals")
            }
        }
        .padding(.horizontal, 22)
        .padding(.bottom, 4)
    }

    private var dashboardScroll: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if tab == .now {
                        nowDashboard
                    } else {
                        conversationContent
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 22)
                .padding(.top, 4)
                .padding(.bottom, 4)
            }
            .scrollIndicators(.hidden)
            .onChange(of: model.messages.count) { _, _ in
                if tab == .conversation { scrollToBottom(proxy) }
            }
            .onChange(of: tab) { _, newTab in
                if newTab == .conversation { scrollToBottom(proxy) }
            }
        }
        .frame(maxHeight: .infinity)
    }

    private var nowDashboard: some View {
        VStack(alignment: .leading, spacing: 10) {
            quickActions
            goalCard
            approvalsSection
            screenContextCard
            activityCard
        }
    }

    private var quickActions: some View {
        HStack(spacing: 6) {
            QuickChip(icon: "eye", title: "Look at screen") {
                controller.lookAtScreen()
            }
            QuickChip(icon: "envelope", title: "Email") {
                model.createGoal(
                    title: "Triage email",
                    detail: "Review the inbox, draft replies, and wait for approval before sending."
                )
                tab = .now
            }
            QuickChip(icon: "magnifyingglass", title: "Research") {
                model.createGoal(
                    title: "Research the current screen",
                    detail: "Investigate what the user is working on and report concrete findings."
                )
                tab = .now
            }
            QuickChip(icon: "square.stack.3d.up", title: "Jobs") {
                model.createGoal(
                    title: "Review active jobs",
                    detail: "Check running batches and jobs, then report progress and blockers."
                )
                tab = .now
            }
            Spacer(minLength: 0)
        }
    }

    private var goalCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel("CURRENT GOAL", icon: "target")

            let task = model.currentGoal?.title
                ?? model.messages.last(where: { $0.fromUser })?.text
            if let task, !task.isEmpty {
                HStack(alignment: .top, spacing: 6) {
                    Text(task)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundStyle(Ink.primary.opacity(0.92))
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    if let goal = model.currentGoal {
                        Pill(text: goal.status.capitalized, tint: goalTint(goal.status))
                    }
                }
                if let detail = model.currentGoal?.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.system(size: 10.5, design: .rounded))
                        .foregroundStyle(Ink.secondary)
                        .lineLimit(2)
                }
            } else {
                Text("No active goal yet.")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(Ink.secondary)
                Text("Use a quick action above or queue a task below. Maki keeps working after the panel closes.")
                    .font(.system(size: 10.5, design: .rounded))
                    .foregroundStyle(Ink.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let batch = model.activeBatch {
                Divider().overlay(.white.opacity(0.07))
                HStack(spacing: 6) {
                    Image(systemName: "square.stack.3d.up.fill")
                        .font(.system(size: 8.5, weight: .semibold))
                        .foregroundStyle(amber)
                    Text(batch.goal)
                        .font(.system(size: 10.5, weight: .medium, design: .rounded))
                        .foregroundStyle(Ink.primary.opacity(0.8))
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Text("\(batch.done)/\(batch.total)")
                        .font(.system(size: 9.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(Ink.secondary)
                    Text(batch.status.uppercased())
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(amber)
                }
                progressBar(
                    value: batch.total > 0 ? Double(batch.done) / Double(batch.total) : 0
                )
            } else {
                Text("No active batch.")
                    .font(.system(size: 9.5, design: .rounded))
                    .foregroundStyle(Ink.tertiary)
            }
        }
        .padding(11)
        .background(cardSurface)
    }

    private var approvalsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(
                "APPROVALS",
                icon: "hand.raised",
                trailing: model.approvals.isEmpty ? nil : "\(model.approvals.count) waiting"
            )
            if model.approvals.isEmpty {
                emptyRow(icon: "checkmark.shield", text: "No approvals waiting")
            } else {
                ForEach(Array(model.approvals.prefix(1).enumerated()), id: \.element.id) { index, approval in
                    ApprovalCard(approval: approval, primary: index == 0, controller: controller)
                }
                if model.approvals.count > 1 {
                    Text("\(model.approvals.count - 1) more waiting · decide one at a time")
                        .font(.system(size: 9.5, design: .rounded))
                        .foregroundStyle(Ink.tertiary)
                }
            }
        }
        .padding(11)
        .background(cardSurface)
    }

    private var screenContextCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel("SCREEN CONTEXT", icon: "macwindow")
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "macwindow")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Ink.secondary)
                    .frame(width: 18)
                VStack(alignment: .leading, spacing: 2) {
                    Text(contextApp)
                        .font(.system(size: 12, weight: .medium, design: .rounded))
                        .foregroundStyle(Ink.primary.opacity(0.85))
                        .lineLimit(1)
                    if !contextWindow.isEmpty {
                        Text(contextWindow)
                            .font(.system(size: 10, design: .rounded))
                            .foregroundStyle(Ink.secondary)
                            .lineLimit(1)
                    }
                    HStack(spacing: 4) {
                        if let contextAt = model.contextAt {
                            Text("context \(contextAt, style: .relative)")
                        } else {
                            Text("Waiting for context")
                        }
                        if let lastLookAt = model.lastLookAt {
                            Text("· looked \(lastLookAt, style: .relative)")
                        }
                    }
                    .font(.system(size: 9.5, design: .rounded))
                    .foregroundStyle(Ink.tertiary)
                }
                Spacer(minLength: 6)
                Button {
                    controller.lookAtScreen()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "camera.viewfinder")
                            .font(.system(size: 9, weight: .semibold))
                        Text("Look")
                            .font(.system(size: 10, weight: .semibold, design: .rounded))
                    }
                }
                .buttonStyle(PillButton(tint: .white.opacity(0.1)))
                .accessibilityLabel("Look at screen")
                .help("Capture the screen and ask Maki what it sees")
            }
        }
        .padding(11)
        .background(cardSurface)
    }

    private var activityCard: some View {
        VStack(alignment: .leading, spacing: 7) {
            sectionLabel("RECENT ACTIVITY", icon: "clock")
            if model.activity.isEmpty {
                emptyRow(icon: "sparkles", text: "Nothing yet. Maki is standing by.")
            } else {
                ForEach(Array(model.activity.prefix(4))) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Image(systemName: activityIcon(item.kind))
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(activityTint(item.kind))
                            .frame(width: 12)
                        Text(item.text)
                            .font(.system(size: 10.5, design: .rounded))
                            .foregroundStyle(Ink.primary.opacity(0.8))
                            .lineLimit(1)
                        Spacer(minLength: 4)
                        Text(item.date, style: .relative)
                            .font(.system(size: 9, design: .rounded))
                            .foregroundStyle(Ink.tertiary)
                            .fixedSize()
                    }
                }
            }
        }
        .padding(11)
        .background(cardSurface)
    }

    private var conversationContent: some View {
        VStack(alignment: .leading, spacing: 9) {
            if !model.approvals.isEmpty {
                Button {
                    tab = .now
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "hand.raised.fill")
                            .font(.system(size: 9, weight: .semibold))
                        Text("\(model.approvals.count) approval\(model.approvals.count == 1 ? "" : "s") waiting · review on Now")
                            .font(.system(size: 10.5, weight: .medium, design: .rounded))
                        Spacer()
                    }
                    .foregroundStyle(amber)
                    .padding(.horizontal, 10)
                    .frame(height: 28)
                    .background(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .fill(amber.opacity(0.1))
                    )
                }
                .buttonStyle(.plain)
            }
            if model.messages.isEmpty {
                Text("No conversation yet.")
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(Ink.secondary)
                Text("Ask Maki below, or create a durable goal with the target button. Chat stays secondary to Maki's autonomous work.")
                    .font(.system(size: 10.5, design: .rounded))
                    .foregroundStyle(Ink.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ForEach(model.messages) { line in
                    message(line)
                }
            }
        }
        .padding(.top, 2)
    }

    private func message(_ line: NotchModel.ChatLine) -> some View {
        HStack {
            if line.fromUser { Spacer(minLength: 34) }
            Text(line.text)
                .font(.system(size: 12, weight: .regular, design: .rounded))
                .foregroundStyle(line.fromUser ? .black : Ink.primary.opacity(0.88))
                .padding(.horizontal, 11)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 13, style: .continuous)
                        .fill(
                            line.fromUser
                                ? AnyShapeStyle(Color.white.opacity(0.9))
                                : AnyShapeStyle(
                                    LinearGradient(
                                        colors: [.white.opacity(0.085), .white.opacity(0.045)],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 13, style: .continuous)
                                .stroke(.white.opacity(line.fromUser ? 0 : 0.055), lineWidth: 0.7)
                        )
                )
            if !line.fromUser { Spacer(minLength: 34) }
        }
    }

    // MARK: Composer and footer

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            if voice.isRecording || voice.isStarting || voice.isFinalizing {
                HStack(spacing: 6) {
                    Circle()
                        .fill(voice.isFinalizing ? amber : Color(red: 1, green: 0.32, blue: 0.32))
                        .frame(width: 6, height: 6)
                        .shadow(color: (voice.isFinalizing ? amber : Color.red).opacity(0.45), radius: 3)
                    Text(voice.isStarting ? "Opening mic…" : voice.isFinalizing ? "Finishing…" : "Listening…")
                        .foregroundStyle(Ink.primary.opacity(0.85))
                    if let start = recordingStartedAt, voice.isRecording {
                        TimelineView(.periodic(from: start, by: 1)) { timeline in
                            Text("· \(NotchModel.elapsedLabel(timeline.date.timeIntervalSince(start)))")
                                .monospacedDigit()
                                .foregroundStyle(Ink.secondary)
                        }
                    }
                    Spacer(minLength: 4)
                    Text(voice.isFinalizing ? "⌘esc cancel" : "esc stop · ⌘esc cancel")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(Ink.tertiary)
                        .fixedSize()
                }
                .font(.system(size: 11, design: .rounded))

                if !voice.transcript.isEmpty {
                    Text(voice.transcript)
                        .font(.system(size: 11, design: .rounded))
                        .foregroundStyle(Ink.secondary)
                        .lineLimit(2)
                }
            }
            if !voice.error.isEmpty {
                Text(voice.error)
                    .font(.system(size: 10, design: .rounded))
                    .foregroundStyle(Color.orange.opacity(0.9))
                    .lineLimit(2)
            }
            HStack(spacing: 8) {
                TextField("Ask Maki or queue a task…", text: $model.draft)
                    .textFieldStyle(.plain)
                    .font(.system(size: 12, design: .rounded))
                    .foregroundStyle(Ink.primary.opacity(0.9))
                    .focused($composerFocused)
                    .onSubmit { model.send() }
                    .accessibilityLabel("Message or task for Maki")

                if voice.isRecording || voice.isStarting {
                    Button {
                        voice.stop()
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "stop.fill")
                                .font(.system(size: 8.5, weight: .bold))
                            Text(voice.isStarting ? "Cancel start" : "Stop")
                                .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 9)
                        .frame(height: 26)
                        .background(Capsule().fill(Color(red: 0.82, green: 0.20, blue: 0.22)))
                        .overlay(Capsule().stroke(.white.opacity(0.2), lineWidth: 0.6))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Stop dictation")
                    .help("Stop and send transcript (Esc)")

                    Button {
                        voice.cancel()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(Ink.secondary)
                            .frame(width: 26, height: 26)
                            .background(Circle().fill(.white.opacity(0.07)))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Cancel dictation")
                    .help("Cancel dictation and discard the transcript (⌘Esc)")
                } else if voice.isFinalizing {
                    HStack(spacing: 5) {
                        Image(systemName: "waveform")
                            .font(.system(size: 9, weight: .semibold))
                        Text("Finishing…")
                            .font(.system(size: 10.5, weight: .semibold, design: .rounded))
                    }
                    .foregroundStyle(amber)
                    .padding(.horizontal, 9)
                    .frame(height: 26)
                    .background(Capsule().fill(amber.opacity(0.12)))

                    Button {
                        voice.cancel()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(Ink.secondary)
                            .frame(width: 26, height: 26)
                            .background(Circle().fill(.white.opacity(0.07)))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Cancel dictation")
                    .help("Cancel dictation and discard the transcript (⌘Esc)")
                } else {
                    Button {
                        voice.start()
                    } label: {
                        Image(systemName: "mic")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Ink.secondary)
                            .frame(width: 26, height: 26)
                            .background(Circle().fill(.white.opacity(0.07)))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Dictate")
                    .help("Start dictation")

                    Button {
                        model.createGoal(title: model.draft, detail: "")
                        model.draft = ""
                        tab = .now
                    } label: {
                        Image(systemName: "target")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Ink.secondary)
                            .frame(width: 26, height: 26)
                            .background(Circle().fill(.white.opacity(0.07)))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSend)
                    .accessibilityLabel("Create goal from draft")
                    .help("Create a durable goal from this text")
                }

                Button {
                    controller.lookAtScreen()
                } label: {
                    Image(systemName: "camera.viewfinder")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Ink.secondary)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(.white.opacity(0.07)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Look at screen")
                .help("Look at the screen and ask Maki what it sees")

                Button {
                    model.send()
                } label: {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.black)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(.white.opacity(canSend ? 0.92 : 0.25)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Send message")
                .disabled(!canSend)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.white.opacity(0.065))
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(.white.opacity(0.08), lineWidth: 0.8)
                    )
            )
            .background {
                if voice.isRecording || voice.isStarting || voice.isFinalizing {
                    // Registered only while the expanded composer has a live
                    // recording or pending finalization; the key window receives it.
                    Button("Cancel dictation") { voice.cancel() }
                        .keyboardShortcut(.escape, modifiers: .command)
                        .opacity(0)
                        .accessibilityHidden(true)
                }
            }
        }
    }

    private var canSend: Bool {
        !model.draft.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private var footer: some View {
        HStack(spacing: 6) {
            statusDot(size: 5)
            Text(model.connection == "live" ? "connected" : "offline · demo")
                .font(.system(size: 9.5, design: .rounded))
                .foregroundStyle(Ink.tertiary)
            Text("·")
                .foregroundStyle(Ink.tertiary)
            Text(model.daemonLabel.lowercased())
                .font(.system(size: 9.5, design: .rounded))
                .foregroundStyle(Ink.tertiary)
                .lineLimit(1)
            Text("·")
                .foregroundStyle(Ink.tertiary)
            Text("\(memory) facts")
                .font(.system(size: 9.5, design: .rounded))
                .foregroundStyle(Ink.tertiary)
            if model.connection != "live" {
                Button("Start") {
                    GatewayLauncher.shared.ensureRunning()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { model.refresh() }
                }
                .buttonStyle(.plain)
                .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                .foregroundStyle(Ink.secondary)
                .accessibilityLabel("Start the gateway")
            }
            Spacer()
            Text("esc close")
                .font(.system(size: 9.5, design: .rounded))
                .foregroundStyle(Ink.tertiary.opacity(0.75))
        }
        .frame(height: 16)
    }

    // MARK: Dashboard helpers

    private var contextApp: String {
        let app = model.context["app"] ?? ""
        return app.isEmpty ? "No frontmost app yet" : app
    }

    private var contextWindow: String {
        model.context["window"] ?? ""
    }

    private func goalTint(_ status: String) -> Color {
        switch status {
        case "active": return makiGreen
        case "paused": return amber
        case "done": return makiGreen
        default: return Ink.secondary
        }
    }

    private func progressBar(value: Double) -> some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.white.opacity(0.08))
                Capsule()
                    .fill(LinearGradient(colors: [amberPalette[3], amberPalette[0]], startPoint: .leading, endPoint: .trailing))
                    .frame(width: max(3, geometry.size.width * min(1, max(0, value))))
            }
        }
        .frame(height: 4)
    }

    private func sectionLabel(_ title: String, icon: String, trailing: String? = nil) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
                .font(.system(size: 8, weight: .semibold))
                .foregroundStyle(Ink.tertiary)
            Text(title)
                .font(.system(size: 8.5, weight: .semibold, design: .monospaced))
                .tracking(0.8)
                .foregroundStyle(Ink.tertiary)
            Spacer(minLength: 4)
            if let trailing {
                Text(trailing)
                    .font(.system(size: 9, weight: .medium, design: .rounded))
                    .foregroundStyle(amber.opacity(0.8))
            }
        }
    }

    private func emptyRow(icon: String, text: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: icon)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Ink.tertiary)
            Text(text)
                .font(.system(size: 10.5, design: .rounded))
                .foregroundStyle(Ink.tertiary)
            Spacer(minLength: 0)
        }
    }

    private var cardSurface: some View {
        RoundedRectangle(cornerRadius: 13, style: .continuous)
            .fill(
                LinearGradient(
                    colors: [.white.opacity(0.055), .white.opacity(0.022)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .stroke(.white.opacity(0.06), lineWidth: 0.7)
            )
    }

    private func activityIcon(_ kind: NotchModel.ActivityItem.Kind) -> String {
        switch kind {
        case .message: return "bubble.left"
        case .approval: return "hand.raised"
        case .goal: return "target"
        case .screen: return "eye"
        case .system: return "info.circle"
        }
    }

    private func activityTint(_ kind: NotchModel.ActivityItem.Kind) -> Color {
        switch kind {
        case .approval: return amber
        case .goal: return makiGreen
        case .screen: return Color(hex: 0x9BC7FF)
        case .message: return Ink.secondary
        case .system: return Ink.tertiary
        }
    }

    private func statusDot(size: CGFloat) -> some View {
        Circle()
            .fill(model.connection == "live" ? makiGreen : Color.orange)
            .frame(width: size, height: size)
            .shadow(color: (model.connection == "live" ? makiGreen : Color.orange).opacity(0.4), radius: 3)
            .accessibilityLabel(model.connection == "live" ? "Connected" : "Offline")
    }

    private func mascot(size: CGFloat, maxX: CGFloat, maxY: CGFloat) -> some View {
        MakiGlyph(mascot: agent.mascot, size: size, idlePaused: isMorphing)
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
        DispatchQueue.main.async {
            controller.expand(activate: true)
        }
    }
}

// MARK: - Maki glyph

private struct MakiGlyph: View {
    let mascot: MascotState
    var size: CGFloat = 20
    var idlePaused: Bool = false

    var body: some View {
        MascotView(state: mascot, size: size, calm: true, idlePaused: idlePaused)
            .overlay {
                if mascot == .waiting {
                    Ripple(size: size)
                }
            }
            .animation(.smooth(duration: 0.5), value: mascot)
    }
}

private struct Ripple: View {
    let size: CGFloat
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            if reduceMotion {
                Circle()
                    .stroke(amber.opacity(0.35), lineWidth: 1)
                    .frame(width: size * 1.25, height: size * 1.25)
            } else {
                TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
                    let time = timeline.date.timeIntervalSinceReferenceDate
                    ForEach(0..<2, id: \.self) { index in
                        let progress = CGFloat((time * 0.8 + Double(index) * 0.5).truncatingRemainder(dividingBy: 1))
                        Circle()
                            .stroke(amber.opacity(0.7 * (1 - progress)), lineWidth: 1)
                            .frame(width: size * (1 + 0.5 * progress), height: size * (1 + 0.5 * progress))
                    }
                }
            }
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Working aura

private struct FlowGradient: View {
    let colors: [Color]
    var speed = 0.35

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20)) { timeline in
            GeometryReader { geometry in
                let width = geometry.size.width
                let phase = CGFloat(
                    (timeline.date.timeIntervalSinceReferenceDate * speed)
                        .truncatingRemainder(dividingBy: 1)
                )
                LinearGradient(
                    colors: colors + colors + [colors[0]],
                    startPoint: .leading,
                    endPoint: .trailing
                )
                .frame(width: width * 2)
                .offset(x: -phase * width)
            }
        }
        .clipped()
    }
}

private struct WorkingAura<S: Shape>: View {
    let colors: [Color]
    let notch: CGSize
    let wing: CGFloat
    let shape: S
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20, paused: reduceMotion)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            let breathe = 0.82 + 0.18 * sin(time * 2.2)
            let side = wing + 4
            let wings = HStack(spacing: 0) {
                LinearGradient(
                    stops: [
                        .init(color: .black, location: 0),
                        .init(color: .black.opacity(0.6), location: 0.45),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
                .frame(width: side)
                Spacer(minLength: 0)
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: .black.opacity(0.6), location: 0.55),
                        .init(color: .black, location: 1),
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
                .frame(width: side)
            }
            ZStack {
                FlowGradient(colors: colors, speed: 0.12)
                    .blur(radius: 8)
                    .opacity(0.75 * breathe)
                let rim = AngularGradient(
                    colors: colors + [colors[0]],
                    center: .center,
                    angle: .degrees((time * 40).truncatingRemainder(dividingBy: 360))
                )
                shape.stroke(rim, lineWidth: 5).blur(radius: 4).opacity(0.85)
                shape.stroke(rim, lineWidth: 1).blur(radius: 0.5).opacity(0.8)
            }
            .drawingGroup()
            .mask(wings)
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: .black.opacity(0.35), location: 0.45),
                        .init(color: .black, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .clipShape(shape)
            .blendMode(.plusLighter)
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Mesh background

private struct MeshField: View {
    let palette: [Color]
    var speed: Double = 0.4
    var paused = false

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 15, paused: paused)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate * speed
            Canvas { context, size in
                let points: [(Double, Double, Int)] = [
                    (0.18, 0.2, 0),
                    (0.5, 0.1, 1),
                    (0.82, 0.25, 2),
                    (0.2, 0.54, 1),
                    (0.5, 0.5, 3),
                    (0.8, 0.62, 0),
                    (0.22, 0.86, 2),
                    (0.55, 0.9, 1),
                    (0.85, 0.84, 3),
                ]
                for (index, point) in points.enumerated() {
                    let wobbleX = sin(time + Double(index) * 1.3) * 0.055
                    let wobbleY = cos(time * 0.8 + Double(index) * 1.7) * 0.045
                    let center = CGPoint(
                        x: (point.0 + wobbleX) * size.width,
                        y: (point.1 + wobbleY) * size.height
                    )
                    let radius = max(size.width, size.height) * (0.3 + 0.06 * sin(time * 1.1 + Double(index)))
                    let color = palette[point.2 % palette.count]
                    let rect = CGRect(
                        x: center.x - radius,
                        y: center.y - radius,
                        width: radius * 2,
                        height: radius * 2
                    )
                    context.fill(
                        Path(ellipseIn: rect),
                        with: .radialGradient(
                            Gradient(colors: [color.opacity(0.55), color.opacity(0)]),
                            center: center,
                            startRadius: 0,
                            endRadius: radius
                        )
                    )
                }
            }
            .drawingGroup()
        }
    }
}

private struct DarkMeshBackdrop: View {
    let palette: [Color]
    var active = false
    var paused = false

    var body: some View {
        ZStack {
            Color.black
            MeshField(palette: palette, speed: active ? 0.8 : 0.3, paused: paused)
                .mask(DotGrid(pitch: 6, dot: 1.5))
                .mask(
                    LinearGradient(
                        stops: [
                            .init(color: .clear, location: 0.32),
                            .init(color: .black.opacity(0.55), location: 1),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .opacity(0.9)
        }
        .allowsHitTesting(false)
    }
}

// MARK: - Text and badges

private struct ElapsedText: View {
    let since: Date
    var pitch: CGFloat = 2
    var colors: [Color] = [.white]

    var body: some View {
        TimelineView(.periodic(from: since, by: 1)) { timeline in
            let seconds = max(0, Int(timeline.date.timeIntervalSince(since)))
            DotText(
                text: NotchModel.elapsedLabel(TimeInterval(seconds)),
                pitch: pitch,
                colors: colors,
                ghost: 0.06
            )
        }
    }
}

private struct ContextRing: View {
    let fraction: Double
    let colors: [Color]
    var lineWidth: CGFloat = 2.5

    var body: some View {
        ZStack {
            Circle().stroke(Color.white.opacity(0.07), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: max(0.001, fraction))
                .stroke(
                    AngularGradient(
                        colors: colors + [colors[0]],
                        center: .center,
                        startAngle: .degrees(0),
                        endAngle: .degrees(360 * max(fraction, 0.01))
                    ),
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
                .shadow(color: colors[1].opacity(0.5), radius: 3)
        }
        .animation(.smooth(duration: 0.8), value: fraction)
    }
}

private struct ShimmerText: View {
    let text: String
    let color: Color
    let active: Bool
    var size: CGFloat = 11

    var body: some View {
        let label = Text(text)
            .font(.system(size: size, weight: .medium, design: .rounded))
        label
            .foregroundStyle(color.opacity(active ? 0.72 : 0.9))
            .lineLimit(1)
            .truncationMode(.middle)
            .overlay {
                if active {
                    TimelineView(.animation(minimumInterval: 1 / 24)) { timeline in
                        let progress = CGFloat(
                            (timeline.date.timeIntervalSinceReferenceDate * 0.55)
                                .truncatingRemainder(dividingBy: 1)
                        )
                        GeometryReader { geometry in
                            LinearGradient(
                                colors: [.clear, .white.opacity(0.9), .clear],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                            .frame(width: max(40, geometry.size.width * 0.35))
                            .offset(x: -geometry.size.width * 0.35 + progress * geometry.size.width * 1.7)
                        }
                    }
                    .mask(label.lineLimit(1).truncationMode(.middle))
                    .allowsHitTesting(false)
                }
            }
    }
}

// MARK: - Controls

private struct QuietButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        QuietBody(configuration: configuration)
    }

    private struct QuietBody: View {
        let configuration: Configuration
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            configuration.label
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.white.opacity(enabled ? (configuration.isPressed ? 1 : 0.6) : 0.25))
                .frame(width: 26, height: 26)
                .background(Circle().fill(.white.opacity(configuration.isPressed ? 0.14 : 0.0001)))
                .scaleEffect(configuration.isPressed ? 0.88 : 1)
                .animation(.spring(response: 0.25, dampingFraction: 0.6), value: configuration.isPressed)
                .contentShape(Circle())
        }
    }
}

private struct PillButton: ButtonStyle {
    let tint: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11.5, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background(Capsule().fill(tint))
            .scaleEffect(configuration.isPressed ? 0.93 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.6), value: configuration.isPressed)
    }
}

private struct GradientPill: ButtonStyle {
    let palette: [Color]

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11.5, weight: .semibold, design: .rounded))
            .foregroundStyle(.black.opacity(0.85))
            .padding(.horizontal, 13)
            .frame(height: 26)
            .background(
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [palette[3], palette[0]],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .overlay(Capsule().strokeBorder(.white.opacity(0.35), lineWidth: 0.5))
            )
            .shadow(color: palette[1].opacity(0.5), radius: 8, y: 2)
            .scaleEffect(configuration.isPressed ? 0.93 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.6), value: configuration.isPressed)
    }
}

private struct ShortcutLabel: View {
    let title: String
    let key: String?

    var body: some View {
        HStack(spacing: 5) {
            Text(title)
            if let key {
                Text("⌘\(key)").font(.system(size: 9.5, weight: .semibold)).opacity(0.55)
            }
        }
    }
}

private struct QuickChip: View {
    let icon: String
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.system(size: 9, weight: .semibold))
                Text(title)
                    .font(.system(size: 9.5, weight: .semibold, design: .rounded))
            }
            .foregroundStyle(Ink.secondary)
            .padding(.horizontal, 8)
            .frame(height: 24)
            .background(Capsule().fill(.white.opacity(0.06)))
            .overlay(Capsule().stroke(.white.opacity(0.06), lineWidth: 0.6))
        }
        .buttonStyle(.plain)
        .help(title)
        .accessibilityLabel(title)
    }
}

private struct Pill: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.system(size: 8.5, weight: .semibold, design: .monospaced))
            .foregroundStyle(tint)
            .padding(.horizontal, 6)
            .frame(height: 18)
            .background(Capsule().fill(tint.opacity(0.12)))
            .overlay(Capsule().stroke(tint.opacity(0.25), lineWidth: 0.5))
    }
}

private struct HeartbeatBadge: View {
    let date: Date

    var body: some View {
        TimelineView(.periodic(from: .now, by: 5)) { timeline in
            let seconds = date.timeIntervalSince(timeline.date)
            Text(seconds > 0 ? "next heartbeat \(Self.short(seconds))" : "heartbeat due")
                .font(.system(size: 9.5, design: .rounded))
                .foregroundStyle(Ink.tertiary)
                .monospacedDigit()
        }
    }

    private static func short(_ interval: TimeInterval) -> String {
        let seconds = max(0, Int(interval.rounded()))
        if seconds >= 3600 { return "\(seconds / 3600)h" }
        if seconds >= 60 { return "\(seconds / 60)m" }
        return "\(seconds)s"
    }
}

// MARK: - Approval

private struct ApprovalCard: View {
    let approval: ResidentApproval
    let primary: Bool
    let controller: NotchPanelController

    private var pending: PendingPermission { PendingPermission(approval) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(amber)
                Text("Allow \(pending.tool)?")
                    .font(.system(size: 12.5, weight: .semibold, design: .rounded))
                    .foregroundStyle(Ink.primary)
                if !pending.summary.isEmpty {
                    Text(pending.summary)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Ink.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 6)
                if primary {
                    Text("⌘Y allow · ⌘N deny")
                        .font(.system(size: 8.5, weight: .medium, design: .monospaced))
                        .foregroundStyle(Ink.tertiary)
                        .fixedSize()
                }
            }

            if !pending.diff.isEmpty {
                ScrollView(.horizontal) {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(pending.diff) { line in
                            let marker = line.kind == .addition ? "+" : line.kind == .removal ? "−" : " "
                            let tint = line.kind == .addition ? Color.green : line.kind == .removal ? Color.red : Color.clear
                            HStack(spacing: 6) {
                                Text(marker).foregroundStyle(tint.opacity(0.9))
                                Text(line.text.isEmpty ? " " : line.text)
                                    .foregroundStyle(Ink.primary.opacity(0.85))
                                    .fixedSize(horizontal: true, vertical: false)
                            }
                            .font(.system(size: 10.5, design: .monospaced))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 1.5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(tint.opacity(0.1))
                        }
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            if pending.previewTruncated {
                Text("Preview truncated")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Ink.secondary)
            }

            HStack(spacing: 6) {
                Spacer(minLength: 0)
                if primary {
                    Button {
                        controller.resolveApproval(approval, verdict: "denied")
                    } label: {
                        ShortcutLabel(title: "Deny", key: "N")
                    }
                    .buttonStyle(PillButton(tint: .white.opacity(0.1)))
                    .keyboardShortcut("n", modifiers: .command)
                    .accessibilityLabel("Deny \(pending.tool)")
                } else {
                    Button {
                        controller.resolveApproval(approval, verdict: "denied")
                    } label: {
                        ShortcutLabel(title: "Deny", key: nil)
                    }
                    .buttonStyle(PillButton(tint: .white.opacity(0.1)))
                    .accessibilityLabel("Deny \(pending.tool)")
                }

                if primary {
                    Button {
                        controller.resolveApproval(approval, verdict: "approved")
                    } label: {
                        ShortcutLabel(title: "Allow", key: "Y")
                    }
                    .buttonStyle(GradientPill(palette: amberPalette))
                    .keyboardShortcut("y", modifiers: .command)
                    .accessibilityLabel("Allow \(pending.tool)")
                } else {
                    Button {
                        controller.resolveApproval(approval, verdict: "approved")
                    } label: {
                        ShortcutLabel(title: "Allow", key: nil)
                    }
                    .buttonStyle(GradientPill(palette: amberPalette))
                    .accessibilityLabel("Allow \(pending.tool)")
                }
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [amber.opacity(0.12), .white.opacity(0.03)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(amber.opacity(0.45), lineWidth: 0.75)
                )
        )
    }
}

// MARK: - Reveal removed: the single shape/frame spring is the only driver.
