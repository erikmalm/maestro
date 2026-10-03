import { expect, test, type Page, type Route } from "@playwright/test";
import type { Workspace } from "../src/api";

async function taskFixture(page: Page) {
  await page.goto("/");
  expect((await page.request.get("/api/session")).ok()).toBeTruthy();
  const state: Workspace = await (
    await page.request.get("/api/workspace")
  ).json();
  state.tasks = [
    {
      id: "legacy",
      title: "Legacy synthetic task",
      details: "",
      priority: "normal",
      done: false,
      created_at: "2026-10-03",
    },
    {
      id: "ai",
      title: "Investigate a synthetic regression",
      details: "Compare the synthetic result with its baseline.",
      priority: "high",
      done: false,
      created_at: "2026-10-03",
      initiated_by: "maestro",
      suggested_assignee: "maestro",
    },
  ];
  state.work!.config.enabled = false;
  Object.assign(state.work!, {
    worker_available: true,
    running: false,
    queued: 0,
    tasks: state.tasks,
    journal: [],
  });
  Object.assign(state.work!.config, {
    auto_curate: false,
    periodic_reflection: false,
    auto_create_tasks: false,
  });
  state.chats = [];
  state.messages = [];
  state.active_chat_id = null;
  const writes: { method: string; body: Record<string, unknown> }[] = [];
  let polls = 0;
  let workspaceCalls = 0;
  let holdNext = false;
  let held: Route | undefined;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path === "/api/session")
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    else if (path === "/api/workspace") {
      workspaceCalls += 1;
      await route.fulfill({ json: state });
    } else if (path === "/api/reflection") {
      polls += 1;
      if (holdNext) {
        holdNext = false;
        held = route;
      } else await route.fulfill({ json: state.work });
    } else if (path === "/api/work-config") {
      const body = route.request().postDataJSON();
      writes.push({ method, body });
      state.work!.config = body;
      await route.fulfill({ json: state.work });
    } else if (path === "/api/tasks" && method === "POST") {
      const body = route.request().postDataJSON();
      writes.push({ method, body });
      state.tasks.push({
        ...body,
        id: "manual",
        initiated_by: "user",
        done: false,
        created_at: "2026-10-03",
      });
      await route.fulfill({ json: state });
    } else if (path.startsWith("/api/tasks/") && method === "PATCH") {
      const body = route.request().postDataJSON();
      writes.push({ method, body });
      Object.assign(
        state.tasks.find((task) => task.id === path.split("/")[3])!,
        body,
      );
      await route.fulfill({ json: state });
    } else
      throw new Error(`Unexpected task-metadata request: ${method} ${path}`);
  });
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  return {
    state,
    writes,
    polls: () => polls,
    workspaceCalls: () => workspaceCalls,
    holdNext: () => {
      holdNext = true;
    },
    held: () => held,
    row: (title: string) =>
      page
        .locator(".task-row")
        .filter({ has: page.getByText(title, { exact: true }) }),
  };
}

