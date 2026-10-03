import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Memory, Workspace } from "../src/api";

const makeMemory = (
  id: string,
  content: string,
  extra: Partial<Memory> = {},
): Memory => ({
  id,
  content,
  kind: "fact",
  origin: "explicit",
  pinned: true,
  scope: "workspace",
  chat_id: null,
  source_message_id: null,
  created_at: "2026-10-03T12:00:00Z",
  updated_at: "2026-10-03T12:00:00Z",
  ...extra,
});

async function mapFixture(page: Page, many = false) {
  await page.goto("/");
  expect((await page.request.get("/api/session")).ok()).toBeTruthy();
  const state: Workspace = await (
    await page.request.get("/api/workspace")
  ).json();
  state.chats = ["project", "other"].map((id) => ({
    id,
    title: id === "project" ? "Synthetic project" : "Other synthetic chat",
    created_at: "2026-10-03",
    updated_at: "2026-10-03",
  }));
  state.active_chat_id = "project";
  state.messages = [];
  state.tasks = [];
  const kinds = ["fact", "preference", "identity", "lesson"] as const;
  state.memories = many
    ? Array.from({ length: 2000 }, (_, index) =>
        makeMemory(
          `record-${String(index).padStart(4, "0")}`,
          `Synthetic memory ${index}`,
          { kind: kinds[index % 4] },
        ),
      )
    : [
        makeMemory("fact", "The synthetic project uses SQLite.", {
          origin: "curated",
          pinned: false,
          chat_id: "project",
          source_message_id: "fact-message",
          evidence: "Our synthetic project uses SQLite.",
        }),
        makeMemory("preference", "Prefer concise synthetic project updates.", {
          kind: "preference",
          scope: "conversation",
          chat_id: "project",
          source_message_id: "preference-message",
          origin: "curated",
          pinned: false,
          evidence: "Please keep synthetic project updates concise.",
        }),
        makeMemory(
          "identity",
          "Favor synthetic changes that can be verified.",
          {
            kind: "identity",
            origin: "reflective",
            pinned: false,
            provenance: [
              { memory_id: "fact" },
              { chat_id: "project", message_id: "style-message" },
            ],
          },
        ),
        makeMemory("lesson", "Compare synthetic migrations with a baseline.", {
          kind: "lesson",
          origin: "reflective",
          pinned: false,
          provenance: [
            { memory_id: "fact" },
            { chat_id: "project", message_id: "migration-message" },
          ],
        }),
        makeMemory(
          "unrelated",
          "An unrelated synthetic fact without recorded sources.",
        ),
        makeMemory("other", "A synthetic fact for the other conversation.", {
          scope: "conversation",
          chat_id: "other",
        }),
      ];
  Object.assign(state.work!, {
    queued: 0,
    running: false,
    candidates: [],
    journal: [],
    memories: state.memories,
  });
  state.work!.config.enabled = false;
  state.work!.config.auto_curate = true;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/session")
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    else if (path === "/api/workspace") await route.fulfill({ json: state });
    else if (path === "/api/reflection")
      await route.fulfill({ json: state.work });
    else if (path.startsWith("/api/memory/")) {
      const id = path.split("/")[3];
      const memory = state.memories!.find((item) => item.id === id)!;
      if (route.request().method() === "PATCH") {
        Object.assign(memory, {
          content: route.request().postDataJSON().content,
          origin: "explicit",
          pinned: true,
          provenance: [],
          evidence: null,
          source_message_id: null,
          chat_id: memory.scope === "conversation" ? memory.chat_id : null,
        });
        await route.fulfill({ json: memory });
      } else {
        state.memories = state.memories!.filter((item) => item.id !== id);
        state.work!.memories = state.memories;
        await route.fulfill({ json: { deleted: true } });
      }
    } else throw new Error(`Unexpected memory-map request: ${path}`);
  });
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  return { state, memory: page.locator(".memory-setup") };
}

