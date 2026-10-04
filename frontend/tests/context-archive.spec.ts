import { expect, test, type Page, type Route } from "@playwright/test";
import type {
  ContextCapture,
  ContextSearchResult,
  Message,
  Workspace,
  WebSearchStatus,
} from "../src/api";

const retrieved = "2026-10-02T10:30:00Z";
const publication = "2024-03-12";
const source: ContextCapture = {
  capture_id: "capture-one",
  title: "Public source snapshot",
  url: "https://docs.example.org/guide",
  content:
    "Archived excerpt <script>window.sourceExecuted = true</script>\n<img src=x onerror=alert(1)>",
  content_hash: "a".repeat(64),
  manifest_hash: "c".repeat(64),
  content_kind: "search_excerpt",
  retrieved_at: retrieved,
  published_at: publication,
  modified_at: null,
  completeness: { maestro_truncated: false, full_page: false },
  archive_status: "saved",
};

function savedResult(
  query: string,
  sources: ContextCapture[] = [{ ...source, stale: true }],
): ContextSearchResult {
  return {
    query,
    at: null,
    sources,
    from_cache: true,
    stale: sources.some((item) => item.stale),
    retrieval: "keyword",
  };
}

function workspace(): Workspace {
  return {
    tasks: [],
    chats: [
      {
        id: "archive-chat",
        title: "Source archive conversation",
        created_at: retrieved,
        updated_at: retrieved,
      },
    ],
    active_chat_id: "archive-chat",
    messages: [],
    memories: [],
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
        model: "synthetic-local",
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
      models: ["synthetic-local"],
      tested_at: null,
    },
    web_search: {
      config: { enabled: true, daily_limit: 1, max_results: 3 },
      searches_today: 1,
      remaining_today: 0,
      paused_until: null,
      credentials_present: false,
      credential_source: "missing",
      tested_at: null,
    },
    context_archive: {
      configured: true,
      available: true,
      path: "/archive",
      config: {
        enabled: true,
        public_sources: ["https://docs.example.org/"],
        reuse_hours: 24,
        max_bytes: 256 * 1048576,
        max_items: 50000,
      },
      indexed_count: 2,
      missing_count: 1,
      corrupt_count: 0,
      archive_bytes: 1048576,
      archive_items: 3,
      last_error: null,
      last_indexed_at: retrieved,
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
}

async function openSearchOptions(page: Page) {
  const trigger = page.getByRole("button", { name: /^Search options:/ });
  if ((await trigger.getAttribute("aria-expanded")) !== "true")
    await trigger.click();
  const popup = page.getByRole("dialog", {
    name: "Search options",
    exact: true,
  });
  await expect(popup).toBeVisible();
  return popup;
}

async function fixture(page: Page, state = workspace()) {
  const requests: {
    path: string;
    url: string;
    method: string;
    body: unknown;
    csrf?: string;
  }[] = [];
  const held = new Map<string, Route>();
  const hold = new Set<string>();
  const missing = new Set<string>();
  const searchReplies = new Map<string, { status?: number; json: unknown }>();
  const chatProvenance: Partial<NonNullable<Message["web_search"]>> = {};
  const chatHistory = new Map<string, Message[]>([
    ...(state.active_chat_id
      ? [
          [state.active_chat_id, structuredClone(state.messages)] as [
            string,
            Message[],
          ],
        ]
      : []),
  ]);
  let failSave = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === "/api/session")
      return route.fulfill({ json: { csrf: "synthetic-context-csrf" } });
    if (path === "/api/workspace") return route.fulfill({ json: state });
    requests.push({
      path,
      url: request.url(),
      method,
      body: method === "GET" ? null : request.postDataJSON(),
      csrf: request.headers()["x-maestro-csrf"],
    });
    if (hold.has(`${method} ${path}`)) {
      hold.delete(`${method} ${path}`);
      held.set(`${method} ${path}`, route);
      return;
    }
    if (path === "/api/web-search") {
      if (method === "PUT") {
        state.web_search = {
          ...state.web_search,
          config: request.postDataJSON().config,
          ready: false,
          unavailable_reason: "Enable Optional web search in Settings.",
        };
      }
      return route.fulfill({ json: state.web_search });
    }
    if (path === "/api/context") {
      if (method === "PUT" && failSave) {
        failSave = false;
        return route.fulfill({
          status: 409,
          json: { detail: "The archive settings could not be saved." },
        });
      }
      if (method === "PUT") {
        state.context_archive!.config = request.postDataJSON();
        if (state.context_archive!.config.enabled)
          state.context_archive!.available = true;
      }
      return route.fulfill({ json: state.context_archive });
    }
    if (path === "/api/chats" && method === "POST") {
      if (state.active_chat_id)
        chatHistory.set(state.active_chat_id, structuredClone(state.messages));
      const chat = {
        id: "fresh-chat",
        title: "New chat",
        created_at: retrieved,
        updated_at: retrieved,
      };
      state.chats = [chat, ...state.chats];
      state.active_chat_id = chat.id;
      state.messages = [];
      chatHistory.set(chat.id, []);
      return route.fulfill({ json: state });
    }
    if (path === "/api/context/rebuild") {
      state.context_archive!.indexed_count = 3;
      state.context_archive!.missing_count = 0;
      return route.fulfill({ json: state.context_archive });
    }
    if (path === "/api/context/search") {
      const body = request.postDataJSON();
      const response = searchReplies.get(body.query);
      return route.fulfill(
        response ?? {
          json: savedResult(
            body.query,
            missing.has(source.capture_id) ? [] : [{ ...source, stale: true }],
          ),
        },
      );
    }
    if (path.startsWith("/api/context/sources/")) {
      const id = path.split("/").at(-1)!;
      if (missing.has(id))
        return route.fulfill({
          status: 404,
          json: { detail: "This saved source is unavailable." },
        });
      if (method === "DELETE") {
        missing.add(id);
        state.context_archive!.indexed_count -= 1;
        return route.fulfill({ json: state.context_archive });
      }
      const expectedHash = new URL(request.url()).searchParams.get(
        "expected_hash",
      );
      const expectedManifestHash = new URL(request.url()).searchParams.get(
        "expected_manifest_hash",
      );
      if (
        (expectedHash !== null && expectedHash !== source.content_hash) ||
        (expectedManifestHash !== null &&
          expectedManifestHash !== source.manifest_hash)
      )
        return route.fulfill({
          status: 404,
          json: { detail: "This saved source is unavailable." },
        });
      return route.fulfill({
        json: {
          ...source,
          capture_id: id,
          ...(id === "capture-two"
            ? { title: "Second snapshot", content: "Second archived excerpt" }
            : {}),
        },
      });
    }
    if (path === "/api/chat") {
      const body = request.postDataJSON();
      if (body.context_mode === "refresh")
        return route.fulfill({
          status: 409,
          json: {
            detail:
              "Fresh search is unavailable. Restore search readiness or choose saved evidence.",
          },
        });
      state.messages = [
        { id: "request", role: "user", text: body.text },
        {
          id: "reply",
          role: "assistant",
          text: "Answer from saved sources [1]",
          model: "synthetic-local",
          web_search: {
            query: "public reference",
            at: "2026-10-04T12:00:00Z",
            from_cache: true,
            sources: [source],
            ...chatProvenance,
          },
        },
      ];
      return route.fulfill({ json: state });
    }
    throw new Error(`Unexpected archive API request: ${method} ${path}`);
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Source archive conversation" }),
  ).toBeVisible();
  return {
    state,
    requests,
    hold,
    held,
    missing,
    searchReplies,
    chatProvenance,
    chatHistory,
    failNextSave: () => {
      failSave = true;
    },
  };
}

