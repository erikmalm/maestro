import { expect, test, type Page, type Route } from "@playwright/test";
import type { ResearchRun } from "../src/api";

const chatId = "f".repeat(32);
const firstId = "a".repeat(32);
const secondId = "b".repeat(32);
const candidate = {
  capture_id: "c".repeat(32),
  content_hash: "1".repeat(64),
  manifest_hash: "2".repeat(64),
  retrieved_at: "2026-10-17T12:00:00Z",
  stale: true,
};
const allowance = {
  model_calls: 0,
  tokens: 0,
  seconds: 30,
  estimated_model_usd: 0,
  hosted_searches: 0,
};
function recorded(
  id = firstId,
  state: ResearchRun["state"] = "partial",
): ResearchRun {
  return {
    id,
    submission_id: id,
    request: "Inspect the recorded Northstar report",
    query: "Northstar report",
    chat_id: chatId,
    state,
    created_at: "2026-10-21T12:00:00Z",
    updated_at: "2026-10-21T12:00:01Z",
    permissions: {
      source_mode: "saved_only",
      hosted_allowed: false,
      memory_recall: false,
      memory_derived: false,
      chat_id: chatId,
    },
    budget: {
      total: allowance,
      consumed: { ...allowance, seconds: 1 },
      reserved: { ...allowance, seconds: 0 },
      remaining: { ...allowance, seconds: 29 },
      deadline: "2026-10-21T12:00:30Z",
      daily_search: { used: 2, remaining: 18, limit: 20 },
    },
    result:
      state === "partial"
        ? {
            lookup_state: "ready",
            search_limited: false,
            skipped_count: 0,
            candidates: [candidate],
            summary: "One saved version was inspected.",
          }
        : null,
    events: [
      {
        sequence: 1,
        at: "2026-10-21T12:00:00Z",
        kind: "submitted",
        summary: "Request recorded.",
      },
      ...(state === "partial"
        ? [
            {
              sequence: 2,
              at: "2026-10-21T12:00:01Z",
              kind: "lookup",
              summary: "Saved-evidence lookup completed.",
            },
          ]
        : []),
    ],
  };
}
function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
type Submission = {
  submission_id: string;
  request: string;
  query: string;
  chat_id: string | null;
};
type MockState = {
  runs: ResearchRun[];
  posts: Submission[];
  listReads: number;
  detailReads: number;
  create?: (route: Route, body: Submission) => Promise<void>;
  detail?: (route: Route, run: ResearchRun | undefined) => Promise<void>;
};
async function fixture(page: Page, runs: ResearchRun[] = []) {
  const state: MockState = { runs, posts: [], listReads: 0, detailReads: 0 };
  await page.clock.install();
  await page.route("**/api/workspace**", async (route) => {
    const response = await route.fetch({
      url: new URL("/api/workspace", route.request().url()).href,
    });
    const workspace = await response.json();
    workspace.active_chat_id = chatId;
    workspace.chats = [
      {
        id: chatId,
        title: "Synthetic originating conversation",
        created_at: "2026-10-21T12:00:00Z",
        updated_at: "2026-10-21T12:00:00Z",
      },
    ];
    workspace.messages = [];
    workspace.work.config.enabled = false;
    await route.fulfill({ json: workspace });
  });
  await page.route("**/api/research/runs**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === "/api/research/runs") {
      if (method === "GET") {
        state.listReads += 1;
        expect(url.searchParams.get("limit")).toBe("20");
        return route.fulfill({
          json: {
            runs: state.runs.map(({ events: _events, ...run }) => run),
            skipped_count: 0,
          },
        });
      }
      expect(method).toBe("POST");
      expect(route.request().headers()["x-maestro-csrf"]).toBeTruthy();
      const body = route.request().postDataJSON() as Submission;
      state.posts.push(body);
      if (state.create) return state.create(route, body);
      const run = { ...recorded(firstId, "queued"), ...body };
      state.runs = [run];
      return route.fulfill({ status: 202, json: { run, created: true } });
    }
    const id = url.pathname.split("/")[4];
    const run = state.runs.find((item) => item.id === id);
    if (method === "GET") {
      state.detailReads += 1;
      if (state.detail) return state.detail(route, run);
      return route.fulfill({
        status: run ? 200 : 404,
        json: run || { detail: "This research run is unavailable." },
      });
    }
    expect(route.request().headers()["x-maestro-csrf"]).toBeTruthy();
    if (method === "DELETE") {
      state.runs = state.runs.filter((item) => item.id !== id);
      return route.fulfill({ json: { deleted: true } });
    }
    expect(method).toBe("POST");
    expect(url.pathname.endsWith("/cancel")).toBeTruthy();
    const cancelled = {
      ...run!,
      state: "cancelled" as const,
      updated_at: "2026-10-21T12:00:02Z",
      events: [
        ...(run?.events || []),
        {
          sequence: 3,
          at: "2026-10-21T12:00:02Z",
          kind: "cancelled",
          summary: "Cancellation recorded.",
        },
      ],
    };
    state.runs = state.runs.map((item) => (item.id === id ? cancelled : item));
    return route.fulfill({ json: cancelled });
  });
  return state;
}
const selected = (page: Page) =>
  page.getByRole("region", { name: "Selected research run" });