test("map shows only recorded relationships, supports keyboard selection, search and scope", async ({
  page,
}) => {
  const { memory } = await mapFixture(page);
  await expect(
    memory.getByRole("button", { name: "Map", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(memory.locator(".memory-map-node")).toHaveCount(6);
  await expect(
    memory.locator("[data-memory-link='fact:identity']"),
  ).toHaveCount(1);
  await expect(memory.locator("[data-memory-link='fact:lesson']")).toHaveCount(
    1,
  );
  await expect(
    memory.locator("[data-conversation-link='project:preference']"),
  ).toHaveCount(1);
  await expect(memory.locator(".memory-map-source")).toHaveCount(1);
  await expect(
    memory.locator("[data-conversation-link$=':other']"),
  ).toHaveCount(0);
  await expect(memory.locator("[data-memory-link$=':unrelated']")).toHaveCount(
    0,
  );
  const identity = memory.getByRole("button", {
    name: "Select memory: Favor synthetic changes that can be verified.",
    exact: true,
  });
  await identity.focus();
  await page.keyboard.press("Enter");
  await expect(identity).toHaveAttribute("aria-pressed", "true");
  await expect(memory.locator(".memory-entry")).toHaveCount(1);
  await expect(
    memory
      .locator(".memory-entry")
      .getByText(/Working identity · AI reflection/),
  ).toBeVisible();
  await expect(
    memory.getByText("Synthetic project · message style-message", {
      exact: true,
    }),
  ).toBeVisible();
  await memory
    .getByRole("button", {
      name: "Highlight memories from Synthetic project",
      exact: true,
    })
    .click();
  await expect(memory.locator(".memory-map-node.related")).toHaveCount(4);
  await memory
    .getByRole("button", {
      name: "Source memory: The synthetic project uses SQLite.",
      exact: true,
    })
    .click();
  await expect(
    memory.getByRole("button", {
      name: "Select memory: The synthetic project uses SQLite.",
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await memory
    .getByRole("combobox", { name: "Memory scope", exact: true })
    .selectOption("conversation");
  await expect(memory.locator(".memory-map-node")).toHaveCount(1);
  await expect(memory.locator(".memory-map-node")).toHaveText(
    "Prefer concise synthetic project updates.",
  );
  await memory
    .getByRole("combobox", { name: "Memory scope", exact: true })
    .selectOption("all");
  await memory.getByLabel("Search memories", { exact: true }).fill("baseline");
  await expect(memory.locator(".memory-map-node")).toHaveCount(1);
  await expect(
    memory
      .locator(".memory-entry")
      .getByText("Compare synthetic migrations with a baseline.", {
        exact: true,
      }),
  ).toBeVisible();
  await memory
    .getByLabel("Search memories", { exact: true })
    .fill("missing synthetic phrase");
  await expect(
    memory.getByText("No memories match these filters.", { exact: true }),
  ).toBeVisible();
});

test("selected memory can be corrected, pinned and forgotten through both views", async ({
  page,
}) => {
  const { memory } = await mapFixture(page);
  await memory
    .getByRole("button", {
      name: "Select memory: Favor synthetic changes that can be verified.",
      exact: true,
    })
    .click();
  const position = await memory
    .locator(".memory-map-node[aria-pressed='true']")
    .getAttribute("style");
  await memory
    .getByRole("button", { name: "Edit saved memory", exact: true })
    .click();
  await memory
    .getByRole("textbox", { name: "Edit memory", exact: true })
    .fill("Verify every synthetic change.");
  await memory
    .getByRole("button", { name: "Update memory", exact: true })
    .click();
  await expect(
    memory
      .locator(".memory-entry")
      .getByText("Verify every synthetic change.", { exact: true }),
  ).toBeVisible();
  await expect(
    memory.locator(".memory-entry").getByText(/Working identity · User-pinned/),
  ).toBeVisible();
  await expect(
    memory.locator(".memory-map-node[aria-pressed='true']"),
  ).toHaveAttribute("style", position!);
  await expect(
    memory.locator("[data-memory-link='fact:identity']"),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await memory.getByRole("button", { name: "List", exact: true }).click();
  await expect(memory.locator(".memory-entry")).toHaveCount(6);
  await memory
    .locator(".memory-entry")
    .filter({ hasText: "Verify every synthetic change." })
    .getByRole("button", { name: "Forget saved memory" })
    .click();
  await expect(
    memory.getByText("Verify every synthetic change.", { exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    memory.getByRole("button", {
      name: "Select memory: Verify every synthetic change.",
      exact: true,
    }),
  ).toHaveCount(0);
});

test("large maps bound visible nodes, page through groups, zoom and remain usable on mobile", async ({
  page,
}) => {
  const { memory } = await mapFixture(page, true);
  await expect(memory.locator(".memory-map-node")).toHaveCount(20);
  await memory.getByRole("button", { name: "Next facts", exact: true }).click();
  await expect(
    memory.getByRole("button", {
      name: "Select memory: Synthetic memory 20",
      exact: true,
    }),
  ).toBeVisible();
  await memory
    .getByRole("button", { name: "Zoom in memory map", exact: true })
    .click();
  await expect(memory.locator(".memory-map-zoom")).toContainText("120%");
  await memory
    .getByRole("button", { name: "Reset memory map view", exact: true })
    .click();
  await expect(memory.locator(".memory-map-zoom")).toContainText("100%");
  await memory
    .getByLabel("Search memories", { exact: true })
    .fill("memory 1999");
  await expect(memory.locator(".memory-map-node")).toHaveCount(1);
  await expect(
    memory.getByRole("button", {
      name: "Select memory: Synthetic memory 1999",
      exact: true,
    }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await memory.getByLabel("Search memories", { exact: true }).fill("");
  await expect(memory.locator(".memory-map-node")).toHaveCount(20);
  const viewport = memory.getByRole("region", {
    name: "Memory map",
    exact: true,
  });
  await viewport.focus();
  await page.keyboard.press("ArrowDown");
  await expect
    .poll(() => viewport.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("synthetic memory map desktop and mobile visual previews", async ({
  page,
}) => {
  const { memory } = await mapFixture(page);
  await memory
    .getByRole("button", {
      name: "Select memory: Favor synthetic changes that can be verified.",
      exact: true,
    })
    .click();
  await memory
    .getByRole("button", { name: "Reset memory map view", exact: true })
    .click();
  const directory = join(tmpdir(), "Maestro-map");
  await mkdir(directory, { recursive: true });
  await memory.screenshot({
    path: join(directory, "desktop.png"),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    memory.getByRole("button", { name: "Map", exact: true }),
  ).toBeVisible();
  await memory.screenshot({
    path: join(directory, "mobile.png"),
    animations: "disabled",
  });
});

test("selected node follows its page when an earlier record is removed by background work", async ({
  page,
}) => {
  await page.clock.install();
  const { memory, state } = await mapFixture(page, true);
  state.work!.config.enabled = true;
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  for (let pageIndex = 0; pageIndex < 2; pageIndex++)
    await memory
      .getByRole("button", { name: "Next facts", exact: true })
      .click();
  const selected = memory.getByRole("button", {
    name: "Select memory: Synthetic memory 40",
    exact: true,
  });
  await selected.click();
  state.memories!.splice(0, 1);
  await page.clock.fastForward(3000);
  await expect(memory.locator(".badge")).toHaveText("1999 saved");
  await expect(selected).toBeVisible();
  await expect(selected).toHaveAttribute("aria-pressed", "true");
});