test("manual tasks choose an assignee while legacy tasks retain You defaults", async ({
  page,
}) => {
  const { row, writes } = await taskFixture(page);
  await expect(
    row("Legacy synthetic task").getByText("Initiated by You", { exact: true }),
  ).toBeVisible();
  await expect(
    row("Legacy synthetic task").getByRole("combobox", {
      name: "Suggested assignee for Legacy synthetic task",
      exact: true,
    }),
  ).toHaveValue("user");
  await page.getByRole("button", { name: "New task", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New task", exact: true });
  const assignee = dialog.getByRole("combobox", {
    name: "Suggested assignee",
    exact: true,
  });
  await expect(assignee).toHaveValue("user");
  await dialog
    .getByLabel("What would you like to do?")
    .fill("Synthetic manual task");
  await dialog
    .getByLabel("Context & completion criteria")
    .fill("A concrete synthetic next step.");
  await assignee.selectOption("maestro");
  await dialog.getByRole("button", { name: "Save task", exact: true }).click();
  const manual = row("Synthetic manual task");
  await expect(
    manual.getByText("Initiated by You", { exact: true }),
  ).toBeVisible();
  await expect(
    manual.getByRole("combobox", {
      name: "Suggested assignee for Synthetic manual task",
      exact: true,
    }),
  ).toHaveValue("maestro");
  expect(writes[0]).toEqual({
    method: "POST",
    body: {
      title: "Synthetic manual task",
      details: "A concrete synthetic next step.",
      priority: "normal",
      suggested_assignee: "maestro",
    },
  });
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    manual.getByRole("combobox", {
      name: "Suggested assignee for Synthetic manual task",
      exact: true,
    }),
  ).toHaveValue("maestro");
});

test("AI task assignment can change without changing who initiated it, including after completion", async ({
  page,
}) => {
  const { state, row, writes } = await taskFixture(page);
  const task = row("Investigate a synthetic regression");
  await expect(
    task.getByText("Initiated by Maestro", { exact: true }),
  ).toBeVisible();
  await expect(task.locator(".task-metadata")).toContainText("Suggested for");
  const assignee = task.getByRole("combobox", {
    name: "Suggested assignee for Investigate a synthetic regression",
    exact: true,
  });
  await expect(assignee).toHaveValue("maestro");
  await assignee.selectOption("user");
  await expect(assignee).toHaveValue("user");
  await task
    .getByRole("button", {
      name: "Complete Investigate a synthetic regression",
      exact: true,
    })
    .click();
  await expect(
    task.getByRole("button", {
      name: "Reopen Investigate a synthetic regression",
      exact: true,
    }),
  ).toBeVisible();
  expect(writes).toEqual([
    { method: "PATCH", body: { suggested_assignee: "user" } },
    { method: "PATCH", body: { done: true } },
  ]);
  expect(state.tasks.find((item) => item.id === "ai")).toMatchObject({
    initiated_by: "maestro",
    suggested_assignee: "user",
    done: true,
  });
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    task.getByText("Initiated by Maestro", { exact: true }),
  ).toBeVisible();
  await expect(assignee).toHaveValue("user");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("Tasks receives reviewed AI tasks without replacing a draft or restoring stale assignments", async ({
  page,
}) => {
  await page.clock.install();
  const fixture = await taskFixture(page);
  const { state, row } = fixture;
  state.work!.config.enabled = true;
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  const calls = fixture.workspaceCalls();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  const draft = page
    .getByRole("dialog", { name: "New task", exact: true })
    .getByLabel("What would you like to do?");
  await draft.fill("Keep this unsaved synthetic task draft.");
  const task = {
    ...state.tasks[1],
    id: "new-ai",
    title: "Review synthetic coverage",
    suggested_assignee: "user" as const,
  };
  state.tasks.push(task);
  state.work!.journal = [
    {
      id: "task-journal",
      kind: "reflection",
      assessment_basis: "capabilities_and_practices",
      created_at: "2026-10-03",
      summary: "Added one reviewed synthetic task.",
      changes: [],
      sources: [],
      models: ["synthetic-model"],
      tasks_created: [
        { id: task.id, title: task.title, suggested_assignee: "user" },
      ],
    },
  ];
  await page.clock.fastForward(3000);
  await expect(page.locator(".task-row")).toHaveCount(3);
  await expect(draft).toHaveValue("Keep this unsaved synthetic task draft.");
  expect(fixture.workspaceCalls()).toBe(calls);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    row(task.title).getByText("Initiated by Maestro", { exact: true }),
  ).toBeVisible();
  const stale = structuredClone(state.work);
  fixture.holdNext();
  await page.clock.fastForward(3000);
  await expect.poll(fixture.held).toBeTruthy();
  const assignee = row("Investigate a synthetic regression").getByRole(
    "combobox",
    {
      name: "Suggested assignee for Investigate a synthetic regression",
      exact: true,
    },
  );
  await assignee.selectOption("user");
  await expect(assignee).toHaveValue("user");
  await fixture.held()!.fulfill({ json: stale });
  await page.clock.fastForward(100);
  await expect(assignee).toHaveValue("user");
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  const journal = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: "Private reflection journal",
      exact: true,
    }),
  });
  await expect(
    journal.getByText("Added one reviewed synthetic task.", { exact: true }),
  ).toBeVisible();
  await expect(journal.getByText(/Self-improvement reflection/)).toBeVisible();
  await expect(
    journal.getByText(
      /Ideas without chat evidence are experiments to evaluate/,
    ),
  ).toBeVisible();
  await expect(
    journal.getByText("0 memory changes · 0 source messages", { exact: true }),
  ).toBeVisible();
  await journal.getByText("1 tasks created", { exact: true }).click();
  await expect(
    journal.getByText("Review synthetic coverage · Suggested for You", {
      exact: true,
    }),
  ).toBeVisible();
});

test("AI task creation is an explicit saved setting that requires periodic automatic reflection", async ({
  page,
}) => {
  const { writes } = await taskFixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const work = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: "Task models & reflection",
      exact: true,
    }),
  });
  const create = work.getByLabel("AI can create tasks during reflection", {
    exact: true,
  });
  await expect(create).not.toBeChecked();
  await expect(create).toBeDisabled();
  await work
    .getByLabel("AI curates memory automatically", { exact: true })
    .check();
  await expect(create).toBeDisabled();
  await work
    .getByLabel("Reflect periodically on identity and working style", {
      exact: true,
    })
    .check();
  await create.check();
  await work
    .getByRole("button", { name: "Save task settings", exact: true })
    .click();
  await expect(work.getByRole("status")).toHaveText("Task settings saved.");
  expect(writes[0].body).toMatchObject({
    auto_curate: true,
    periodic_reflection: true,
    auto_create_tasks: true,
  });
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(create).toBeChecked();
  await work
    .getByLabel("Reflect periodically on identity and working style", {
      exact: true,
    })
    .uncheck();
  await expect(create).not.toBeChecked();
  await expect(create).toBeDisabled();
});