test("archive settings save directly, preserve later drafts and rebuild boundedly", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  await expect(card.getByText("Archive folder: /archive")).toBeVisible();
  await expect(
    card.getByText(/2 saved source snapshots.*1 missing/),
  ).toBeVisible();
  const sources = card.getByLabel("Approved public source URLs (one per line)");
  await sources.fill("https://docs.example.org/\n");
  await sources.pressSequentially("https://public.example.org/");
  await card.getByLabel("Reuse saved searches for (hours)").fill("48");
  await card.getByLabel("Archive size limit (MiB)").fill("512");
  api.hold.add("PUT /api/context");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect.poll(() => api.held.has("PUT /api/context")).toBe(true);
  await expect(
    card.getByRole("button", { name: "Rebuild source index" }),
  ).toBeDisabled();
  expect(api.requests.at(-1)).toMatchObject({
    method: "PUT",
    csrf: "synthetic-context-csrf",
    body: {
      enabled: true,
      public_sources: [
        "https://docs.example.org/",
        "https://public.example.org/",
      ],
      reuse_hours: 48,
      max_bytes: 512 * 1048576,
      max_items: 50000,
    },
  });
  await card.getByLabel("Reuse saved searches for (hours)").fill("72");
  api.state.context_archive!.config = api.requests.at(-1)!.body as NonNullable<
    Workspace["context_archive"]
  >["config"];
  await api.held
    .get("PUT /api/context")!
    .fulfill({ json: api.state.context_archive });
  await expect(
    card.getByRole("button", { name: "Save source settings" }),
  ).toBeEnabled();
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
  await card.getByRole("button", { name: "Rebuild source index" }).click();
  await expect(
    card.getByText(/3 saved source snapshots.*0 missing/),
  ).toBeVisible();
  expect(api.requests.at(-1)).toMatchObject({
    path: "/api/context/rebuild",
    body: { limit: 1000 },
    csrf: "synthetic-context-csrf",
  });
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
});

test("source index rebuild runs sequential bounded batches with progress and preserves drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const key = "POST /api/context/rebuild";
  const id = "1".repeat(32);
  const requests = () =>
    api.requests.filter((request) => request.path === "/api/context/rebuild");
  api.hold.add(key);
  await card
    .getByRole("button", { name: "Rebuild source index", exact: true })
    .click();
  await expect.poll(() => requests().length).toBe(1);
  const first = api.held.get(key)!;
  api.hold.add(key);
  await first.fulfill({
    json: {
      ...api.state.context_archive,
      rebuild_progress: { id, processed: 1000, total: 2500, complete: false },
    },
  });
  await expect.poll(() => requests().length).toBe(2);
  await expect(
    card.getByText(
      /Rebuilding source index: 1,000 of 2,500 source records checked/,
    ),
  ).toBeVisible();
  await expect(card.getByText(/2 saved source snapshots/)).toBeVisible();
  await card.getByLabel("Reuse saved searches for (hours)").fill("72");
  const second = api.held.get(key)!;
  api.hold.add(key);
  await second.fulfill({
    json: {
      ...api.state.context_archive,
      rebuild_progress: { id, processed: 2000, total: 2500, complete: false },
    },
  });
  await expect.poll(() => requests().length).toBe(3);
  api.state.context_archive = {
    ...api.state.context_archive!,
    indexed_count: 2498,
    rebuild_progress: { id, processed: 2500, total: 2500, complete: true },
  };
  await api.held.get(key)!.fulfill({ json: api.state.context_archive });
  await expect(
    card.getByText(
      /Source index rebuild complete: 2,500 of 2,500 source records checked/,
    ),
  ).toBeVisible();
  await expect(card.getByText(/2,498 saved source snapshots/)).toBeVisible();
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
  expect(requests().map((request) => request.body)).toEqual([
    { limit: 1000 },
    { limit: 1000, continuation: id },
    { limit: 1000, continuation: id },
  ]);
});

test("source index rebuild pauses after the current batch, refreshes invalidation errors and restarts safely", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const key = "POST /api/context/rebuild";
  const id = "2".repeat(32);
  const requests = () =>
    api.requests.filter((request) => request.path === "/api/context/rebuild");
  api.hold.add(key);
  await card
    .getByRole("button", { name: "Rebuild source index", exact: true })
    .click();
  await expect.poll(() => requests().length).toBe(1);
  const first = api.held.get(key)!;
  api.hold.add(key);
  await first.fulfill({
    json: {
      ...api.state.context_archive,
      rebuild_progress: { id, processed: 1000, total: 3000, complete: false },
    },
  });
  await expect.poll(() => requests().length).toBe(2);
  await card.getByRole("button", { name: "Pause after current batch" }).click();
  await expect(
    card.getByRole("button", { name: "Pausing after this batch…" }),
  ).toBeDisabled();
  await card.getByLabel("Reuse saved searches for (hours)").fill("72");
  api.state.context_archive = {
    ...api.state.context_archive!,
    rebuild_progress: { id, processed: 1500, total: 3000, complete: false },
  };
  await api.held.get(key)!.fulfill({ json: api.state.context_archive });
  await expect(
    card.getByText(
      /Source index rebuild paused: 1,500 of 3,000 source records checked/,
    ),
  ).toBeVisible();
  await expect(
    card.getByRole("button", { name: "Continue source index rebuild" }),
  ).toBeEnabled();
  expect(requests()).toHaveLength(2);
  api.hold.add(key);
  await card
    .getByRole("button", { name: "Continue source index rebuild" })
    .click();
  await expect.poll(() => requests().length).toBe(3);
  const resumed = api.held.get(key)!;
  api.hold.add(key);
  await resumed.fulfill({
    json: {
      ...api.state.context_archive,
      rebuild_progress: { id, processed: 2200, total: 3000, complete: false },
    },
  });
  await expect.poll(() => requests().length).toBe(4);
  api.state.context_archive!.rebuild_progress = null;
  await api.held
    .get(key)!
    .fulfill({
      status: 409,
      json: {
        detail: "The archive changed during rebuilding. Restart the rebuild.",
      },
    });
  await expect(card.getByRole("alert")).toHaveText(
    "The archive changed during rebuilding. Restart the rebuild.",
  );
  await expect(
    card.getByRole("button", { name: "Rebuild source index", exact: true }),
  ).toBeEnabled();
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
  expect(requests()).toHaveLength(4);
  api.hold.add(key);
  await card
    .getByRole("button", { name: "Rebuild source index", exact: true })
    .click();
  await expect.poll(() => requests().length).toBe(5);
  api.state.context_archive!.rebuild_progress = {
    id: "3".repeat(32),
    processed: 3001,
    total: 3001,
    complete: true,
  };
  await api.held.get(key)!.fulfill({ json: api.state.context_archive });
  await expect(
    card.getByText(
      /Source index rebuild complete: 3,001 of 3,001 source records checked/,
    ),
  ).toBeVisible();
  await expect(card.getByRole("alert")).toHaveCount(0);
  expect(requests().map((request) => request.body)).toEqual([
    { limit: 1000 },
    { limit: 1000, continuation: id },
    { limit: 1000 },
    { limit: 1000, continuation: id },
    { limit: 1000 },
  ]);
});

for (const invalid of ["stalled", "changed_id", "over_limit"] as const) {
  test(`source index rebuild stops on ${invalid} progress instead of dispatching another batch`, async ({
    page,
  }) => {
    const api = await fixture(page);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const card = page.locator(".context-archive-setup");
    const key = "POST /api/context/rebuild";
    const id = "4".repeat(32);
    const requests = () =>
      api.requests.filter((request) => request.path === "/api/context/rebuild");
    api.hold.add(key);
    await card
      .getByRole("button", { name: "Rebuild source index", exact: true })
      .click();
    await expect.poll(() => requests().length).toBe(1);
    const first = api.held.get(key)!;
    api.hold.add(key);
    api.state.context_archive!.rebuild_progress = {
      id,
      processed: 1000,
      total: 3000,
      complete: false,
    };
    await first.fulfill({ json: api.state.context_archive });
    await expect.poll(() => requests().length).toBe(2);
    const invalidProgress =
      invalid === "stalled"
        ? { id, processed: 1000, total: 3000, complete: false }
        : invalid === "changed_id"
          ? {
              id: "5".repeat(32),
              processed: 2000,
              total: 3000,
              complete: false,
            }
          : { id, processed: 2000, total: 200001, complete: false };
    await api.held
      .get(key)!
      .fulfill({
        json: {
          ...api.state.context_archive,
          rebuild_progress: invalidProgress,
        },
      });
    await expect(card.getByRole("alert")).toHaveText(
      "Source index rebuild did not make valid progress. Refresh the archive status and retry.",
    );
    expect(requests()).toHaveLength(2);
    await expect(
      card.getByRole("button", { name: "Continue source index rebuild" }),
    ).toBeEnabled();
  });
}

test("leaving archive settings pauses rebuild continuation and retains settings drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const key = "POST /api/context/rebuild";
  api.hold.add(key);
  await card
    .getByRole("button", { name: "Rebuild source index", exact: true })
    .click();
  await expect.poll(() => api.held.has(key)).toBe(true);
  await card.getByLabel("Reuse saved searches for (hours)").fill("72");
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  api.state.context_archive!.rebuild_progress = {
    id: "6".repeat(32),
    processed: 1000,
    total: 2000,
    complete: false,
  };
  await api.held.get(key)!.fulfill({ json: api.state.context_archive });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "Continue source index rebuild" }),
  ).toBeEnabled();
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
  expect(
    api.requests.filter((request) => request.path === "/api/context/rebuild"),
  ).toHaveLength(1);
});

