import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/test/fixtures/workspace.html");
  await expect(page.getByRole("region", { name: "Agent harness" })).toBeVisible();
});

test("agent is primary and optional panes reclaim space at desktop and compact widths", async ({ page }) => {
  await expect(page.getByRole("region", { name: "Code workspace" })).toHaveCount(0);
  for (const width of [1440, 1024, 720]) {
    await page.setViewportSize({ width, height: 800 });
    const agent = page.getByRole("region", { name: "Agent harness" });
    const before = await agent.boundingBox();
    await page.getByRole("button", { name: "Toggle code editor" }).click();
    await expect(page.getByRole("textbox", { name: "Code editor", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Hide code editor" }).click();
    await expect(agent).toBeVisible();
    expect((await agent.boundingBox())!.width).toBeGreaterThanOrEqual(before!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  }
});

test("busy Enter queues, process controls act on the same job, and settings close with Escape", async ({ page }) => {
  const composer = page.getByRole("textbox", { name: "Message Machina" });
  await composer.fill("Keep the save conflict checks");
  await composer.press("Enter");
  await expect(page.getByRole("region", { name: "Queued messages" })).toContainText("Keep the save conflict checks");
  await expect(composer).toHaveValue("");
  await page.getByRole("button", { name: "Background", exact: true }).click();
  await expect(page.getByRole("button", { name: "Background", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Output", exact: true }).click();
  await expect(page.getByRole("region", { name: "Command output" })).toContainText("Synthetic command output");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.locator(".job-strip")).toContainText("stopped");
  await page.getByRole("button", { name: "Open settings" }).click();
  await expect(page.locator(".appearance-dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".appearance-dialog")).not.toBeVisible();
});

test("long transcripts keep a bounded DOM while code typing and token streaming continue", async ({ page }) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.getByRole("button", { name: "Toggle code editor" }).click();
  await page.evaluate(() => {
    const { fixtureController: controller } = window as any;
    for (let index = 0; index < 1500; index++) controller.transcript.put({ id: `long:${index}`, role: index % 2 ? "assistant" : "user", text: `${index}: ${"A synthetic long conversation checks scrolling and editor responsiveness. ".repeat(30)}` });
    controller.transcript.flush();
  });
  await expect.poll(() => page.locator(".message").count()).toBeLessThan(40);
  const editor = page.getByRole("textbox", { name: "Code editor", exact: true });
  await editor.click();
  await editor.press("Control+Home");
  await page.evaluate(() => {
    const target = window as any;
    let remaining = 250;
    const timer = setInterval(() => {
      target.fixtureEmit({ type: "message.delta", sessionId: "fixture-session", turnId: "fixture-turn", text: "stream " });
      if (--remaining === 0) clearInterval(timer);
    }, 2);
  });
  const started = Date.now();
  await editor.pressSequentially("# Typed while a long conversation is streaming\n", { delay: 4 });
  expect(Date.now() - started).toBeLessThan(4000);
  await expect(editor).toContainText("Typed while a long conversation is streaming");
  await page.locator(".transcript").hover();
  await page.mouse.wheel(0, -1200);
  await expect.poll(() => page.locator(".message").count()).toBeLessThan(40);
  const intervals = await page.evaluate(async () => {
    const times: number[] = []; let previous = performance.now();
    for (let frame = 0; frame < 40; frame++) await new Promise<void>(resolve => requestAnimationFrame(now => { times.push(now - previous); previous = now; resolve(); }));
    return times.sort((a, b) => a - b);
  });
  console.log("Synthetic long-chat rAF p95:", intervals[Math.floor(intervals.length * .95)], "ms; rendered rows:", await page.locator(".message").count());
  expect(intervals[Math.floor(intervals.length * .95)]).toBeLessThan(150);
  expect(errors).toEqual([]);
});

test("real IndexedDB recovery restores unsaved code and the composer after reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Toggle code editor" }).click();
  const editor = page.getByRole("textbox", { name: "Code editor", exact: true });
  await editor.fill("# unsaved recovery check\nvalue = 42\n");
  await page.getByRole("textbox", { name: "Message Machina" }).fill("Keep this unsent draft");
  await expect.poll(async () => page.evaluate(() => new Promise<number>((resolve, reject) => {
    const request = indexedDB.open("machina-desktop-recovery", 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const read = request.result.transaction("buffers", "readonly").objectStore("buffers").count();
      read.onsuccess = () => { request.result.close(); resolve(read.result); };
    };
  }))).toBe(2);
  page.on("dialog", dialog => { void dialog.accept(); });
  await page.reload();
  await expect(editor).toContainText("unsaved recovery check");
  await expect(page.getByRole("textbox", { name: "Message Machina" })).toHaveValue("Keep this unsent draft");
  await expect(page.getByRole("alert")).toContainText("Recovered 1 unsaved buffer");
});

test("styles, motion and custom wallpaper persist while editor state and draft survive", async ({ page }) => {
  await page.getByRole("button", { name: "Toggle code editor" }).click();
  const editor = page.getByRole("textbox", { name: "Code editor", exact: true });
  await editor.click(); await editor.press("Control+Home"); await editor.pressSequentially("# preserved across styles\n");
  const composer = page.getByRole("textbox", { name: "Message Machina" });
  await composer.fill("Unsent draft while personalizing");
  const retainedEditor = await page.locator(".cm-editor").elementHandle();
  await page.getByRole("button", { name: "Hide code editor" }).click();
  for (const preset of ["Volt", "Chrome", "Afterimage"]) {
    await page.getByRole('button',{name:'Open settings'}).click();
    await page.locator(`.preset-choice.preset-${preset.toLowerCase()}`).click();
    await page.keyboard.press('Escape');
    await expect(page.locator(".desktop-app")).toHaveAttribute("data-preset", preset.toLowerCase());
  }
  await page.getByRole("button", { name: "Toggle code editor" }).click();
  expect(await retainedEditor!.evaluate(element => element.isConnected)).toBe(true);
  await expect(editor).toContainText("preserved across styles");
  await expect(composer).toHaveValue("Unsent draft while personalizing");
  await page.getByRole("button", { name: "Open settings", exact: true }).click();
  await page.getByRole("combobox", { name: "Motion", exact: true }).click();
  await page.getByRole("option", { name: "Off", exact: true }).click();
  await page.getByRole('button',{name:'Wallpaper & surfaces',exact:true}).click();
  const artwork = await page.request.get("/art/afterimage.png");
  await page.getByLabel("Upload wallpaper").setInputFiles({ name: "wallpaper.png", mimeType: "image/png", buffer: await artwork.body() });
  await expect(page.getByRole("combobox", { name: "Background source" })).toHaveText("Your wallpaper");
  await expect(page.locator(".desktop-app")).toHaveAttribute("data-motion", "off");
  await page.keyboard.press("Escape");
  await expect.poll(() => page.locator(".wallpaper-layer").evaluate(element => getComputedStyle(element).backgroundImage)).toContain("blob:");
  expect(await page.locator('.working-dot').first().evaluate(element=>getComputedStyle(element).animationName)).toBe('none');
  await page.reload();
  await expect(page.locator(".desktop-app")).toHaveAttribute("data-motion", "off");
  await expect.poll(() => page.locator(".wallpaper-layer").evaluate(element => getComputedStyle(element).backgroundImage)).toContain("blob:");
});

test("the visible agent stop sends cancellation and waits for the engine's terminal event", async ({ page }) => {
  await page.evaluate(() => {
    const target = window as any;
    target.stopRequests = [];
    const bridge = target.fixtureController.bridge;
    const request = bridge.request.bind(bridge);
    bridge.request = (method: string, params: unknown) => { target.stopRequests.push(method); return request(method, params); };
  });
  await page.getByRole("button", { name: "Stop agent", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).stopRequests.includes("prompt.cancel"))).toBe(true);
  await expect(page.getByRole("button", { name: "Stop agent", exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).fixtureEmit({ type: "message.complete", sessionId: "fixture-session", turnId: "fixture-turn", text: "", cancelled: true }));
  await expect(page.getByRole("button", { name: "Stop agent", exact: true })).toHaveCount(0);
  await expect(page.getByText("Interrupted. Queued prompts remain pending.", { exact: true })).toBeVisible();
});
