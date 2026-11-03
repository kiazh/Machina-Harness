// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MessageBody } from "../../src/features/harness/MessageBody.tsx";
afterEach(() => { cleanup(); vi.useRealTimers(); });
it("renders readable headings, lists and code instead of raw Markdown markers", () => {
  render(<MessageBody text={'## Findings\n\nTwo things to fix.\n\n- First\n- Second\n\n```python\nvalue = 42\n```'} streaming={false}/>);
  expect(screen.getByRole("heading", { name: "Findings" })).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByText("value = 42").closest("pre")).toBeTruthy();
});
it("keeps HTML inert and rejects script URLs from generated content", () => {
  const result = render(<MessageBody text={'<script>alert(1)</script>\n\n[bad](javascript:alert(1))'} streaming={false}/>);
  expect(result.container.querySelector("script")).toBeNull();
  expect(result.container.querySelector('a[href^="javascript:"]')).toBeNull();
});
it("keeps external navigation outside the workspace and does not fetch embedded images", () => {
  const result = render(<MessageBody text={'[Reference](https://example.com)\n\n![Diagram](https://example.com/tracker.png)'} streaming={false}/>);
  expect(screen.getByRole("link", { name: "Reference" }).getAttribute("target")).toBe("_blank");
  expect(result.container.querySelector("img")).toBeNull();
});
it("batches streaming formatting and immediately reveals the complete final reply", () => {
  vi.useFakeTimers();
  const result = render(<MessageBody text="First" streaming/>);
  result.rerender(<MessageBody text="Second" streaming/>);
  expect(screen.queryByText("Second")).toBeNull();
  act(() => { vi.advanceTimersByTime(125); });
  expect(screen.getByText("Second")).toBeTruthy();
  result.rerender(<MessageBody text="Final reply" streaming={false}/>);
  expect(screen.getByText("Final reply")).toBeTruthy();
});