test("valid byte archive caps remain editable without a whole MiB restriction", async ({
  page,
}) => {
  const state = workspace();
  state.context_archive!.config.max_bytes = 1048577;
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  await card.getByLabel("Reuse saved searches for (hours)").fill("48");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  expect(api.requests.at(-1)!.body).toMatchObject({
    reuse_hours: 48,
    max_bytes: 1048577,
  });
  await card.getByLabel("Archive size limit (MiB)").fill("1.234567");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  expect(api.requests.at(-1)!.body).toMatchObject({
    max_bytes: Math.round(1.234567 * 1048576),
  });
});

test("unavailable archive can be disabled without an editable deployment path", async ({
  page,
}) => {
  const state = workspace();
  Object.assign(state.context_archive!, {
    configured: false,
    available: false,
    path: null,
  });
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  await expect(card.getByText("Not configured", { exact: true })).toBeVisible();
  await expect(
    card.getByRole("button", { name: "Rebuild source index" }),
  ).toBeDisabled();
  await card.getByLabel("Save and reuse public search results").uncheck();
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  expect(api.requests.at(-1)!.body).toMatchObject({ enabled: false });
  await expect(
    card.getByLabel("Save and reuse public search results"),
  ).toBeDisabled();
});

test("a configured new archive can be enabled before its directory is initialized", async ({
  page,
}) => {
  const state = workspace();
  state.context_archive!.available = false;
  state.context_archive!.config.enabled = false;
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const enabled = card.getByLabel("Save and reuse public search results");
  await expect(enabled).toBeEnabled();
  await enabled.check();
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.locator(".badge")).toHaveText("Enabled");
  expect(api.requests.at(-1)!.body).toMatchObject({ enabled: true });
});

test("failed archive save refreshes status while retaining the source settings draft", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  await card.getByLabel("Reuse saved searches for (hours)").fill("72");
  api.failNextSave();
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByRole("alert")).toHaveText(
    "The archive settings could not be saved.",
  );
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "72",
  );
  await expect(
    card.getByRole("button", { name: "Save source settings" }),
  ).toBeEnabled();
  expect(api.requests.slice(-2).map((request) => request.method)).toEqual([
    "PUT",
    "GET",
  ]);
});

test("a new public-result archive remains disabled until enabled and needs no approved URL list", async ({
  page,
}) => {
  const state = workspace();
  Object.assign(state.context_archive!.config, {
    enabled: false,
    capture_policy: "all_public",
    public_sources: [],
    max_bytes: 10 * 1073741824,
    max_items: 200000,
  });
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const enabled = card.getByLabel("Save and reuse public search results");
  await expect(enabled).not.toBeChecked();
  await expect(
    card.getByRole("combobox", { name: "Source saving policy" }),
  ).toHaveValue("all_public");
  await expect(
    card.getByLabel("Approved public source URLs (one per line)"),
  ).toHaveCount(0);
  await expect(
    card.getByLabel("Maximum archive files/directories"),
  ).toHaveValue("200000");
  await expect(card.getByLabel("Archive size limit (MiB)")).toHaveValue(
    "10240",
  );
  expect(api.requests).toHaveLength(0);
  await enabled.check();
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  expect(api.requests.at(-1)!.body).toMatchObject({
    enabled: true,
    capture_policy: "all_public",
    public_sources: [],
    max_bytes: 10 * 1073741824,
    max_items: 200000,
  });
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  const popup = await openSearchOptions(page);
  const saving = popup.getByRole("status", { name: "Source saving status" });
  await expect(saving).toContainText("Saving eligible public search results.");
  await expect(saving).toContainText(
    "When enabled, eligible public results are saved automatically as dated search excerpts.",
  );
  await expect(saving).not.toContainText("needs approved public sources");
  await expect(saving).not.toContainText("approved public URL");
});

test("saving policy drafts and hidden approved URLs survive a delayed save", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const policy = card.getByRole("combobox", { name: "Source saving policy" });
  const urls = card.getByLabel("Approved public source URLs (one per line)");
  await expect(policy).toHaveValue("approved_sources");
  await urls.fill("https://weather.example.org/forecast?lat=56.6&lon=16.3");
  await policy.selectOption("all_public");
  await expect(urls).toHaveCount(0);
  expect(api.requests).toHaveLength(0);
  api.hold.add("PUT /api/context");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect.poll(() => api.held.has("PUT /api/context")).toBe(true);
  const sent = structuredClone(api.requests.at(-1)!.body) as NonNullable<
    Workspace["context_archive"]
  >["config"];
  expect(sent).toMatchObject({
    capture_policy: "all_public",
    public_sources: ["https://weather.example.org/forecast?lat=56.6&lon=16.3"],
  });
  await policy.selectOption("approved_sources");
  await expect(urls).toHaveValue(
    "https://weather.example.org/forecast?lat=56.6&lon=16.3",
  );
  await urls.fill("https://docs.example.org/later");
  api.state.context_archive!.config = sent;
  await api.held
    .get("PUT /api/context")!
    .fulfill({ json: api.state.context_archive });
  await expect(
    card.getByRole("button", { name: "Save source settings" }),
  ).toBeEnabled();
  await expect(policy).toHaveValue("approved_sources");
  await expect(urls).toHaveValue("https://docs.example.org/later");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  expect(api.requests.at(-1)!.body).toMatchObject({
    capture_policy: "approved_sources",
    public_sources: ["https://docs.example.org/later"],
  });
});

