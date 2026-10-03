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
    memories: state.memories,
    journal: [],
    next_reflection_at: null,
  });
  work.config.enabled = true;
  work.config.auto_curate = false;
  work.config.periodic_reflection = false;
  work.config.reflection_interval_minutes = 360;
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
    } else if (path.startsWith("/api/memory/")) {
      const id = path.split("/")[3];
      const memory = state.memories!.find((item) => item.id === id)!;
      if (route.request().method() === "PATCH") {
        Object.assign(memory, {
          content: route.request().postDataJSON().content,
          pinned: true,
          origin: "explicit",
          provenance: [],
          evidence: null,
        });
        await route.fulfill({ json: memory });
      } else {
        state.memories = state.memories!.filter((item) => item.id !== id);
        work.memories = state.memories;
        await route.fulfill({ json: { deleted: true } });
      }
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
  await page
    .locator(".memory-setup")
    .getByRole("button", { name: "List", exact: true })
    .click();
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
  await page
    .locator(".memory-setup")
    .getByRole("button", { name: "List", exact: true })
    .click();
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

test("automatic memory, periodic reflection and the private journal stay inspectable and editable", async ({
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
  const memory = page.locator(".memory-setup");
  const periodic = work.getByLabel(
    "Reflect periodically on identity and working style",
  );
  const interval = work.getByLabel("Reflection interval (minutes)");
  await expect(periodic).toBeDisabled();
  await expect(interval).toBeDisabled();
  await work.getByLabel("AI curates memory automatically").check();
  await periodic.check();
  await interval.fill("120");
  await work
    .getByRole("button", { name: "Save task settings", exact: true })
    .click();
  await expect(work.getByRole("status")).toHaveText("Task settings saved.");
  await expect(memory.getByLabel("What should Maestro remember?")).toBeHidden();
  fixture.work.candidates = [];
  const records: Memory[] = [
    {
      id: "auto-fact",
      content: "The user works on Maestro.",
      kind: "fact",
      origin: "curated",
      pinned: false,
      scope: "workspace",
      chat_id: "source-chat",
      source_message_id: "source-fact",
      evidence: "I work on Maestro.",
      created_at: "2026-10-03",
      updated_at: "2026-10-03",
    },
    {
      id: "auto-identity",
      content: "I favor small, measured changes.",
      kind: "identity",
      origin: "reflective",
      pinned: false,
      scope: "workspace",
      chat_id: null,
      source_message_id: null,
      provenance: [{ memory_id: "auto-fact" }],
      created_at: "2026-10-03",
      updated_at: "2026-10-03",
    },
  ];
  fixture.state.memories!.push(...records);
  fixture.work.journal = [
    {
      id: "journal-1",
      created_at: "2026-10-03T12:00:00+02:00",
      kind: "reflection",
      summary: "Kept the project fact and clarified working style.",
      changes: [
        ...records.map((item) => ({
          operation: "add" as const,
          memory_id: item.id,
          kind: item.kind!,
          content: item.content,
        })),
        {
          operation: "remove",
          memory_id: "obsolete-preference",
          kind: "preference",
          content: "",
        },
      ],
      sources: [{ chat_id: "source-chat", message_id: "source-style" }],
      models: ["qwen2.5:7b", "gpt-oss:20b"],
    },
  ];
  await work.getByText("Work limits & memory recall", { exact: true }).click();
  await work.getByLabel("Daily reflection jobs", { exact: true }).fill("123");
  await page.clock.fastForward(3000);
  const fact = memory
    .locator(".memory-entry")
    .filter({ has: page.getByText(records[0].content, { exact: true }) });
  const identity = memory
    .locator(".memory-entry")
    .filter({ hasText: records[1].content });
  await expect(fact.getByText(/User fact · AI-curated/)).toBeVisible();
  await expect(
    identity.getByText(/Working identity · AI reflection/),
  ).toBeVisible();
  await expect(
    memory.getByRole("button", { name: "Accept memory", exact: true }),
  ).toHaveCount(0);
  await fact.getByText("Source", { exact: true }).click();
  await expect(
    fact.getByText("I work on Maestro.", { exact: true }),
  ).toBeVisible();
  await identity.getByText("Source", { exact: true }).click();
  await expect(
    identity.getByRole("button", {
      name: "Source memory: The user works on Maestro.",
      exact: true,
    }),
  ).toBeVisible();
  await work.getByText("Private reflection journal", { exact: true }).click();
  await expect(
    work.getByText("Kept the project fact and clarified working style.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(work.getByText(/qwen2.5:7b → gpt-oss:20b/)).toBeVisible();
  await work
    .getByText("3 memory changes · 1 source messages", { exact: true })
    .click();
  await expect(
    work.getByText(/Added identity: I favor small, measured changes/),
  ).toBeVisible();
  await expect(
    work.getByText("Forgot preference: memory obsolete-preference", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    work.getByText("Source conversation · message source-style", {
      exact: true,
    }),
  ).toBeVisible();
  await identity.getByRole("button", { name: "Edit saved memory" }).click();
  await memory
    .getByRole("textbox", { name: "Edit memory", exact: true })
    .fill("I make small changes and verify their effect.");
  await memory
    .getByRole("button", { name: "Update memory", exact: true })
    .click();
  await expect(
    memory.getByText("I make small changes and verify their effect.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    memory.getByText(/Working identity · User-pinned/),
  ).toBeVisible();
  await expect(
    work.getByLabel("Daily reflection jobs", { exact: true }),
  ).toHaveValue("123");
  await fact.getByRole("button", { name: "Forget saved memory" }).click();
  await expect(
    memory.getByText(records[0].content, { exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .locator(".memory-setup")
    .getByRole("button", { name: "List", exact: true })
    .click();
  await expect(
    work.getByLabel("AI curates memory automatically"),
  ).toBeChecked();
  await expect(periodic).toBeChecked();
  await expect(interval).toHaveValue("120");
  await expect(
    memory.getByText("I make small changes and verify their effect.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    memory.getByText(records[0].content, { exact: true }),
  ).toHaveCount(0);
  await work.getByLabel("AI curates memory automatically").uncheck();
  await expect(periodic).not.toBeChecked();
  await expect(periodic).toBeDisabled();
  await expect(interval).toBeDisabled();
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
  await page
    .locator(".memory-setup")
    .getByRole("button", { name: "List", exact: true })
    .click();
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
