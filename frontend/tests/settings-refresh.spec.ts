import { expect, test, type Route } from "@playwright/test";
import type { Workspace } from "../src/api";

const initial: Workspace = {
  tasks: [],
  chats: [],
  active_chat_id: null,
  messages: [],
  limits: { run_usd: 1, daily_usd: 5, monthly_usd: 50, max_tokens: 100000 },
  usage: {
    today_usd: 0,
    month_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    calls: 0,
    reserved_usd: 0,
    uncertain: [],
  },
  provider: {
    config: {
      base_url: "http://127.0.0.1:11434",
      protocol: "ollama",
      model: "old-model",
      input_usd_per_million: 0,
      output_usd_per_million: 0,
      pricing_verified: true,
      max_output_tokens: 1024,
      ollama_context_tokens: 8192,
      ollama_threads: 0,
      ollama_keep_alive_minutes: 5,
    },
    credentials_required: false,
    credentials_present: false,
    credential_source: "not_required",
    models: [],
    tested_at: null,
  },
  web_search: {
    config: { enabled: true, daily_limit: 20, max_results: 3 },
    searches_today: 0,
    remaining_today: 20,
    paused_until: null,
    credentials_present: true,
    credential_source: "session",
    tested_at: "2026-10-01T00:00:00Z",
  },
  capabilities: {
    mode: "local",
    live_ai: true,
    task_execution: false,
    delegation: false,
    github_pr: false,
    secure_credentials: false,
  },
};

for (const [name, order, failed] of [
  ["older response first", [0, 1], -1],
  ["newer response first", [1, 0], -1],
  ["superseded failure", [1, 0], 0],
  ["current failure", [1, 0], 1],
] as const) {
  test(`concurrent settings refresh: ${name}`, async ({ page }) => {
    const state = structuredClone(initial);
    const pending: { route: Route; snapshot: Workspace }[] = [];
    let loaded = false;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/session") {
        await route.fulfill({ json: { csrf: "synthetic-csrf" } });
      } else if (path === "/api/workspace") {
        if (loaded) pending.push({ route, snapshot: structuredClone(state) });
        else {
          loaded = true;
          await route.fulfill({ json: state });
        }
      } else if (path === "/api/provider") {
        state.provider.config = route.request().postDataJSON().config;
        await route.fulfill({ json: state.provider });
      } else if (path === "/api/web-search") {
        state.web_search.config = route.request().postDataJSON().config;
        state.web_search.remaining_today = state.web_search.config.daily_limit;
        await route.fulfill({ json: state.web_search });
      } else throw new Error(`Unexpected API request: ${path}`);
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByLabel("Daily search cap").fill("30");
    await page.getByRole("button", { name: "Save search settings" }).click();
    await expect.poll(() => pending.length).toBe(1);
    await page.getByLabel("Model ID", { exact: true }).fill("new-model");
    await page
      .getByRole("button", { name: "Save connection", exact: true })
      .click();
    await expect.poll(() => pending.length).toBe(2);
    await page.getByRole("button", { name: "Workspace", exact: true }).click();

    let currentModel = "old-model";
    for (const index of order) {
      const { route, snapshot } = pending[index];
      const response = page.waitForResponse(
        (next) => next.request() === route.request(),
      );
      await route.fulfill(
        index === failed
          ? {
              status: 500,
              json: {
                detail: `${index ? "Current" : "Superseded"} refresh failed.`,
              },
            }
          : { json: snapshot },
      );
      await (await response).finished();
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      if (index === 1 && failed !== 1) currentModel = "new-model";
      await expect(page.locator(".composer-bottom small")).toHaveText(
        currentModel,
      );
      await expect(page.getByText(/Automatic web search/)).toContainText(
        `0 / ${currentModel === "new-model" ? 30 : 20} today`,
      );
      await expect(
        page.getByText("Superseded refresh failed.", { exact: true }),
      ).toHaveCount(0);
    }
    if (failed === 1) {
      await expect(page.getByRole("alert")).toContainText(
        "Current refresh failed.",
      );
    } else {
      await expect(page.getByRole("alert")).toHaveCount(0);
    }
  });
}