async function open(page: Page, suffix = "") {
  await page.goto("/" + suffix);
  if (!suffix)
    await page.getByRole("button", { name: "Research", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research", exact: true }),
  ).toBeVisible();
}
const focus = (page: Page) =>
  page.evaluate(() => window.dispatchEvent(new Event("focus")));

test("Research records one opt-in preview with conversation scope and only actual activity", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  await expect(
    page.getByText(/makes no model calls or hosted searches/),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill("Inspect the saved Northstar report");
  await page
    .getByRole("textbox", {
      name: "Saved-evidence keywords (optional)",
      exact: true,
    })
    .fill("Northstar");
  await page
    .getByRole("button", { name: "Inspect saved evidence", exact: true })
    .click();
  await expect(selected(page).getByRole("status")).toHaveText("Queued");
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]).toEqual({
    submission_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    request: "Inspect the saved Northstar report",
    query: "Northstar",
    chat_id: chatId,
  });
  const done = { ...recorded(), ...state.posts[0] };
  state.runs = [done];
  await page.clock.runFor(2600);
  await expect(selected(page).getByRole("status")).toHaveText(
    "Partial — no reviewed answer",
  );
  const activity = selected(page).getByRole("region", {
    name: "Recorded activity",
  });
  await expect(activity.getByRole("listitem")).toHaveCount(2);
  await expect(activity).not.toContainText("reviewed");
  await expect(
    selected(page).getByText("1 available saved-version references.", {
      exact: true,
    }),
  ).toBeVisible();
  await selected(page)
    .getByText("Saved version", { exact: false })
    .filter({ has: page.locator("code") })
    .first()
    .click();
  await expect(
    selected(page).getByText(candidate.content_hash, { exact: true }),
  ).toBeVisible();
  await expect(
    selected(page).getByText("Outside reuse window", { exact: true }),
  ).toBeVisible();
  await expect(
    selected(page).getByRole("table", { name: "Recorded shared allowance" }),
  ).toBeVisible();
  await expect(
    selected(page).getByText("Not permitted", { exact: true }),
  ).toHaveCount(2);
  expect(state.posts).toHaveLength(1);
  await page.screenshot({
    path: test.info().outputPath("research-preview.png"),
    fullPage: true,
  });
});

