import { expect, test, type Page, type Route } from "@playwright/test";
import type { LocalRequest, Workspace } from "../src/api";

const originalServer = "http://127.0.0.1:11434";
const currentServer = "http://127.0.0.1:11435";
const acknowledgement =
  "I stopped this original Ollama server completely and restarted it.";

async function recoveryFixture(
  page: Page,
  baseURL: string | null = originalServer,
) {
  await page.clock.install({ time: new Date("2026-10-04T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-10-04T12:00:00Z"));
  await page.goto("/");
  expect((await page.request.get("/api/session")).ok()).toBeTruthy();
  const state: Workspace = await (
    await page.request.get("/api/workspace")
  ).json();
  state.chats = [
    {
      id: "recovery-chat",
      title: "Recovery conversation",
      created_at: "2026-10-04",
      updated_at: "2026-10-04",
    },
  ];
  state.active_chat_id = "recovery-chat";
  state.messages = [];
  state.memories = [];
  state.tasks = [];
  state.provider.config = {
    ...state.provider.config,
    protocol: "ollama",
    base_url: currentServer,
    model: "current-local-model",
    pricing_verified: true,
    max_output_tokens: 1024,
  };
  Object.assign(state.provider, {
    credentials_required: false,
    models: ["current-local-model"],
  });
  const entry: LocalRequest = {
    id: "unknown-local",
    at: "2026-10-04T01:30:00Z",
    model: "original-local-model",
    base_url: baseURL,
  };
  state.usage.local_requests = [entry];
  const work = state.work!;
  Object.assign(work, {
    worker_available: true,
    running: true,
    waiting_for_ollama: true,
    queued: 0,
    candidates: [],
    journal: [],
    memories: [],
    tasks: [],
    last_stop_reason: "An interrupted local request holds the generation slot.",
  });
  work.config.enabled = false;
  let pending: Route | undefined;
  let holdNext = false;
  let polls = 0;
  let workspaceReads = 0;
  const recoveries: { body: unknown; csrf: string | undefined }[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/session")
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    else if (path === "/api/workspace") {
      workspaceReads += 1;
      await route.fulfill({ json: state });
    } else if (path === "/api/reflection") {
      polls += 1;
      await route.fulfill({ json: { ...work, usage: state.usage } });
    } else if (path === "/api/provider/local-requests/unknown-local/recover") {
      expect(route.request().method()).toBe("POST");
      recoveries.push({
        body: route.request().postDataJSON(),
        csrf: route.request().headers()["x-maestro-csrf"],
      });
      if (holdNext) {
        holdNext = false;
        pending = route;
      } else {
        state.usage.local_requests = [];
        work.running = false;
        work.waiting_for_ollama = false;
        await route.fulfill({ json: state });
      }
    } else if (path === "/api/chat") {
      await route.fulfill({
        status: 409,
        json: {
          detail:
            "The interrupted local request still holds the generation slot.",
        },
      });
    } else if (path === "/api/provider" && route.request().method() === "PUT") {
      state.provider.config = route.request().postDataJSON().config;
      await route.fulfill({ json: state.provider });
    } else throw new Error(`Unexpected local recovery request: ${path}`);
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Recovery conversation" }),
  ).toBeVisible();
  return {
    state,
    work,
    recoveries,
    polls: () => polls,
    workspaceReads: () => workspaceReads,
    holdNext: () => {
      holdNext = true;
      pending = undefined;
    },
    pending: () => pending,
  };
}

async function openRecovery(page: Page) {
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  return page.getByRole("dialog", { name: "Usage & limits" });
}

test("local recovery requires acknowledgement, retains a failed block and preserves drafts", async ({
  page,
}) => {
  const fixture = await recoveryFixture(page);
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  await composer.fill("Keep this unsent conversation draft.");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Open local recovery" }),
  ).toBeVisible();
  await expect(composer).toHaveValue("Keep this unsent conversation draft.");
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await expect(page.getByText("Recovery required", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const tokenDraft = page.getByLabel("Maximum output tokens per reply", {
    exact: true,
  });
  await tokenDraft.fill("1536");
  await page.getByRole("button", { name: "Open local recovery" }).click();
  const usage = page.getByRole("dialog", { name: "Usage & limits" });
  const recovery = usage.locator(".local-recovery-form");
  await expect(
    recovery.getByText(originalServer, { exact: true }),
  ).toBeVisible();
  await expect(recovery).toContainText("original-local-model");
  await expect(recovery.locator("time")).toHaveAttribute(
    "datetime",
    "2026-10-04T01:30:00Z",
  );
  await expect(recovery).not.toContainText(currentServer);
  await expect(
    recovery.getByLabel("Original Ollama server URL", { exact: true }),
  ).toHaveCount(0);
  const confirm = recovery.getByRole("checkbox", { name: acknowledgement });
  const release = recovery.getByRole("button", {
    name: "Verify and release slot",
  });
  await expect(confirm).not.toBeChecked();
  await expect(release).toBeDisabled();
  // The submit guard also rejects programmatic submission without confirmation.
  await recovery.evaluate((form) =>
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    ),
  );
  expect(fixture.recoveries).toHaveLength(0);
  fixture.holdNext();
  await confirm.check();
  await release.click();
  await expect.poll(() => fixture.recoveries.length).toBe(1);
  expect(fixture.recoveries[0]).toEqual({
    body: { restart_confirmed: true, expected_base_url: originalServer },
    csrf: "synthetic-csrf",
  });
  await expect(confirm).toBeDisabled();
  await expect(release).toBeDisabled();
  await fixture.pending()!.fulfill({
    status: 409,
    json: {
      detail:
        "The original server still has a loaded model. The slot remains reserved.",
    },
  });
  await expect(usage.getByRole("alert")).toContainText(
    "The slot remains reserved.",
  );
  await expect(recovery).toBeVisible();
  await expect(confirm).toBeChecked();
  await expect(release).toBeEnabled();
  expect(fixture.state.usage.local_requests).toHaveLength(1);
  await release.click();
  await expect(recovery).toHaveCount(0);
  expect(fixture.recoveries).toHaveLength(2);
  await usage.getByRole("button", { name: "Close dialog" }).click();
  await expect(tokenDraft).toHaveValue("1536");
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await expect(composer).toHaveValue("Keep this unsent conversation draft.");
});

test("legacy local recovery starts blank and every original URL edit resets acknowledgement", async ({
  page,
}) => {
  const fixture = await recoveryFixture(page, null);
  const usage = await openRecovery(page);
  const recovery = usage.locator(".local-recovery-form");
  const original = recovery.getByLabel("Original Ollama server URL", {
    exact: true,
  });
  const confirm = recovery.getByRole("checkbox", { name: acknowledgement });
  const release = recovery.getByRole("button", {
    name: "Verify and release slot",
  });
  await expect(original).toHaveValue("");
  await expect(confirm).toBeDisabled();
  await expect(release).toBeDisabled();
  expect(fixture.recoveries).toHaveLength(0);
  await original.fill(originalServer);
  await confirm.check();
  await expect(release).toBeEnabled();
  const restoredServer = "http://127.0.0.1:11436";
  await original.fill(restoredServer);
  await expect(confirm).not.toBeChecked();
  await expect(release).toBeDisabled();
  await confirm.check();
  fixture.holdNext();
  await release.click();
  await expect.poll(() => fixture.recoveries.length).toBe(1);
  expect(fixture.recoveries[0].body).toEqual({
    restart_confirmed: true,
    expected_base_url: restoredServer,
  });
  await expect(original).toBeDisabled();
  await fixture.pending()!.fulfill({
    status: 409,
    json: {
      detail: "Original server is unavailable; the slot remains reserved.",
    },
  });
  await expect(original).toBeEnabled();
  await expect(original).toHaveValue(restoredServer);
  await expect(confirm).toBeChecked();
  await expect(release).toBeEnabled();
  expect(fixture.state.usage.local_requests![0].base_url).toBeNull();
  await original.fill("");
  await expect(confirm).not.toBeChecked();
  await expect(confirm).toBeDisabled();
  await expect(release).toBeDisabled();
});

test("polling resets recovery acknowledgement only when request identity or endpoint changes", async ({
  page,
}) => {
  const fixture = await recoveryFixture(page);
  fixture.work.config.enabled = true;
  const usage = await openRecovery(page);
  const confirm = usage.getByRole("checkbox", { name: acknowledgement });
  await confirm.check();
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(1);
  await expect(confirm).toBeChecked();
  fixture.state.usage.local_requests![0].base_url = "http://127.0.0.1:11436";
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(2);
  await expect(confirm).not.toBeChecked();
  await expect(
    usage.getByText("http://127.0.0.1:11436", { exact: true }),
  ).toBeVisible();
  await confirm.check();
  fixture.state.usage.local_requests![0].id = "replacement-local";
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(3);
  await expect(confirm).not.toBeChecked();
  await confirm.check();
  const reappearing = structuredClone(fixture.state.usage.local_requests![0]);
  fixture.state.usage.local_requests = [];
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(4);
  await expect(usage.locator(".local-recovery-form")).toHaveCount(0);
  fixture.state.usage.local_requests = [reappearing];
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(5);
  await expect(confirm).not.toBeChecked();
  expect(fixture.recoveries).toHaveLength(0);
});

test("late recovery results refresh the workspace without replacing newer provider settings", async ({
  page,
}) => {
  const fixture = await recoveryFixture(page);
  await page
    .getByRole("textbox", { name: "Message Maestro" })
    .fill("Preserve the composer while recovery finishes.");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const usage = await openRecovery(page);
  fixture.holdNext();
  const stale = structuredClone(fixture.state);
  stale.usage.local_requests = [];
  stale.work!.running = false;
  stale.work!.waiting_for_ollama = false;
  await usage.getByRole("checkbox", { name: acknowledgement }).check();
  await usage.getByRole("button", { name: "Verify and release slot" }).click();
  await expect.poll(() => fixture.recoveries.length).toBe(1);
  await usage.getByRole("button", { name: "Close dialog" }).click();
  const providerURL = page.getByLabel("API base URL", { exact: true });
  const newerServer = "http://127.0.0.1:11437";
  await providerURL.fill(newerServer);
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await expect
    .poll(() => fixture.state.provider.config.base_url)
    .toBe(newerServer);
  await expect.poll(fixture.workspaceReads).toBe(2);
  const tokenDraft = page.getByLabel("Maximum output tokens per reply", {
    exact: true,
  });
  await tokenDraft.fill("1792");
  fixture.state.usage.local_requests = [];
  fixture.work.running = false;
  fixture.work.waiting_for_ollama = false;
  await fixture.pending()!.fulfill({ json: stale });
  await expect.poll(fixture.workspaceReads).toBe(3);
  await expect(providerURL).toHaveValue(newerServer);
  await expect(tokenDraft).toHaveValue("1792");
  expect(fixture.recoveries[0].body).toEqual({
    restart_confirmed: true,
    expected_base_url: originalServer,
  });
  const refreshedUsage = await openRecovery(page);
  await expect(refreshedUsage.locator(".local-recovery-form")).toHaveCount(0);
  await refreshedUsage.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Message Maestro" }),
  ).toHaveValue("Preserve the composer while recovery finishes.");
});
