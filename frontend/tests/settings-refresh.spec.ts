import { expect, test, type Page, type Route } from "@playwright/test";
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
      orchestrator_model: "",
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

async function mockSettings(
  page: Page,
  state: Workspace,
  hold: string,
  failRefresh = () => false,
) {
  let pending: Route | undefined;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === hold && route.request().method() !== "GET") pending = route;
    else if (path === "/api/session") {
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    } else if (path === "/api/workspace") {
      await route.fulfill(
        failRefresh()
          ? { status: 500, json: { detail: "Synthetic refresh failed." } }
          : { json: state },
      );
    } else if (path === "/api/limits") {
      state.limits = route.request().postDataJSON();
      await route.fulfill({ json: state });
    } else if (path === "/api/provider" || path === "/api/web-search") {
      const status =
        path === "/api/provider" ? state.provider : state.web_search;
      if (route.request().method() === "PUT")
        status.config = route.request().postDataJSON().config;
      await route.fulfill({ json: status });
    } else throw new Error(`Unexpected API request: ${path}`);
  });
  return () => pending;
}

for (const managed of [false, true]) {
  test(`container credentials are ${managed ? "managed outside the UI" : "saved only for the session"}`, async ({
    page,
  }) => {
    const state = structuredClone(initial);
    Object.assign(state.provider.config, {
      base_url: "https://provider.example/v1",
      protocol: "responses",
      model: "synthetic-model",
    });
    for (const status of [state.provider, state.web_search]) {
      status.persist_supported = false;
      status.managed_credentials = managed;
      status.credentials_present = true;
      status.credential_source = managed ? "mounted_secret" : "session";
    }
    state.provider.credentials_required = true;
    await mockSettings(page, state, "");
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByText(/Windows Credential Manager/)).toHaveCount(0);
    if (managed) {
      await expect(page.getByLabel("API key", { exact: true })).toBeDisabled();
      await expect(
        page.getByLabel("Ollama search API key", { exact: true }),
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Remove API key", exact: true }),
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Remove search key", exact: true }),
      ).toBeDisabled();
      await expect(
        page.getByText(/This key is managed by the server/),
      ).toHaveCount(2);
    } else {
      await expect(
        page.getByText(/New keys stay in server memory/),
      ).toHaveCount(2);
      for (const [path, field, button] of [
        ["/api/provider", "API key", "Save connection"],
        ["/api/web-search", "Ollama search API key", "Save search settings"],
      ]) {
        await page
          .getByLabel(field, { exact: true })
          .fill("synthetic-session-key");
        const saved = page.waitForRequest(
          (request) =>
            new URL(request.url()).pathname === path &&
            request.method() === "PUT",
        );
        await page.getByRole("button", { name: button, exact: true }).click();
        expect((await saved).postDataJSON().persist).toBe(false);
        await expect(
          page.getByRole("button", { name: button, exact: true }),
        ).toBeEnabled();
      }
    }
  });
}