test("selected research run survives reload without resubmitting or browser-storing request text", async ({
  page,
}) => {
  const older = {
    ...recorded(firstId),
    request: "Original selected research request",
  };
  const newer = {
    ...recorded(secondId),
    request: "Different recent request",
    updated_at: "2026-10-21T12:30:00Z",
  };
  const state = await fixture(page, [newer, older]);
  await open(page, "?research_run=" + firstId);
  await expect(
    selected(page).getByText(older.request, { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Research", exact: true }),
  ).toBeVisible();
  await expect(
    selected(page).getByText(older.request, { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp("research_run=" + firstId));
  expect(state.posts).toHaveLength(0);
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...sessionStorage, ...localStorage }),
  );
  expect(storage).not.toContain(older.request);
  expect(storage).not.toContain(older.query);
});

test("an unknown POST response retries the same identity and a new submission gets a fresh identity", async ({
  page,
}) => {
  const state = await fixture(page);
  state.create = async (route, body) => {
    if (state.posts.length === 1) return route.abort("failed");
    const run = {
      ...recorded(state.posts.length === 2 ? firstId : secondId),
      ...body,
    };
    state.runs = [run, ...state.runs];
    return route.fulfill({
      status: 202,
      json: { run, created: state.posts.length !== 2 },
    });
  };
  await open(page);
  const request = "Private request retained only in memory";
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill(request);
  await page
    .getByRole("button", { name: "Inspect saved evidence", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry same submission" }),
  ).toBeVisible();
  const stored = await page.evaluate(() =>
    sessionStorage.getItem("maestro.research.pending"),
  );
  expect(stored).toMatch(/^[a-f0-9]{32}$/);
  expect(stored).not.toContain(request);
  await page.getByRole("button", { name: "Retry same submission" }).click();
  await expect(selected(page)).toBeVisible();
  expect(state.posts[1]).toEqual(state.posts[0]);
  await expect(
    page.getByText("Existing submission restored.", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("maestro.research.pending"),
    ),
  ).toBeNull();
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill("A new request");
  await page
    .getByRole("button", { name: "Inspect saved evidence", exact: true })
    .click();
  await expect.poll(() => state.posts.length).toBe(3);
  expect(state.posts[2].submission_id).not.toBe(state.posts[0].submission_id);
});

test("a lost response can be reconciled from its opaque submission identity after reload", async ({
  page,
}) => {
  const state = await fixture(page);
  let committed: ResearchRun;
  state.create = async (route, body) => {
    committed = { ...recorded(), ...body };
    return route.abort("failed");
  };
  await open(page);
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill("Reconcile this saved request");
  await page
    .getByRole("button", { name: "Inspect saved evidence", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry same submission" }),
  ).toBeVisible();
  state.runs = [committed!];
  await page.reload();
  await expect(
    selected(page).getByText("Reconcile this saved request", { exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp("research_run=" + firstId));
  expect(state.posts).toHaveLength(1);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("maestro.research.pending"),
    ),
  ).toBeNull();
});

test("cancel and delete fence snapshots started before each action", async ({
  page,
}) => {
  const state = await fixture(page, [recorded(firstId, "running")]);
  const holds: ReturnType<typeof deferred>[] = [];
  let held = 0;
  state.detail = async (route, run) => {
    const snapshot = structuredClone(run);
    const gate = holds.shift();
    if (gate) {
      held += 1;
      await gate.promise;
    }
    return route.fulfill({
      status: snapshot ? 200 : 404,
      json: snapshot || { detail: "Unavailable" },
    });
  };
  await open(page, "?research_run=" + firstId);
  await expect(selected(page).getByRole("status")).toHaveText("Running");
  const beforeCancel = deferred();
  holds.push(beforeCancel);
  await focus(page);
  await expect.poll(() => held).toBe(1);
  await selected(page)
    .getByRole("button", { name: "Cancel run", exact: true })
    .click();
  await expect(selected(page).getByRole("status")).toHaveText("Cancelled");
  beforeCancel.release();
  await expect(selected(page).getByRole("status")).toHaveText("Cancelled");
  const beforeDelete = deferred();
  holds.push(beforeDelete);
  await focus(page);
  await expect.poll(() => held).toBe(2);
  await selected(page)
    .getByRole("button", { name: "Delete run", exact: true })
    .click();
  await expect(selected(page)).toHaveCount(0);
  beforeDelete.release();
  await expect(page).not.toHaveURL(/research_run=/);
  await expect(
    page
      .getByRole("region", { name: "Recent research runs" })
      .getByRole("button"),
  ).toHaveCount(0);
  await expect(selected(page)).toHaveCount(0);
});

test("unavailable and limited receipts stay partial, and refreshed permissions withhold old references", async ({
  page,
}) => {
  const run = recorded();
  run.result = {
    lookup_state: "unavailable",
    search_limited: false,
    skipped_count: 0,
    candidates: [],
    summary: "Archive unavailable.",
  };
  const state = await fixture(page, [run]);
  await open(page, "?research_run=" + firstId);
  await expect(
    selected(page).getByText(
      "Saved evidence was unavailable. This is not an exhaustive no-match.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(selected(page).getByRole("status")).toHaveText(
    "Partial — no reviewed answer",
  );
  state.runs = [
    {
      ...recorded(),
      result: {
        ...recorded().result!,
        lookup_state: "limited",
        search_limited: true,
        skipped_count: 2,
      },
    },
  ];
  await focus(page);
  await expect(
    selected(page).getByText(
      "Results may be incomplete. 2 records were skipped.",
      { exact: true },
    ),
  ).toBeVisible();
  state.runs = [
    {
      ...recorded(),
      permissions: { ...recorded().permissions, memory_derived: true },
      result: {
        ...recorded().result!,
        candidates: [],
        references_unavailable: 1,
      },
    },
  ];
  await focus(page);
  await expect(
    selected(page).getByText(
      "Memory-derived conversation restrictions retained",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    selected(page).getByText(
      "1 previously recorded reference is no longer available.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    selected(page).getByText(candidate.capture_id, { exact: true }),
  ).toHaveCount(0);
  state.runs = [];
  await focus(page);
  await expect(selected(page)).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText(
    "This research run is unavailable.",
  );
  state.runs = [recorded()];
  await focus(page);
  await expect(
    selected(page).getByText("1 available saved-version references.", {
      exact: true,
    }),
  ).toBeVisible();
});

test("recorded activity polls while visible and refreshes after returning", async ({
  page,
}) => {
  const state = await fixture(page, [recorded()]);
  await open(page, "?research_run=" + firstId);
  await expect(selected(page)).toBeVisible();
  const before = state.detailReads;
  await page.clock.runFor(5100);
  await expect.poll(() => state.detailReads).toBeGreaterThan(before);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const hiddenReads = { list: state.listReads, detail: state.detailReads };
  await page.clock.runFor(7500);
  expect({ list: state.listReads, detail: state.detailReads }).toEqual(
    hiddenReads,
  );
  state.runs = [
    {
      ...recorded(firstId, "interrupted"),
      events: [
        {
          sequence: 3,
          at: "2026-10-21T12:01:00Z",
          kind: "interrupted",
          summary: "Interrupted work was recorded; no automatic retry.",
        },
      ],
    },
  ];
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(selected(page).getByRole("status")).toHaveText("Interrupted");
  await expect(
    selected(page).getByText(
      "Interrupted work was recorded; no automatic retry.",
      { exact: true },
    ),
  ).toBeVisible();
  expect(state.posts).toHaveLength(0);
});

test("a response slower than the polling interval completes before a queued refresh starts", async ({
  page,
}) => {
  const state = await fixture(page, [recorded(firstId, "running")]);
  const reads: {
    gate: ReturnType<typeof deferred>;
    snapshot: ResearchRun | undefined;
  }[] = [];
  state.detail = async (route, run) => {
    const read = { gate: deferred(), snapshot: structuredClone(run) };
    reads.push(read);
    await read.gate.promise;
    return route.fulfill({ json: read.snapshot });
  };
  try {
    await open(page, "?research_run=" + firstId);
    await expect.poll(() => reads.length).toBe(1);
    state.runs = [
      {
        ...recorded(firstId, "interrupted"),
        updated_at: "2026-10-21T12:01:00Z",
      },
    ];
    await page.clock.runFor(5100);
    await focus(page);
    expect(reads).toHaveLength(1);
    reads[0].gate.release();
    await expect(selected(page).getByRole("status")).toHaveText("Running");
    await expect.poll(() => reads.length).toBe(2);
    reads[1].gate.release();
    await expect(selected(page).getByRole("status")).toHaveText("Interrupted");
    expect(state.posts).toHaveLength(0);
  } finally {
    for (const read of reads) read.gate.release();
  }
});