test("a delayed limits save keeps newer provider settings", async ({
  page,
}) => {
  const state = structuredClone(initial);
  let saved: { route: Route; snapshot: Workspace } | undefined;
  let reads = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/session") {
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    } else if (path === "/api/workspace") {
      reads += 1;
      await route.fulfill({ json: state });
    } else if (path === "/api/limits") {
      state.limits = route.request().postDataJSON();
      saved = { route, snapshot: structuredClone(state) };
    } else if (path === "/api/provider") {
      state.provider.config = route.request().postDataJSON().config;
      await route.fulfill({ json: state.provider });
    } else throw new Error(`Unexpected API request: ${path}`);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Per day", { exact: true }).fill("9");
  await page.getByRole("button", { name: "Save limits", exact: true }).click();
  await expect.poll(() => !!saved).toBe(true);
  await page.getByLabel("Model ID", { exact: true }).fill("new-model");
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await expect(page.locator(".composer-bottom small")).toHaveText("new-model");
  await saved!.route.fulfill({ json: saved!.snapshot });
  await expect(page.getByText("Limits saved.", { exact: true })).toBeVisible();
  await expect(page.locator(".composer-bottom small")).toHaveText("new-model");
  await expect.poll(() => reads).toBe(3);
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("9");
});

test("settings refreshes preserve an explicitly selected new chat", async ({
  page,
}) => {
  const state = structuredClone(initial);
  state.chats = ["a", "b"].map((id) => ({
    id,
    title: `Chat ${id}`,
    created_at: "2026-10-01",
    updated_at: "2026-10-01",
  }));
  state.active_chat_id = "a";
  let created: { route: Route; snapshot: Workspace } | undefined;
  const pending: { route: Route; snapshot: Workspace }[] = [];
  let loaded = false;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/session") {
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    } else if (url.pathname === "/api/workspace") {
      const snapshot = structuredClone(state);
      snapshot.active_chat_id = url.searchParams.get("chat_id");
      if (loaded) pending.push({ route, snapshot });
      else {
        loaded = true;
        await route.fulfill({ json: snapshot });
      }
    } else if (url.pathname === "/api/chats") {
      state.active_chat_id = "b";
      created = { route, snapshot: structuredClone(state) };
    } else if (url.pathname === "/api/provider") {
      state.provider.config = route.request().postDataJSON().config;
      await route.fulfill({ json: state.provider });
    } else if (url.pathname === "/api/web-search") {
      state.web_search.config = route.request().postDataJSON().config;
      await route.fulfill({ json: state.web_search });
    } else throw new Error(`Unexpected API request: ${url.pathname}`);
  });
  await page.goto("/?chat=a");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect.poll(() => !!created).toBe(true);
  await page.getByLabel("Model ID", { exact: true }).fill("new-model");
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await expect.poll(() => pending.length).toBe(1);
  await created!.route.fulfill({ json: created!.snapshot });
  await expect.poll(() => pending.length).toBe(2);
  await page.getByLabel("Daily search cap").fill("30");
  await page.getByRole("button", { name: "Save search settings" }).click();
  await expect.poll(() => pending.length).toBe(3);
  await pending[2].route.fulfill({ json: pending[2].snapshot });
  await pending[1].route.fulfill({ json: pending[1].snapshot });
  await expect(
    page.getByRole("heading", { name: "Chat b", exact: true }),
  ).toBeVisible();
  await pending[0].route.fulfill({ json: pending[0].snapshot });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(page).toHaveURL(/chat=b/);
  await expect(
    page.getByRole("heading", { name: "Chat b", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".composer-bottom small")).toHaveText("new-model");
  await expect(page.getByText(/Automatic web search/)).toContainText(
    "0 / 30 today",
  );
});
