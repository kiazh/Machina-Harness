// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Composer } from "../../src/features/harness/Composer.tsx";

afterEach(cleanup);

it("queues with Enter while busy and retains the draft when sending fails", async () => {
  const send = vi.fn().mockRejectedValue(new Error("Engine disconnected"));
  const interrupt = vi.fn();
  render(<Composer running onSend={send} onInterrupt={interrupt} disabled={false} />);
  const input = screen.getByRole("textbox", { name: "Message Machina" });
  fireEvent.change(input, { target: { value: "Check the new tests" } });
  await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
  expect(send).toHaveBeenCalledWith("Check the new tests");
  expect((input as HTMLTextAreaElement).value).toBe("Check the new tests");
  expect(screen.getByRole("alert").textContent).toContain("Engine disconnected");
  fireEvent.keyDown(input, { key: "Escape" });
  expect(interrupt).toHaveBeenCalledTimes(1);
});

it("does not submit while composing text or when Shift+Enter is pressed", () => {
  const send = vi.fn();
  render(<Composer running={false} onSend={send} onInterrupt={() => {}} disabled={false} />);
  const input = screen.getByRole("textbox", { name: "Message Machina" });
  fireEvent.change(input, { target: { value: "draft" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(send).not.toHaveBeenCalled();
});