for (const search of [false, true]) {
  test(`an older ${search ? "search" : "provider"} save cannot restore invalidated settings`, async ({
    page,
  }) => {
    const state = structuredClone(initial);
    if (!search) {
      Object.assign(state.provider.config, {
        base_url: "https://provider.example/v1",
        protocol: "responses",
        model: "synthetic-model",
      });
      Object.assign(state.provider, {
        credentials_required: true,
        credentials_present: true,
      });
    }
    let failRefresh = false;
    const pending = await mockSettings(
      page,
      state,
      search ? "/api/web-search" : "/api/provider",
      () => failRefresh,
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const save = page.getByRole("button", {
      name: search ? "Save search settings" : "Save connection",
      exact: true,
    });
    await save.click();
    await expect.poll(() => !!pending()).toBe(true);
    const oldStatus = structuredClone(
      search ? state.web_search : state.provider,
    );
    if (search) {
      state.web_search.config.enabled = false;
      state.web_search.tested_at = null;
    } else state.provider.config.pricing_verified = false;
    await page
      .getByRole("button", { name: "Save limits", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Save limits", exact: true }),
    ).toBeEnabled();
    const field = page.getByLabel(
      search ? "Daily search cap" : "Maximum output tokens per reply",
    );
    await field.fill(search ? "30" : "1500");
    failRefresh = true;
    await pending()!.fulfill({ json: oldStatus });
    await expect(page.getByRole("alert")).toHaveText(
      "Synthetic refresh failed.",
    );
    await expect(field).toHaveValue(search ? "30" : "1500");
    if (search) {
      await expect(page.locator(".ollama-search-setup .badge")).toHaveText(
        "Disabled",
      );
      await expect(
        page.getByLabel("Let Maestro decide when to search"),
      ).not.toBeChecked();
      await expect(page.getByText(/Search connection tested/)).toHaveCount(0);
    } else {
      await expect(
        page.getByLabel("Use these prices for cost estimates"),
      ).not.toBeChecked();
      await expect(save).toBeDisabled();
    }
  });
}

for (const change of [
  "pricing",
  "search authentication",
  "search cooldown",
] as const) {
  test(`open settings receive chat updates: ${change}`, async ({ page }) => {
    const state = structuredClone(initial);
    state.chats = [
      {
        id: "thread",
        title: "Thread",
        created_at: "2026-10-01",
        updated_at: "2026-10-01",
      },
    ];
    state.active_chat_id = "thread";
    if (change === "pricing") {
      Object.assign(state.provider.config, {
        base_url: "https://provider.example/v1",
        protocol: "responses",
        model: "synthetic-model",
        input_usd_per_million: 1,
        output_usd_per_million: 2,
      });
      Object.assign(state.provider, {
        credentials_required: true,
        credentials_present: true,
      });
    }
    const pending = await mockSettings(page, state, "/api/chat");
    await page.goto("/?chat=thread");
    await page.getByLabel("Message Maestro").fill("Synthetic message");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect.poll(() => !!pending()).toBe(true);
    await page.getByRole("button", { name: "Settings", exact: true }).click();

    const pricing = change === "pricing";
    const draft = page.getByLabel(
      pricing ? "Maximum output tokens per reply" : "Daily search cap",
    );
    const key = page.getByLabel(pricing ? "API key" : "Ollama search API key", {
      exact: true,
    });
    const confirmation = page.getByLabel("Use these prices for cost estimates");
    await draft.fill(pricing ? "1500" : "30");
    await key.fill("synthetic-unsaved-key");
    if (pricing) state.provider.config.pricing_verified = false;
    else {
      state.web_search.searches_today = 1;
      state.web_search.remaining_today = 19;
      if (change === "search authentication") {
        state.web_search.config.enabled = false;
        state.web_search.tested_at = null;
      } else
        state.web_search.paused_until = new Date(
          Date.now() + 60000,
        ).toISOString();
    }
    await pending()!.fulfill({ json: state });
    await expect(draft).toHaveValue(pricing ? "1500" : "30");
    await expect(key).toHaveValue("synthetic-unsaved-key");

    if (pricing) {
      await expect(confirmation).not.toBeChecked();
      await expect(
        page.getByRole("button", { name: "Save connection", exact: true }),
      ).toBeDisabled();
      // A confirmation matching a newer saved snapshot must stop overriding later invalidations.
      await confirmation.check();
      for (const verified of [true, false]) {
        state.provider.config.pricing_verified = verified;
        await page
          .getByRole("button", { name: "Save limits", exact: true })
          .click();
        await expect(
          page.getByRole("button", { name: "Save limits", exact: true }),
        ).toBeEnabled();
        await expect(confirmation).toBeChecked({ checked: verified });
      }
      await confirmation.check();
      const saved = page.waitForRequest("**/api/provider");
      await page
        .getByRole("button", { name: "Save connection", exact: true })
        .click();
      expect((await saved).postDataJSON()).toMatchObject({
        config: { pricing_verified: true, max_output_tokens: 1500 },
        api_key: "synthetic-unsaved-key",
      });
      await expect(key).toHaveValue("");
    } else {
      const search = page.locator(".ollama-search-setup");
      await expect(search.getByText(/1 \/ 20 searches today/)).toBeVisible();
      if (change === "search authentication") {
        await expect(search.locator(".badge")).toHaveText("Disabled");
        await expect(search.getByText(/Search connection tested/)).toHaveCount(
          0,
        );
        await expect(
          page.getByLabel("Let Maestro decide when to search"),
        ).not.toBeChecked();
      } else {
        await expect(
          search.getByText(/Search service cooldown until/),
        ).toBeVisible();
        await expect(
          page.getByRole("button", { name: "Test search connection" }),
        ).toBeDisabled();
      }
    }
  });
}

for (const search of [false, true]) {
  test(`edits during ${search ? "search" : "provider"} save remain unsaved`, async ({
    page,
  }) => {
    const state = structuredClone(initial);
    const pending = await mockSettings(
      page,
      state,
      search ? "/api/web-search" : "/api/provider",
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const field = page.getByLabel(
      search ? "Daily search cap" : "Maximum output tokens per reply",
    );
    const key = page.getByLabel("Ollama search API key");
    const save = page.getByRole("button", {
      name: search ? "Save search settings" : "Save connection",
      exact: true,
    });
    await field.fill(search ? "30" : "1500");
    if (search) await key.fill("synthetic-submitted-key");
    await save.click();
    await expect.poll(() => !!pending()).toBe(true);
    if (search) await expect(key).toHaveValue("");
    await field.fill(search ? "20" : "1024");
    if (search) await key.fill("synthetic-new-draft-key");
    // An unrelated workspace refresh must preserve the revert while the save is pending.
    await page
      .getByRole("button", { name: "Save limits", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Save limits", exact: true }),
    ).toBeEnabled();
    const status = search ? state.web_search : state.provider;
    status.config = pending()!.request().postDataJSON().config;
    await pending()!.fulfill({ json: status });
    await expect(save).toBeEnabled();
    await expect(field).toHaveValue(search ? "20" : "1024");
    if (search) await expect(key).toHaveValue("synthetic-new-draft-key");
  });
}

for (const failed of [false, true]) {
  test(`search drafts survive ${failed ? "failed save recovery" : "key removal"}`, async ({
    page,
  }) => {
    const state = structuredClone(initial);
    const pending = await mockSettings(
      page,
      state,
      failed ? "/api/web-search" : "/api/web-search/key",
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByLabel("Daily search cap").fill("30");
    await page
      .getByRole("button", {
        name: failed ? "Test search connection" : "Remove search key",
      })
      .click();
    await expect.poll(() => !!pending()).toBe(true);
    if (failed) {
      await pending()!.fulfill({
        status: 409,
        json: { detail: "Synthetic save failure." },
      });
      await expect(page.getByRole("alert")).toHaveText(
        "Synthetic save failure.",
      );
    } else {
      state.web_search.credentials_present = false;
      state.web_search.config.enabled = false;
      state.web_search.tested_at = null;
      await pending()!.fulfill({ json: state.web_search });
      await expect(page.locator(".ollama-search-setup .badge")).toHaveText(
        "Disabled",
      );
    }
    await expect(
      page.getByRole("button", { name: "Save search settings" }),
    ).toBeEnabled();
    await expect(page.getByLabel("Daily search cap")).toHaveValue("30");
  });
}

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
