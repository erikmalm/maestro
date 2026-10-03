import { expect, test, type Page, type Route } from "@playwright/test";
import type { Memory, Workspace } from "../src/api";

async function reflectionFixture(page: Page) {
  await page.goto("/");
  expect((await page.request.get("/api/session")).ok()).toBeTruthy();
  const state: Workspace = await (
    await page.request.get("/api/workspace")
  ).json();
  state.chats = [
    {
      id: "source-chat",
      title: "Source conversation",
      created_at: "2026-10-03",
      updated_at: "2026-10-03",
    },
  ];
  state.active_chat_id = "source-chat";
  state.messages = [];
  state.memories = [];
  state.provider.config.protocol = "ollama";
  const work = state.work!;
  Object.assign(work, {
    worker_available: true,
    queued: 0,
    running: false,
    last_stop_reason: null,
    today_jobs: 0,
    today_tokens: 0,
  });
  work.config.enabled = true;
  work.candidates = ["conversation", "workspace", "reject"].map((id) => ({
    id,
    content: `Synthetic ${id} preference`,
    chat_id: "source-chat",
    source_message_id: `source-${id}`,
    created_at: "2026-10-03",
    evidence: `User evidence for ${id}`,
  }));
  let hold: Route | undefined;
  let holdNext = false;
  let polls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/session")
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    else if (path === "/api/workspace") await route.fulfill({ json: state });
    else if (path === "/api/reflection") {
      polls += 1;
      if (holdNext) {
        holdNext = false;
        hold = route;
      } else await route.fulfill({ json: work });
    } else if (path === "/api/work-config") {
      work.config = route.request().postDataJSON();
      await route.fulfill({ json: work });
    } else if (path.startsWith("/api/reflection/candidates/")) {
      const id = path.split("/")[4];
      const candidate = work.candidates.find((item) => item.id === id)!;
      work.candidates = work.candidates.filter((item) => item.id !== id);
      if (path.endsWith("/accept")) {
        const scope: Memory["scope"] = route.request().postDataJSON().scope;
        const saved: Memory = {
          ...candidate,
          scope,
          chat_id: candidate.chat_id,
          origin: "explicit",
          updated_at: candidate.created_at,
        };
        state.memories!.push(saved);
        await route.fulfill({ json: saved });
      } else await route.fulfill({ json: { deleted: true } });
    } else throw new Error(`Unexpected reflection request: ${path}`);
  });
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  return {
    state,
    work,
    polls: () => polls,
    holdNext: () => {
      holdNext = true;
    },
    pending: () => hold,
  };
}

test("reflection suggestions require acceptance, preserve scope, and stay accepted or rejected after reload", async ({
  page,
}) => {
  await page.clock.install();
  const fixture = await reflectionFixture(page);
  const stale = structuredClone(fixture.work);
  fixture.holdNext();
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(1);
  const memory = page.locator(".memory-setup");
  await memory
    .getByLabel("What should Maestro remember?")
    .fill("Unsaved explicit memory");
  expect(fixture.state.memories).toEqual([]);
  for (const id of ["conversation", "workspace", "reject"]) {
    const entry = memory
      .locator(".memory-entry")
      .filter({ hasText: `Synthetic ${id} preference` });
    await expect(entry.getByText("From Source conversation")).toBeVisible();
    await expect(entry.getByText(`User evidence for ${id}`)).toBeVisible();
    await expect(entry.getByLabel("Use proposed memory in")).toHaveValue(
      "conversation",
    );
    if (id === "reject")
      await entry.getByRole("button", { name: "Reject", exact: true }).click();
    else {
      if (id === "workspace")
        await entry
          .getByLabel("Use proposed memory in")
          .selectOption("workspace");
      await entry
        .getByRole("button", { name: "Accept memory", exact: true })
        .click();
    }
    await expect(
      memory.getByLabel("What should Maestro remember?"),
    ).toHaveValue("Unsaved explicit memory");
    await expect(
      entry.getByRole("button", { name: "Accept memory", exact: true }),
    ).toHaveCount(0);
  }
  expect(fixture.work.candidates).toEqual([]);
  expect(fixture.state.memories?.map(({ scope }) => scope)).toEqual([
    "conversation",
    "workspace",
  ]);
  await fixture.pending()!.fulfill({ json: stale });
  await page.clock.fastForward(100);
  await expect(
    memory.getByRole("button", { name: "Accept memory", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    memory.getByText("Synthetic conversation preference", { exact: true }),
  ).toBeVisible();
  await expect(
    memory.getByText("Synthetic workspace preference", { exact: true }),
  ).toBeVisible();
  await expect(
    memory.getByText("Synthetic reject preference", { exact: true }),
  ).toHaveCount(0);
  await expect(
    memory.getByRole("button", { name: "Accept memory", exact: true }),
  ).toHaveCount(0);
});

test("reflection polling preserves drafts, avoids overlap, and ignores late status after pause", async ({
  page,
}) => {
  await page.clock.install();
  const fixture = await reflectionFixture(page);
  const work = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: "Task models & reflection",
      exact: true,
    }),
  });
  await work.getByText("Work limits & memory recall", { exact: true }).click();
  await work
    .getByLabel("Chat idle time (seconds)", { exact: true })
    .fill("123");
  fixture.holdNext();
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(1);
  await page.clock.fastForward(9000);
  expect(fixture.polls()).toBe(1);
  fixture.work.queued = 2;
  await fixture.pending()!.fulfill({ json: fixture.work });
  await expect(work.getByText(/2 queued/)).toBeVisible();
  await expect(
    work.getByLabel("Chat idle time (seconds)", { exact: true }),
  ).toHaveValue("123");
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      value: true,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.fastForward(6000);
  expect(fixture.polls()).toBe(1);
  fixture.holdNext();
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      value: false,
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(fixture.polls).toBe(2);
  const stale = structuredClone(fixture.work);
  await work.getByLabel("Enable background reflection").uncheck();
  fixture.work.queued = 0;
  await work
    .getByRole("button", { name: "Save task settings", exact: true })
    .click();
  await expect(work.getByText("Paused", { exact: true })).toBeVisible();
  await fixture.pending()!.fulfill({ json: stale });
  await page.clock.fastForward(6000);
  await expect(work.getByText("Paused", { exact: true })).toBeVisible();
  expect(fixture.polls()).toBe(2);
  fixture.work.config.enabled = true;
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  fixture.holdNext();
  await page.clock.fastForward(3000);
  await expect.poll(fixture.polls).toBe(3);
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await fixture.pending()!.fulfill({ json: stale });
  await page.clock.fastForward(6000);
  expect(fixture.polls()).toBe(3);
  await expect(
    page.getByRole("heading", { name: "Source conversation", exact: true }),
  ).toBeVisible();
});
