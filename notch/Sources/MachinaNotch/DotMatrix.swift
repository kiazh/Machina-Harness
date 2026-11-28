import SwiftUI

/// 5×7 dot-matrix font. Adapted from NotchAgents (MIT); see THIRD_PARTY_NOTICES.md.
enum DotFont {
    struct Glyph {
        let rows: [[Bool]]
        var width: Int { rows[0].count }
    }

    static func glyph(_ character: Character) -> Glyph {
        cache[character] ?? cache[" "]!
    }

    private static let cache: [Character: Glyph] = source.mapValues { rows in
        Glyph(rows: rows.map { $0.map { $0 == "#" } })
    }

    private static let source: [Character: [String]] = [
        "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
        "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
        "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
        "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
        "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
        "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
        "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
        "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
        "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
        "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
        "%": ["##...", "##..#", "...#.", "..#..", ".#...", "#..##", "...##"],
        "$": ["..#..", ".####", "#.#..", ".###.", "..#.#", "####.", "..#.."],
        "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
        "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
        "h": ["#....", "#....", "#.##.", "##..#", "#...#", "#...#", "#...#"],
        "m": [".....", ".....", "##.#.", "#.#.#", "#.#.#", "#...#", "#...#"],
        "s": [".....", ".....", ".####", "#....", ".###.", "....#", "####."],
        "d": ["....#", "....#", ".##.#", "#..##", "#...#", "#...#", ".####"],
        ":": [".", ".", "#", ".", "#", ".", "."],
        ".": [".", ".", ".", ".", ".", ".", "#"],
        "-": ["...", "...", "...", "###", "...", "...", "..."],
        "—": ["...", "...", "...", "###", "...", "...", "..."],
        " ": ["..", "..", "..", "..", "..", "..", ".."],
    ]
}

/// Text drawn as a dot matrix with a vertical light gradient.
struct DotText: View {
    let text: String
    var pitch: CGFloat = 2
    var colors: [Color] = [.white]
    var ghost: Double = 0

    var body: some View {
        let glyphs = text.map(DotFont.glyph)
        let columns = glyphs.reduce(0) { $0 + $1.width } + max(0, glyphs.count - 1)
        Canvas { context, size in
            var lit = Path()
            var off = Path()
            let dot = pitch * 0.76
            let inset = (pitch - dot) / 2
            var x = 0
            for glyph in glyphs {
                for row in 0..<7 {
                    for column in 0..<glyph.width {
                        let rect = CGRect(
                            x: CGFloat(x + column) * pitch + inset,
                            y: CGFloat(row) * pitch + inset,
                            width: dot,
                            height: dot
                        )
                        if glyph.rows[row][column] {
                            lit.addEllipse(in: rect)
                        } else if ghost > 0 {
                            off.addEllipse(in: rect)
                        }
                    }
                }
                x += glyph.width + 1
            }
            if ghost > 0 {
                context.fill(off, with: .color(.white.opacity(ghost)))
            }
            context.fill(
                lit,
                with: .linearGradient(
                    Gradient(colors: colors.count > 1 ? colors : colors + colors),
                    startPoint: .zero,
                    endPoint: CGPoint(x: 0, y: size.height)
                )
            )
        }
        .frame(width: CGFloat(columns) * pitch, height: 7 * pitch)
        .animation(nil, value: text)
    }
}

/// Uniform LED dot field used as a mask for the mesh background.
struct DotGrid: View {
    var pitch: CGFloat = 7
    var dot: CGFloat = 1.6

    var body: some View {
        Canvas { context, size in
            var path = Path()
            var y = pitch / 2
            while y < size.height {
                var x = pitch / 2
                while x < size.width {
                    path.addEllipse(in: CGRect(x: x - dot / 2, y: y - dot / 2, width: dot, height: dot))
                    x += pitch
                }
                y += pitch
            }
            context.fill(path, with: .color(.white))
        }
    }
}

/// Dot-matrix equalizer for a working Maki.
struct DotEqualizer: View {
    var colors: [Color]
    var columns = 4
    var rows = 4
    var pitch: CGFloat = 2.6

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
            let time = timeline.date.timeIntervalSinceReferenceDate
            Canvas { context, size in
                var lit = Path()
                var off = Path()
                let dot = pitch * 0.72
                let inset = (pitch - dot) / 2
                for column in 0..<columns {
                    let k = Double(column)
                    let level = (0.55 + 0.3 * sin(time * 5.1 + k * 1.7) + 0.15 * sin(time * 8.3 + k * 2.9)) * Double(rows)
                    for row in 0..<rows {
                        let rect = CGRect(
                            x: CGFloat(column) * pitch + inset,
                            y: CGFloat(rows - 1 - row) * pitch + inset,
                            width: dot,
                            height: dot
                        )
                        if Double(row) < level.rounded() {
                            lit.addEllipse(in: rect)
                        } else {
                            off.addEllipse(in: rect)
                        }
                    }
                }
                context.fill(off, with: .color(.white.opacity(0.1)))
                context.fill(
                    lit,
                    with: .linearGradient(
                        Gradient(colors: colors),
                        startPoint: .zero,
                        endPoint: CGPoint(x: 0, y: size.height)
                    )
                )
            }
        }
        .frame(width: CGFloat(columns) * pitch, height: CGFloat(rows) * pitch)
    }
}

/// Ring of eight dots with a fading comet tail.
struct DotSpinner: View {
    let color: Color

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
            let head = Int(timeline.date.timeIntervalSinceReferenceDate * 10) % 8
            Canvas { context, size in
                let radius = min(size.width, size.height) / 2
                let dot = radius * 0.42
                for index in 0..<8 {
                    let angle = Double(index) / 8 * 2 * .pi - .pi / 2
                    let age = (head - index + 8) % 8
                    let point = CGPoint(
                        x: size.width / 2 + cos(angle) * (radius - dot / 2),
                        y: size.height / 2 + sin(angle) * (radius - dot / 2)
                    )
                    context.fill(
                        Path(ellipseIn: CGRect(x: point.x - dot / 2, y: point.y - dot / 2, width: dot, height: dot)),
                        with: .color(age == 0 ? .white : color.opacity(max(0.12, 1 - Double(age) * 0.22)))
                    )
                }
            }
        }
    }
}