test("settings and compact menu report actual archive usage and last retrieval sizes from backend status", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const state = workspace();
  state.messages = [
    {
      id: "unverified-claim",
      role: "assistant",
      text: "I saved all three results.",
    },
  ];
  Object.assign(state.context_archive!.config, {
    capture_policy: "all_public",
    public_sources: [],
  });
  Object.assign(state.context_archive!, {
    archive_bytes: 8192,
    archive_items: 12,
    last_capture: {
      retrieved_at: retrieved,
      sources_received: 3,
      sources_saved: 2,
      excerpt_bytes: 3840,
      object_bytes: 640,
      manifest_bytes: 800,
      new_bytes: 1440,
    },
  });
  await fixture(page, state);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const accounting = card.locator(".archive-accounting");
  await expect(accounting).toContainText(
    "2 saved source snapshots · 1 missing · 0 corrupt",
  );
  await expect(accounting).toContainText(
    "Archive storage: 8.0 KiB (8,192 bytes) of 256.0 MiB · 12 of 50,000 files/directories",
  );
  await expect(accounting).toContainText(
    "2 of 3 results saved · 3.8 KiB excerpt text · 1.4 KiB new source files",
  );
  await expect(accounting).toContainText(
    "New text objects: 640 B · New snapshot manifests: 800 B",
  );
  await expect(accounting).toContainText(
    "excludes archive control files and the private index",
  );
  await expect(card).toContainText("independently of the model's reply");
  await expect(card).toContainText(
    "Each fresh retrieval has its own dated snapshot",
  );
  await expect(card).toContainText("excerpts, not full pages");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  const popup = await openSearchOptions(page);
  await expect(popup.locator(".archive-accounting")).toContainText(
    "2 of 3 results saved",
  );
  await expect(popup.locator(".archive-accounting")).not.toContainText(
    "3 of 3 results saved",
  );
  const bounds = await popup.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(375);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(812);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

for (const mode of ["prefer_saved", "refresh", "saved_only"] as const) {
  test(`chat sends ${mode} independently of missing hosted credentials or quota`, async ({
    page,
  }) => {
    const api = await fixture(page);
    await openSearchOptions(page);
    const picker = page.getByRole("combobox", { name: "Source mode" });
    await expect(picker).toHaveValue("prefer_saved");
    await picker.selectOption(mode);
    await page
      .getByRole("textbox", { name: "Message Maestro" })
      .fill("Use the public reference");
    await page
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    expect(
      api.requests.find((request) => request.path === "/api/chat")!.body,
    ).toMatchObject({ context_mode: mode, role: "chat" });
    if (mode === "refresh") {
      await expect(page.getByRole("alert")).toHaveText(
        "Fresh search is unavailable. Restore search readiness or choose saved evidence.",
      );
      await expect(
        page.getByRole("textbox", { name: "Message Maestro" }),
      ).toHaveValue("Use the public reference");
      await expect(
        page.getByText("Reused saved evidence; no new web search."),
      ).toHaveCount(0);
    } else {
      await expect(
        page.getByText("Reused saved evidence; no new web search."),
      ).toBeVisible();
      await expect(
        page.getByText("Saved sources for: public reference"),
      ).toBeVisible();
      await expect(
        page.getByText(`Published ${publication}`, { exact: true }),
      ).toBeVisible();
    }
    if (mode === "saved_only")
      await expect(page.getByText(/Queries go/)).toHaveCount(0);
    expect(
      api.requests.some((request) => request.path.includes("web-search")),
    ).toBe(false);
  });
}

const readinessCases: {
  name: string;
  status: Partial<WebSearchStatus>;
  expected: RegExp;
  noArchive?: boolean;
}[] = [
  {
    name: "missing key",
    status: { credentials_present: false, tested_at: null },
    expected: /Add and test an Ollama search API key/,
    noArchive: true,
  },
  {
    name: "disabled search",
    status: { config: { enabled: false, daily_limit: 20, max_results: 3 } },
    expected: /Enable Optional web search/,
  },
  {
    name: "untested key",
    status: { tested_at: null },
    expected: /Test the search key/,
  },
  {
    name: "exhausted allowance",
    status: { remaining_today: 0 },
    expected: /daily online search allowance is used up/,
  },
  {
    name: "cooldown",
    status: { paused_until: "2050-10-04T12:00:00Z" },
    expected: /paused after a rate limit/,
  },
  {
    name: "server unavailable reason",
    status: {
      ready: false,
      unavailable_reason:
        "A web search is already running. Wait for it to finish.",
    },
    expected: /A web search is already running/,
  },
  {
    name: "ready search",
    status: { ready: true, unavailable_reason: null },
    expected: /Online search is ready\./,
  },
];

test("compact search options preserve drafts and mode through keyboard and outside dismissal", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const api = await fixture(page);
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  const trigger = page.getByRole("button", { name: /^Search options:/ });
  const popup = page.getByRole("dialog", {
    name: "Search options",
    exact: true,
  });
  await composer.fill("Retain my question");
  await expect(popup).not.toBeVisible();
  await expect(page.getByRole("combobox", { name: "Source mode" })).toHaveCount(
    0,
  );
  await expect(trigger).toHaveText("");
  await expect(page.locator(".composer-area > .composer-note")).toHaveCount(1);
  const triggerBounds = await trigger.boundingBox();
  const modelBounds = await page
    .getByRole("button", { name: /^Choose model:/ })
    .boundingBox();
  expect(triggerBounds!.x + triggerBounds!.width).toBeLessThanOrEqual(
    modelBounds!.x,
  );
  await trigger.focus();
  await trigger.press("Enter");
  await expect(popup).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(
    popup.getByRole("button", { name: "Close search options" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  const picker = popup.getByRole("combobox", { name: "Source mode" });
  await expect(picker).toBeFocused();
  await picker.selectOption("saved_only");
  await page.keyboard.press("Escape");
  await expect(popup).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).toHaveAccessibleName(/Saved only; online search off/);
  await openSearchOptions(page);
  const bounds = await popup.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(375);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(812);
  await page.locator(".composer-area > .composer-note").click();
  await expect(popup).not.toBeVisible();
  await composer.click();
  await expect(composer).toBeFocused();
  await expect(composer).toHaveValue("Retain my question");
  await openSearchOptions(page);
  await expect(picker).toHaveValue("saved_only");
  await popup.getByRole("button", { name: "Close search options" }).click();
  await expect(trigger).toBeFocused();
  await expect(popup).not.toBeVisible();
  expect(api.requests).toHaveLength(0);
});

for (const saving of [
  "not configured",
  "off",
  "needs approved public sources",
  "unavailable",
  "enabled",
] as const) {
  test(`search options explain source saving ${saving} independently of online search`, async ({
    page,
  }) => {
    const state = workspace();
    Object.assign(state.web_search, { ready: true, unavailable_reason: null });
    if (saving === "not configured") delete state.context_archive;
    else if (saving === "off") state.context_archive!.config.enabled = false;
    else if (saving === "needs approved public sources")
      state.context_archive!.config.public_sources = [];
    else if (saving === "unavailable") {
      state.context_archive!.available = false;
      state.context_archive!.last_error =
        "The archive folder cannot be written.";
    }
    const api = await fixture(page, state);
    const popup = await openSearchOptions(page);
    await expect(
      popup.getByRole("status", { name: "Online search readiness" }),
    ).toContainText("Online search is ready.");
    const status = popup.getByRole("status", { name: "Source saving status" });
    await expect(status).toContainText(
      saving === "enabled"
        ? "Saving approved public sources."
        : `Source saving ${saving === "needs approved public sources" ? "needs approved public sources" : `is ${saving}`}.`,
    );
    await expect(status).toContainText(
      "Search can work without saving. Only results from approved public URLs are archived.",
    );
    if (saving === "unavailable")
      await expect(status).toContainText(
        "The archive folder cannot be written.",
      );
    if (saving === "enabled") {
      await expect(status).toContainText("2 saved source snapshots");
      await expect(status).toContainText("1 approved public URL");
      await expect(status).toContainText("3 of 50,000 files/directories");
    }
    await popup
      .getByRole("button", { name: "Manage saved sources", exact: true })
      .click();
    await expect(popup).not.toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Settings", exact: true }),
    ).toBeVisible();
    if (state.context_archive) {
      await expect(
        page.getByRole("heading", { name: "Saved web sources", exact: true }),
      ).toBeInViewport();
      await expect(
        page.getByLabel("Approved public source URLs (one per line)"),
      ).toHaveValue(state.context_archive.config.public_sources.join("\n"));
    }
    expect(api.requests.some((request) => request.method !== "GET")).toBe(
      false,
    );
  });
}

for (const scenario of readinessCases) {
  test(`composer search readiness explains ${scenario.name} without changing a draft`, async ({
    page,
  }) => {
    const state = workspace();
    Object.assign(
      state.web_search,
      {
        config: { enabled: true, daily_limit: 20, max_results: 3 },
        credentials_present: true,
        tested_at: retrieved,
        remaining_today: 19,
      },
      scenario.status,
    );
    if (scenario.noArchive) delete state.context_archive;
    state.messages = [
      {
        id: "existing-answer",
        role: "assistant",
        text: "Earlier local answer.",
      },
    ];
    const api = await fixture(page, state);
    const composer = page.getByRole("textbox", { name: "Message Maestro" });
    await composer.fill("Keep this question draft");
    await openSearchOptions(page);
    const notice = page.getByRole("status", {
      name: "Online search readiness",
      exact: true,
    });
    await expect(notice).toContainText(scenario.expected);
    await expect(
      notice.getByRole("button", { name: "Open Settings", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/Maestro is searching|Maestro searching/),
    ).toHaveCount(0);
    await notice
      .getByRole("button", { name: "Open Settings", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Settings", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Workspace", exact: true }).click();
    await expect(composer).toHaveValue("Keep this question draft");
    await expect(
      page.getByText("Earlier local answer.", { exact: true }),
    ).toBeVisible();
    expect(api.requests).toHaveLength(0);
    if (state.context_archive) {
      await openSearchOptions(page);
      await expect(
        page.getByRole("combobox", { name: "Source mode" }),
      ).toHaveValue("prefer_saved");
    }
  });
}

test("composer refreshes temporary search readiness after a reply without changing the next draft", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.install({ time: new Date("2026-10-04T11:59:00Z") });
  await page.clock.pauseAt(new Date("2026-10-04T12:00:00Z"));
  const state = workspace();
  Object.assign(state.web_search, {
    config: { enabled: true, daily_limit: 20, max_results: 3 },
    credentials_present: true,
    tested_at: retrieved,
    remaining_today: 19,
    ready: true,
    unavailable_reason: null,
  });
  const api = await fixture(page, state);
  const notice = page.getByRole("status", {
    name: "Online search readiness",
    exact: true,
  });
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  await openSearchOptions(page);
  await expect(notice).toContainText("Online search is ready.");
  await composer.fill("Use search for public references");
  Object.assign(api.state.web_search, {
    ready: false,
    unavailable_reason:
      "Search requests are spaced at least five seconds apart. Wait before trying again.",
  });
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await openSearchOptions(page);
  await expect(notice).toContainText("five seconds apart");
  await expect(
    page.getByText("Answer from saved sources [1]", { exact: true }),
  ).toBeVisible();
  await composer.fill("Keep my next question draft");
  await openSearchOptions(page);
  await page
    .getByRole("combobox", { name: "Source mode" })
    .selectOption("refresh");
  await page.clock.runFor(4000);
  expect(
    api.requests.filter((entry) => entry.path === "/api/web-search"),
  ).toHaveLength(0);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(10000);
  expect(
    api.requests.filter((entry) => entry.path === "/api/web-search"),
  ).toHaveLength(0);
  Object.assign(api.state.web_search, {
    ready: true,
    unavailable_reason: null,
  });
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(1);
  await expect(notice).toContainText("Online search is ready.");
  await expect(composer).toHaveValue("Keep my next question draft");
  await expect(page.getByRole("combobox", { name: "Source mode" })).toHaveValue(
    "refresh",
  );
  await expect(
    page.getByText("Answer from saved sources [1]", { exact: true }),
  ).toBeVisible();
  await page.clock.runFor(60000);
  expect(
    api.requests.filter((entry) => entry.path === "/api/web-search"),
  ).toHaveLength(1);
});

test("a delayed readiness refresh cannot restore search disabled in Settings", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-10-04T11:59:00Z") });
  await page.clock.pauseAt(new Date("2026-10-04T12:00:00Z"));
  const state = workspace();
  Object.assign(state.web_search, {
    config: { enabled: true, daily_limit: 20, max_results: 3 },
    credentials_present: true,
    tested_at: retrieved,
    remaining_today: 19,
    ready: false,
    unavailable_reason:
      "Search requests are spaced at least five seconds apart. Wait before trying again.",
  });
  state.messages = [
    { id: "earlier", role: "assistant", text: "Earlier local answer." },
  ];
  const api = await fixture(page, state);
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  await composer.fill("Retain this unsent question");
  const oldReady = {
    ...api.state.web_search,
    ready: true,
    unavailable_reason: null,
  };
  api.hold.add("GET /api/web-search");
  await page.clock.runFor(5000);
  await expect.poll(() => api.held.has("GET /api/web-search")).toBe(true);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const setup = page.locator(".ollama-search-setup");
  await setup.getByLabel("Let Maestro decide when to search").uncheck();
  await setup
    .getByRole("button", { name: "Save search settings", exact: true })
    .click();
  await expect(
    setup.getByText("Search settings saved.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await openSearchOptions(page);
  const notice = page.getByRole("status", {
    name: "Online search readiness",
    exact: true,
  });
  await expect(notice).toContainText("Enable Optional web search in Settings.");
  await api.held.get("GET /api/web-search")!.fulfill({ json: oldReady });
  await page.clock.runFor(60000);
  await expect(notice).toContainText("Enable Optional web search in Settings.");
  await expect(composer).toHaveValue("Retain this unsent question");
  await expect(
    page.getByText("Earlier local answer.", { exact: true }),
  ).toBeVisible();
  expect(
    api.requests.filter(
      (entry) => entry.path === "/api/web-search" && entry.method === "GET",
    ),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    setup.getByLabel("Let Maestro decide when to search"),
  ).not.toBeChecked();
});

test("composer search readiness separates saved-only mode from ready online setup", async ({
  page,
}) => {
  const state = workspace();
  Object.assign(state.web_search, { ready: true, unavailable_reason: null });
  const api = await fixture(page, state);
  await openSearchOptions(page);
  await page
    .getByRole("combobox", { name: "Source mode" })
    .selectOption("saved_only");
  const notice = page.getByRole("status", {
    name: "Online search readiness",
    exact: true,
  });
  await expect(notice).toContainText("Saved only keeps online search off.");
  await expect(notice).toContainText("The online search setup is ready");
  await expect(page.getByText(/Automatic web search|Queries go/)).toHaveCount(
    0,
  );
  expect(api.requests).toHaveLength(0);
});

test("composer search readiness preserves history and later draft edits when starting an online chat", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const state = workspace();
  Object.assign(state.web_search, { ready: true, unavailable_reason: null });
  state.messages = [
    {
      id: "memory-answer",
      role: "assistant",
      text: "Answer informed by saved memory.",
      memory_ids: ["m1", "m2", "m3", "m4"],
    },
  ];
  const originalHistory = structuredClone(state.messages);
  const api = await fixture(page, state);
  await openSearchOptions(page);
  const notice = page.getByRole("status", {
    name: "Online search readiness",
    exact: true,
  });
  await expect(notice).toContainText("Online search is blocked in this chat.");
  await expect(notice).toContainText("Earlier replies used private memory.");
  await expect(notice).toContainText(
    "Saved public sources remain available locally.",
  );
  await expect(page.getByText(/Automatic web search/)).toHaveCount(0);
  await page
    .getByRole("combobox", { name: "Source mode" })
    .selectOption("refresh");
  await expect(notice).toContainText("Choose Prefer saved or Saved only");
  await expect(
    page.getByText(/Fresh search requires ready online search/),
  ).toBeVisible();
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  await composer.fill("Weather today");
  api.hold.add("POST /api/chats");
  await openSearchOptions(page);
  await notice
    .getByRole("button", { name: "New chat for online search", exact: true })
    .click();
  await expect.poll(() => api.held.has("POST /api/chats")).toBe(true);
  await composer.fill("Weather tomorrow, with sources");
  const fresh = {
    id: "fresh-chat",
    title: "New chat",
    created_at: retrieved,
    updated_at: retrieved,
  };
  Object.assign(api.state, {
    chats: [fresh, ...api.state.chats],
    active_chat_id: fresh.id,
    messages: [],
  });
  await api.held.get("POST /api/chats")!.fulfill({ json: api.state });
  await expect(
    page.getByRole("heading", { name: "New chat", exact: true }),
  ).toBeVisible();
  await expect(composer).toHaveValue("Weather tomorrow, with sources");
  await openSearchOptions(page);
  await expect(page.getByRole("combobox", { name: "Source mode" })).toHaveValue(
    "refresh",
  );
  await expect(notice).toContainText("Online search is ready.");
  await expect(
    notice.getByRole("button", {
      name: "New chat for online search",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(api.chatHistory.get("archive-chat")).toEqual(originalHistory);
  expect(api.state.chats.some((chat) => chat.id === "archive-chat")).toBe(true);
  expect(
    api.requests.map((request) => `${request.method} ${request.path}`),
  ).toEqual(["POST /api/chats"]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("composer search readiness stays hidden for a paid model provider", async ({
  page,
}) => {
  const state = workspace();
  state.provider.config.protocol = "responses";
  state.provider.credentials_required = true;
  state.provider.credentials_present = true;
  state.messages = [
    {
      id: "older-memory-answer",
      role: "assistant",
      text: "Old local answer",
      memory_ids: ["m1"],
    },
  ];
  await fixture(page, state);
  await expect(
    page.getByRole("status", { name: "Online search readiness", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", {
      name: "New chat for online search",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Source mode" })).toHaveCount(
    0,
  );
});

test("chat discloses a partial saved lookup for keyword evidence and fresh fallback", async ({
  page,
}) => {
  const api = await fixture(page);
  Object.assign(api.chatProvenance, {
    retrieval: "keyword",
    at: null,
    search_limited: true,
    filters: { domain: "docs.example.org", retrieved_from: "2026-10-01" },
  });
  const composer = page.getByRole("textbox", { name: "Message Maestro" });
  await composer.fill("Use partial saved evidence");
  await composer.press("Control+Enter");
  const banner = page.getByText(
    "Saved lookup reached its work limit; narrow keywords or saved-source filters for a more complete lookup.",
    { exact: true },
  );
  await expect(banner).toBeVisible();
  await expect(
    page.getByText("Reused saved evidence; no new web search.", {
      exact: true,
    }),
  ).toBeVisible();
  Object.assign(api.chatProvenance, {
    retrieval: "web_search",
    from_cache: false,
  });
  await composer.fill("Use fresh fallback evidence");
  await composer.press("Control+Enter");
  await expect(
    page.getByText("Search query: public reference", { exact: true }),
  ).toBeVisible();
  await expect(banner).toBeVisible();
  await expect(
    page.getByText("Reused saved evidence; no new web search.", {
      exact: true,
    }),
  ).toHaveCount(0);
});

test("saved citations expose original dates and text-only snapshots, with explicit removal", async ({
  page,
}) => {
  const state = workspace();
  state.messages = [
    {
      id: "answer",
      role: "assistant",
      text: "Sourced answer [1]",
      web_search: {
        query: "public reference",
        at: "2026-10-04T12:00:00Z",
        from_cache: true,
        sources: [source],
      },
    },
  ];
  const api = await fixture(page, state);
  await expect(
    page.getByText(`Published ${publication}`, { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View saved source 1" }).click();
  const viewer = page.getByRole("dialog", {
    name: "Saved source",
    exact: true,
  });
  await expect(viewer.locator(".source-snapshot")).toHaveText(source.content);
  await expect(viewer.locator("script, img")).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        (window as typeof window & { sourceExecuted?: boolean }).sourceExecuted,
    ),
  ).toBeUndefined();
  await expect(
    viewer.getByText(`Published ${publication}`, { exact: false }),
  ).toBeVisible();
  const dateText = await viewer
    .getByText(/Search excerpt · Retrieved/)
    .textContent();
  expect(dateText).toContain("2026");
  expect(dateText).not.toContain("2026-10-04T12:00:00Z");
  await viewer
    .getByRole("button", { name: "Remove saved source", exact: true })
    .click();
  expect(api.requests.some((request) => request.method === "DELETE")).toBe(
    false,
  );
  await viewer.getByRole("button", { name: "Confirm removal" }).click();
  await expect(viewer.getByText(/Saved source removed/)).toBeVisible();
  expect(api.requests.at(-1)).toMatchObject({
    path: "/api/context/sources/capture-one",
    method: "DELETE",
    csrf: "synthetic-context-csrf",
  });
  await viewer.getByRole("button", { name: "Close dialog" }).click();
  await expect(
    page.getByText("Sourced answer [1]", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View saved source 1" }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "This saved source is unavailable.",
  );
});

test("unsaved evidence keeps live provenance and late snapshot reads cannot replace another source", async ({
  page,
}) => {
  const state = workspace();
  state.messages = [
    {
      id: "answer",
      role: "assistant",
      text: "Sourced answer",
      web_search: {
        query: "public reference",
        at: "2026-10-04T12:00:00Z",
        archive_warning: "The archive could not save one source.",
        sources: [
          source,
          { ...source, capture_id: "capture-two", title: "Second source" },
          {
            title: "Transient source",
            url: "https://public.example.org/",
            archive_status: "not_saved",
            retrieved_at: retrieved,
            content_kind: "search_excerpt",
            published_at: null,
          },
        ],
      },
    },
  ];
  const api = await fixture(page, state);
  await expect(
    page.getByText("Source was not saved", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Transient source" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View saved source 3" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("The archive could not save one source."),
  ).toBeVisible();
  api.hold.add("GET /api/context/sources/capture-one");
  await page.getByRole("button", { name: "View saved source 1" }).click();
  await expect
    .poll(() => api.held.has("GET /api/context/sources/capture-one"))
    .toBe(true);
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "View saved source 2" }).click();
  await expect(
    page.getByRole("dialog").getByText("Second archived excerpt"),
  ).toBeVisible();
  await api.held
    .get("GET /api/context/sources/capture-one")!
    .fulfill({ json: source });
  await expect(page.getByRole("dialog").locator(".source-snapshot")).toHaveText(
    "Second archived excerpt",
  );
});

for (const change of ["scope", "policy", "enabled"] as const) {
  for (const response of ["loaded", "pending"] as const) {
    test(`saved source viewer revalidates ${response} text after persisted ${change} changes`, async ({
      page,
    }) => {
      const state = workspace();
      if (change === "policy")
        Object.assign(state.context_archive!.config, {
          capture_policy: "all_public",
          public_sources: [],
        });
      const api = await fixture(page, state);
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const card = page.locator(".context-archive-setup");
      const keywords = card.getByLabel("Keywords", { exact: true });
      await keywords.fill("keep the saved search draft");
      await keywords.press("Enter");
      if (change === "policy")
        await card
          .getByRole("combobox", { name: "Source saving policy" })
          .selectOption("approved_sources");
      if (change === "enabled")
        await card.getByLabel("Save and reuse public search results").uncheck();
      else
        await card
          .getByLabel("Approved public source URLs (one per line)")
          .fill("https://other.example.org/");
      api.hold.add("PUT /api/context");
      await card.getByRole("button", { name: "Save source settings" }).click();
      await expect.poll(() => api.held.has("PUT /api/context")).toBe(true);
      const capturePath = `/api/context/sources/${source.capture_id}`;
      if (response === "pending") api.hold.add(`GET ${capturePath}`);
      await card.getByRole("button", { name: "View saved result 1" }).click();
      const viewer = page.getByRole("dialog", {
        name: "Saved source",
        exact: true,
      });
      if (response === "pending")
        await expect.poll(() => api.held.has(`GET ${capturePath}`)).toBe(true);
      else
        await expect(viewer.locator(".source-snapshot")).toHaveText(
          source.content,
        );
      const save = api.held.get("PUT /api/context")!;
      state.context_archive!.config = save.request().postDataJSON();
      api.missing.add(source.capture_id);
      await save.fulfill({ json: state.context_archive });
      await expect(viewer.getByRole("alert")).toHaveText(
        "This saved source is unavailable.",
      );
      await expect(viewer.locator(".source-snapshot")).toHaveCount(0);
      if (response === "pending") {
        await api.held.get(`GET ${capturePath}`)!.fulfill({ json: source });
        await expect(viewer.getByRole("alert")).toHaveText(
          "This saved source is unavailable.",
        );
        await expect(viewer.locator(".source-snapshot")).toHaveCount(0);
      }
      const reads = api.requests.filter(
        (request) => request.path === capturePath && request.method === "GET",
      );
      expect(reads).toHaveLength(2);
      for (const read of reads) {
        const url = new URL(read.url);
        expect(url.searchParams.get("expected_hash")).toBe(source.content_hash);
        expect(url.searchParams.get("expected_manifest_hash")).toBe(
          source.manifest_hash,
        );
      }
      await viewer.getByRole("button", { name: "Close dialog" }).click();
      await expect(keywords).toHaveValue("keep the saved search draft");
    });
  }
}

test("a saved source viewer stays loaded through non-eligibility saves and later settings drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  await card.getByLabel("Keywords", { exact: true }).fill("preserve evidence");
  await card.getByLabel("Keywords", { exact: true }).press("Enter");
  await card.getByLabel("Reuse saved searches for (hours)").fill("48");
  api.hold.add("PUT /api/context");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect.poll(() => api.held.has("PUT /api/context")).toBe(true);
  await card
    .getByLabel("Approved public source URLs (one per line)")
    .fill("https://unsaved.example.org/");
  await card.getByRole("button", { name: "View saved result 1" }).click();
  const viewer = page.getByRole("dialog", {
    name: "Saved source",
    exact: true,
  });
  await expect(viewer.locator(".source-snapshot")).toHaveText(source.content);
  const save = api.held.get("PUT /api/context")!;
  api.state.context_archive!.config = save.request().postDataJSON();
  api.state.context_archive!.indexed_count += 1;
  await save.fulfill({ json: api.state.context_archive });
  await expect(viewer.locator(".source-snapshot")).toHaveText(source.content);
  expect(
    api.requests.filter((request) =>
      request.path.includes("/context/sources/"),
    ),
  ).toHaveLength(1);
  await viewer.getByRole("button", { name: "Close dialog" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  await expect(
    card.getByLabel("Approved public source URLs (one per line)"),
  ).toHaveValue("https://unsaved.example.org/");
  await expect(card.getByLabel("Reuse saved searches for (hours)")).toHaveValue(
    "48",
  );
});

test("older saved citations keep their original retrieval date and request both cited hashes", async ({
  page,
}) => {
  const state = workspace();
  state.messages = [
    {
      id: "older-answer",
      role: "assistant",
      text: "Answer from older saved evidence",
      web_search: {
        query: "older public reference",
        at: "2026-11-04T12:00:00Z",
        from_cache: true,
        stale: true,
        sources: [{ ...source, retrieved_at: "2026-10-02", stale: true }],
      },
    },
  ];
  const api = await fixture(page, state);
  await expect(
    page.getByText("Older saved evidence", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Search excerpt · Older saved evidence · Retrieved 2026-10-02",
      { exact: true },
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "View saved source 1" }).click();
  await expect(
    page.getByRole("dialog").getByLabel("Saved source text"),
  ).toHaveText(source.content);
  const request = api.requests.find(
    (entry) =>
      entry.path === "/api/context/sources/capture-one" &&
      entry.method === "GET",
  )!;
  expect(new URL(request.url).searchParams.get("expected_hash")).toBe(
    source.content_hash,
  );
  expect(new URL(request.url).searchParams.get("expected_manifest_hash")).toBe(
    source.manifest_hash,
  );
});

for (const digest of ["content_hash", "manifest_hash"] as const) {
  test(`a changed saved capture ${digest} shows an unavailable error instead of replacement evidence`, async ({
    page,
  }) => {
    const state = workspace();
    state.messages = [
      {
        id: "historical-answer",
        role: "assistant",
        text: "Historical answer",
        web_search: {
          query: "public reference",
          at: retrieved,
          from_cache: true,
          sources: [{ ...source, [digest]: "b".repeat(64) }],
        },
      },
    ];
    const api = await fixture(page, state);
    await page.getByRole("button", { name: "View saved source 1" }).click();
    const viewer = page.getByRole("dialog", {
      name: "Saved source",
      exact: true,
    });
    await expect(viewer.getByRole("alert")).toHaveText(
      "This saved source is unavailable.",
    );
    await expect(viewer.getByLabel("Saved source text")).toHaveCount(0);
    expect(
      new URL(api.requests.at(-1)!.url).searchParams.get(
        `expected_${digest === "content_hash" ? "hash" : digest}`,
      ),
    ).toBe("b".repeat(64));
    await expect(
      page.getByText("Historical answer", { exact: true }),
    ).toBeVisible();
  });
}

test("finder sends keyword and retrieval filters locally with bounded results and a dated viewer", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  await expect(
    finder.getByText(/no online search or AI request/),
  ).toBeVisible();
  await expect(
    finder.getByText(/Date filters use when Maestro retrieved/),
  ).toBeVisible();
  await expect(finder.getByLabel("Include older saved evidence")).toBeChecked();
  await finder
    .getByLabel("Keywords", { exact: true })
    .fill("  archived text  ");
  await finder.getByLabel("Domain (optional)").fill("docs.example.org");
  await finder.getByLabel("Retrieved from").fill("2026-10-01");
  await finder.getByLabel("Retrieved to").fill("2026-10-03");
  await finder.getByLabel("Maximum results").fill("20");
  await finder.getByLabel("Keywords", { exact: true }).press("Enter");
  const results = finder.getByRole("region", { name: "Saved source results" });
  await expect(results.getByRole("status")).toHaveText(
    "1 saved source for “archived text”.",
  );
  expect(api.requests.at(-1)).toMatchObject({
    path: "/api/context/search",
    method: "POST",
    csrf: "synthetic-context-csrf",
    body: {
      query: "archived text",
      limit: 20,
      domain: "docs.example.org",
      retrieved_from: "2026-10-01",
      retrieved_to: "2026-10-03",
      allow_stale: true,
    },
  });
  await expect(finder.getByLabel("Keywords", { exact: true })).toHaveValue(
    "  archived text  ",
  );
  await expect(results.getByText(/Older saved evidence/)).toBeVisible();
  await expect(results.locator(".archive-result-excerpt")).toHaveText(
    source.content,
  );
  await expect(results.locator("script, img")).toHaveCount(0);
  expect(
    api.requests.every((request) => request.path === "/api/context/search"),
  ).toBe(true);
  await results.getByRole("button", { name: "View saved result 1" }).click();
  const viewer = page.getByRole("dialog", {
    name: "Saved source",
    exact: true,
  });
  await expect(viewer.getByLabel("Saved source text")).toHaveText(
    source.content,
  );
  await expect(
    viewer.getByText(`Published ${publication}`, { exact: false }),
  ).toBeVisible();
  const expectedDate = await page.evaluate(
    (value) => new Date(value).toLocaleString(),
    retrieved,
  );
  await expect(
    viewer.getByText(`Search excerpt · Retrieved ${expectedDate}`, {
      exact: false,
    }),
  ).toBeVisible();
  const read = new URL(api.requests.at(-1)!.url);
  expect(read.searchParams.get("expected_hash")).toBe(source.content_hash);
  expect(read.searchParams.get("expected_manifest_hash")).toBe(
    source.manifest_hash,
  );
});

test("finder distinguishes no matches and filter or availability errors while retaining drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  api.searchReplies.set("nothing", { json: savedResult("nothing", []) });
  api.searchReplies.set("invalid filters", {
    status: 422,
    json: { detail: "Check the saved source search filters and try again." },
  });
  api.searchReplies.set("unavailable", {
    status: 409,
    json: { detail: "The saved-source archive directory is unavailable." },
  });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const query = finder.getByLabel("Keywords", { exact: true });
  await query.fill("nothing");
  await finder.getByLabel("Include older saved evidence").uncheck();
  await query.press("Enter");
  await expect(finder.getByRole("status")).toHaveText(
    "No saved sources matched “nothing”.",
  );
  expect(api.requests.at(-1)!.body).toEqual({
    query: "nothing",
    limit: 10,
    allow_stale: false,
  });
  await query.fill("invalid filters");
  await finder
    .getByLabel("Domain (optional)")
    .fill("https://docs.example.org/private");
  await finder.getByLabel("Retrieved from").fill("2026-10-03");
  await finder.getByLabel("Retrieved to").fill("2026-10-01");
  await query.press("Enter");
  await expect(finder.getByRole("alert")).toHaveText(
    "Check the saved source search filters and try again.",
  );
  await expect(query).toHaveValue("invalid filters");
  await expect(finder.getByLabel("Domain (optional)")).toHaveValue(
    "https://docs.example.org/private",
  );
  await expect(finder.getByLabel("Retrieved from")).toHaveValue("2026-10-03");
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await query.fill("unavailable");
  await finder.getByLabel("Domain (optional)").fill("");
  await finder.getByLabel("Retrieved from").fill("");
  await finder.getByLabel("Retrieved to").fill("");
  await query.press("Enter");
  await expect(finder.getByRole("alert")).toHaveText(
    "The saved-source archive directory is unavailable.",
  );
  await expect(query).toHaveValue("unavailable");
  await expect(
    finder.getByRole("button", { name: "Search saved sources", exact: true }),
  ).toBeEnabled();
});

test("finder makes an empty bounded search visible without claiming an exhaustive no-match", async ({
  page,
}) => {
  const api = await fixture(page);
  api.searchReplies.set("broad topic", {
    json: { ...savedResult("broad topic", []), search_limited: true },
  });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  await finder.getByLabel("Keywords", { exact: true }).fill("broad topic");
  await finder.getByLabel("Keywords", { exact: true }).press("Enter");
  await expect(
    finder.getByText(
      "No saved sources found for “broad topic” within this search’s work limit.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    finder.getByText(
      "Saved search reached its work limit. Narrow the keywords, domain or retrieval dates.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(finder.getByText(/No saved sources matched/)).toHaveCount(0);
  await expect(finder.getByLabel("Keywords", { exact: true })).toHaveValue(
    "broad topic",
  );
  await expect(
    finder.getByRole("button", { name: "Search saved sources", exact: true }),
  ).toBeEnabled();
});

test("finder remains readable without writer availability and clears results when disabled", async ({
  page,
}) => {
  const state = workspace();
  state.context_archive!.available = false;
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  await expect(
    finder.getByRole("button", { name: "Search saved sources", exact: true }),
  ).toBeEnabled();
  await finder.getByLabel("Keywords", { exact: true }).fill("read only");
  await finder.getByLabel("Keywords", { exact: true }).press("Enter");
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  const card = page.locator(".context-archive-setup");
  await card.getByLabel("Save and reuse public search results").uncheck();
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  await expect(
    finder.getByRole("button", { name: "Search saved sources", exact: true }),
  ).toBeDisabled();
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await expect(finder.getByLabel("Keywords", { exact: true })).toHaveValue(
    "read only",
  );
  expect(
    api.requests.filter((entry) => entry.path === "/api/context/search"),
  ).toHaveLength(1);
});

test("finder invalidates changed saved eligibility while preserving drafts and unchanged refresh results", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const query = finder.getByLabel("Keywords", { exact: true });
  await query.fill("saved eligibility");
  await finder.getByLabel("Domain (optional)").fill("docs.example.org");
  await query.press("Enter");
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  await card.getByRole("button", { name: "Refresh archive status" }).click();
  await expect(card.getByText("Archive status refreshed.")).toBeVisible();
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  api.hold.add("POST /api/context/search");
  await query.press("Enter");
  await expect.poll(() => api.held.has("POST /api/context/search")).toBe(true);
  await card.getByLabel("Reuse saved searches for (hours)").fill("1");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  await api.held
    .get("POST /api/context/search")!
    .fulfill({ json: savedResult("saved eligibility") });
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await expect(query).toHaveValue("saved eligibility");
  await expect(finder.getByLabel("Domain (optional)")).toHaveValue(
    "docs.example.org",
  );
  await query.press("Enter");
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  await card
    .getByLabel("Approved public source URLs (one per line)")
    .fill("https://other.example.org/");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await expect(query).toHaveValue("saved eligibility");
  await expect(finder.getByLabel("Domain (optional)")).toHaveValue(
    "docs.example.org",
  );
});

test("finder discards delayed evidence when the persisted saving policy changes", async ({
  page,
}) => {
  const state = workspace();
  Object.assign(state.context_archive!.config, {
    capture_policy: "all_public",
    public_sources: [],
  });
  const api = await fixture(page, state);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.locator(".context-archive-setup");
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const keywords = finder.getByLabel("Keywords", { exact: true });
  await keywords.fill("retain these keywords");
  await keywords.press("Enter");
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  await card.getByRole("button", { name: "Refresh archive status" }).click();
  await expect(card.getByText("Archive status refreshed.")).toBeVisible();
  await expect(
    finder.getByRole("heading", { name: source.title }),
  ).toBeVisible();
  api.hold.add("POST /api/context/search");
  await keywords.press("Enter");
  await expect.poll(() => api.held.has("POST /api/context/search")).toBe(true);
  await card
    .getByRole("combobox", { name: "Source saving policy" })
    .selectOption("approved_sources");
  await card.getByRole("button", { name: "Save source settings" }).click();
  await expect(card.getByText("Saved source settings updated.")).toBeVisible();
  await api.held
    .get("POST /api/context/search")!
    .fulfill({ json: savedResult("retain these keywords") });
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await expect(keywords).toHaveValue("retain these keywords");
  expect(api.state.context_archive!.config.capture_policy).toBe(
    "approved_sources",
  );
});

test("finder ignores out-of-order results after the user changes search drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const query = finder.getByLabel("Keywords", { exact: true });
  api.hold.add("POST /api/context/search");
  await query.fill("first");
  await query.press("Enter");
  await expect.poll(() => api.held.has("POST /api/context/search")).toBe(true);
  const first = api.held.get("POST /api/context/search")!;
  api.held.delete("POST /api/context/search");
  await query.fill("second");
  await finder.getByLabel("Domain (optional)").fill("second.example.org");
  api.hold.add("POST /api/context/search");
  await query.press("Enter");
  await expect.poll(() => api.held.has("POST /api/context/search")).toBe(true);
  await api.held.get("POST /api/context/search")!.fulfill({
    json: savedResult("second", [
      {
        ...source,
        capture_id: "capture-two",
        title: "Second archive result",
      },
    ]),
  });
  await expect(
    finder.getByRole("heading", { name: "Second archive result" }),
  ).toBeVisible();
  await first.fulfill({ json: savedResult("first") });
  await expect(
    finder.getByRole("heading", { name: "Second archive result" }),
  ).toBeVisible();
  await expect(finder.getByRole("heading", { name: source.title })).toHaveCount(
    0,
  );
  await expect(query).toHaveValue("second");
  await expect(finder.getByLabel("Domain (optional)")).toHaveValue(
    "second.example.org",
  );
  await expect(finder.getByRole("status")).toHaveText(
    "1 saved source for “second”.",
  );
});

test("finder clear and leaving Settings discard delayed responses without overwriting drafts", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const query = finder.getByLabel("Keywords", { exact: true });
  async function holdSearch(text: string) {
    api.held.delete("POST /api/context/search");
    api.hold.add("POST /api/context/search");
    await query.fill(text);
    await query.press("Enter");
    await expect
      .poll(() => api.held.has("POST /api/context/search"))
      .toBe(true);
    return api.held.get("POST /api/context/search")!;
  }
  const cleared = await holdSearch("cleared request");
  await finder.getByRole("button", { name: "Clear saved search" }).click();
  await expect(query).toBeFocused();
  await cleared.fulfill({ json: savedResult("cleared request") });
  await expect(query).toHaveValue("");
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  const switched = await holdSearch("retained draft");
  await page.getByRole("button", { name: "Open memory", exact: true }).click();
  await switched.fulfill({ json: savedResult("retained draft") });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(query).toHaveValue("retained draft");
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
  await expect(
    finder.getByRole("button", { name: "Search saved sources", exact: true }),
  ).toBeEnabled();
  const unmounted = await holdSearch("unmounted request");
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await unmounted.fulfill({ json: savedResult("unmounted request") });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(query).toHaveValue("");
  await expect(
    finder.getByRole("region", { name: "Saved source results" }),
  ).toHaveCount(0);
});

test("finder removes deleted results and cannot restore a known deleted capture", async ({
  page,
}) => {
  const api = await fixture(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  const query = finder.getByLabel("Keywords", { exact: true });
  await query.fill("archived");
  await query.press("Enter");
  await finder.getByRole("button", { name: "View saved result 1" }).click();
  const viewer = page.getByRole("dialog", {
    name: "Saved source",
    exact: true,
  });
  await expect(viewer.getByLabel("Saved source text")).toHaveText(
    source.content,
  );
  await viewer
    .getByRole("button", { name: "Remove saved source", exact: true })
    .click();
  await viewer.getByRole("button", { name: "Confirm removal" }).click();
  await expect(viewer.getByText(/Saved source removed/)).toBeVisible();
  await viewer.getByRole("button", { name: "Close dialog" }).click();
  await expect(finder.getByRole("status")).toHaveText(
    "No saved sources remain in these results.",
  );
  await expect(
    finder.getByRole("button", { name: "View saved result 1" }),
  ).toHaveCount(0);
  api.searchReplies.set("late deleted result", {
    json: savedResult("late deleted result"),
  });
  await query.fill("late deleted result");
  await query.press("Enter");
  await expect(finder.getByRole("status")).toHaveText(
    "No saved sources remain in these results.",
  );
  await expect(finder.getByRole("heading", { name: source.title })).toHaveCount(
    0,
  );
  await expect(query).toHaveValue("late deleted result");
});

test("finder result excerpts remain bounded plaintext and keyboard usable on a narrow screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const api = await fixture(page);
  const longSource = {
    ...source,
    title: "Long public source " + "reference".repeat(25),
    url: "https://docs.example.org/" + "long-path".repeat(30),
    content: source.content + "Reference material ".repeat(100),
    published_at: null,
    stale: true,
  };
  api.searchReplies.set("long reference", {
    json: savedResult("long reference", [longSource]),
  });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const finder = page.getByRole("region", {
    name: "Find saved sources",
    exact: true,
  });
  await finder.getByLabel("Keywords", { exact: true }).fill("long reference");
  await finder.getByLabel("Keywords", { exact: true }).press("Enter");
  const results = finder.getByRole("region", { name: "Saved source results" });
  await expect(
    results.getByRole("heading", { name: longSource.title }),
  ).toBeVisible();
  await expect(results.locator(".archive-result-excerpt")).toHaveText(
    longSource.content.slice(0, 360) + "…",
  );
  await expect(results.locator("script, img")).toHaveCount(0);
  await expect(
    results.getByText("Publication date unknown", { exact: false }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const open = results.getByRole("button", { name: "View saved result 1" });
  await open.focus();
  await open.press("Enter");
  await expect(
    page.getByRole("dialog").getByLabel("Saved source text"),
  ).toBeVisible();
  await page.getByRole("dialog").press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(open).toBeFocused();
});

test("source mode and the text snapshot viewer fit a narrow screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const state = workspace();
  state.provider.config.model = "synthetic-local-" + "model".repeat(35);
  state.provider.models = [state.provider.config.model];
  state.messages = [
    {
      id: "answer",
      role: "assistant",
      text: "Sourced answer",
      web_search: {
        query: "public reference",
        at: retrieved,
        sources: [source],
      },
    },
  ];
  await fixture(page, state);
  await openSearchOptions(page);
  await expect(
    page.getByRole("combobox", { name: "Source mode" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Close search options" }).click();
  await page.getByRole("button", { name: "View saved source 1" }).click();
  await expect(
    page.getByRole("dialog").getByLabel("Saved source text"),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
