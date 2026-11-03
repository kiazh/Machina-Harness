import { memo, useEffect, useRef } from "react";
import type { Motion } from "../state/appearance.ts";

type Point = [number, number, number];
const count = 224;
const m: Point[] = [];
for (let y = 0; y < 18; y++) for (let x = 0; x < 19; x++) if (x < 3 || x > 15 || (y < 11 && Math.abs(y - Math.min(x, 18 - x)) < 2)) m.push([(x - 9) / 10, (y - 8.5) / 10, 0]);
const shapes: Point[][] = [
  Array.from({ length: count }, (_, i) => m[Math.floor(i * m.length / count)]!),
  Array.from({ length: count }, (_, i) => { const u = (i % 28) / 28 * Math.PI * 2; const v = Math.floor(i / 28) / 8 * Math.PI * 2; return [(.65 + .22 * Math.cos(v)) * Math.cos(u), (.65 + .22 * Math.cos(v)) * Math.sin(u), .22 * Math.sin(v)]; }),
  Array.from({ length: count }, (_, i) => { const a = (i % 7) / 6 * 1.4 - .7; const b = Math.floor(i / 7) % 6 / 5 * 1.4 - .7; const face = Math.floor(i / 42); return face === 0 ? [a, b, .7] : face === 1 ? [a, b, -.7] : face === 2 ? [.7, a, b] : face === 3 ? [-.7, a, b] : face === 4 ? [a, .7, b] : [a, -.7, b]; }),
];

/** Animation lives on one small canvas, outside React/chat rendering. */
export const Glyph = memo(function Glyph({ motion = "full", working = false, color = "#ff784a", size = 200 }: { motion?: Motion; working?: boolean; color?: string; size?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = canvas.current!; const context = element.getContext("2d"); if (!context) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    element.width = size * ratio; element.height = size * ratio;
    context.scale(ratio, ratio);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0; let previous = -1000; let visible = true;
    const observer = new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting ?? true; }); observer.observe(element);
    const paint = (time: number) => {
      const still = motion === "off" || reduced.matches;
      const interval = motion === "subtle" ? 1000 / 12 : 1000 / 24;
      if (visible && document.visibilityState !== "hidden" && time - previous >= interval) {
        previous = time;
        const t = still ? 0 : time / 1000 * (working ? 1 : .45);
        const cycle = t / 4.5; const phase = Math.floor(cycle) % 3; const fraction = cycle % 1;
        const blend = Math.max(0, (fraction - .6) / .4); const ease = blend * blend * (3 - 2 * blend);
        const angle = t * .28; const tilt = Math.sin(t * .23) * .35;
        context.clearRect(0, 0, size, size); context.fillStyle = color;
        context.font = `${Math.max(5, size / 34)}px "Cascadia Code", monospace`; context.textAlign = "center";
        for (let i = 0; i < count; i++) {
          const a = shapes[phase]![i]!; const b = shapes[(phase + 1) % 3]![i]!;
          const x = a[0] + (b[0] - a[0]) * ease; const y = a[1] + (b[1] - a[1]) * ease; const z = a[2] + (b[2] - a[2]) * ease;
          const rx = x * Math.cos(angle) + z * Math.sin(angle); const rz = z * Math.cos(angle) - x * Math.sin(angle);
          const ry = y * Math.cos(tilt) - rz * Math.sin(tilt); const depth = y * Math.sin(tilt) + rz * Math.cos(tilt);
          const scale = 2.6 / (2.6 + depth);
          context.globalAlpha = .4 + (1 - (depth + 1) / 2) * .6;
          context.fillText(".:+*#@"[Math.min(5, Math.max(0, Math.floor((1 - depth) * 2.8)))]!, size / 2 + rx * size * .37 * scale, size / 2 + ry * size * .37 * scale);
        }
        context.globalAlpha = 1;
      }
      if (!still) frame = requestAnimationFrame(paint);
    };
    frame = requestAnimationFrame(paint);
    const restart = () => { cancelAnimationFrame(frame); previous = -1000; frame = requestAnimationFrame(paint); };
    reduced.addEventListener("change", restart);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); reduced.removeEventListener("change", restart); };
  }, [motion, working, color, size]);
  return <canvas className="machina-glyph" ref={canvas} style={{ width: size, height: size }} aria-hidden="true"/>;
});
