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
function assessed(decision: "ready" | "clarify" = "ready"): ResearchRun {
  const run = recorded(
    firstId,
    decision === "clarify" ? "clarification" : "partial",
  );
  run.workflow = "requirements";
  run.query = "";
  run.result = null;
  run.request =
    "Compare Northstar Class A revenue in FY2025, in USD millions, using annual reports only; do not search the web.";
  run.assessment = {
    version: 1,
    decision,
    subjects: [
      {
        id: "northstar-a",
        kind: "security",
        name: "Northstar Class A",
        identifiers: [
          { kind: "ticker", text: "NSTA" },
          { kind: "listing", text: "Example exchange" },
        ],
      },
    ],
    items: [
      {
        id: "revenue",
        subject_ids: ["northstar-a"],
        criterion: "Compare the requested revenue measure",
        period: "FY2025",
        unit: "millions",
        currency: "USD",
        freshness: "Annual report for FY2025",
        mandatory: true,
      },
    ],
    constraints: [
      {
        kind: "source",
        strength: "require",
        item_ids: ["revenue"],
        criterion: "Annual reports only",
      },
      {
        kind: "search",
        strength: "forbid",
        item_ids: [],
        criterion: "Do not search the web",
      },
    ],
    clarifications:
      decision === "clarify"
        ? [
            {
              kind: "period",
              item_ids: ["revenue"],
              quote: "FY2025",
              question: "Which reporting period should be used?",
            },
          ]
        : [],
    completion_criterion: run.request,
  };
  run.budget.total = {
    ...allowance,
    model_calls: 4,
    tokens: 100000,
    seconds: 120,
    estimated_model_usd: 1,
  };
  run.budget.consumed = { ...run.budget.consumed, model_calls: 1, tokens: 48 };
  run.budget.remaining = {
    ...run.budget.total,
    model_calls: 3,
    tokens: 99952,
    seconds: 119,
  };
  run.budget.deadline = "2026-10-21T12:02:00Z";
  run.events = [
    run.events![0],
    {
      sequence: 2,
      at: "2026-10-21T12:00:01Z",
      kind: "requirements_finished",
      summary: "Research requirements recorded.",
    },
  ];
  return run;
}
function evidenceReviewed(): ResearchRun {
  const run = assessed();
  run.workflow = "evidence_review";
  const base = run.assessment!;
  base.subjects.push({
    id: "boreal",
    kind: "company",
    name: "Boreal",
    identifiers: [],
  });
  base.items = [
    { ...base.items[0], id: "revenue", criterion: "Northstar revenue" },
    {
      ...base.items[0],
      id: "boreal-revenue",
      criterion: "Boreal revenue",
      subject_ids: ["boreal"],
      period: "FY2024",
      currency: "SEK",
    },
    {
      ...base.items[0],
      id: "price",
      criterion: "Current share price",
      period: null,
      unit: null,
    },
    { ...base.items[0], id: "profit", criterion: "Operating profit" },
    {
      ...base.items[0],
      id: "expenses",
      criterion: "Boreal expenses",
      subject_ids: ["boreal"],
      period: "FY2024",
      currency: "SEK",
    },
  ];
  const source = {
    ...candidate,
    id: "c1",
    title: "Saved Northstar annual report",
    url: "https://example.org/report",
    published_at: "2026-01-20T12:00:00Z",
    modified_at: null,
    completeness: { maestro_truncated: false, full_page: false as const },
  };
  const passages = [
    {
      id: "p1",
      source_id: "c1",
      start: 0,
      end: 24,
      text: "FY2025 revenue was USD 10.",
      passage_hash: "3".repeat(64),
    },
    {
      id: "p2",
      source_id: "c1",
      start: 25,
      end: 50,
      text: "Old observed share price.",
      passage_hash: "4".repeat(64),
    },
    {
      id: "p3",
      source_id: "c1",
      start: 51,
      end: 76,
      text: "Operating profit was 4.",
      passage_hash: "5".repeat(64),
    },
    {
      id: "p4",
      source_id: "c2",
      start: 0,
      end: 25,
      text: "Operating profit was 6.",
      passage_hash: "6".repeat(64),
    },
  ];
  for (const passage of passages)
    passage.end = passage.start + Array.from(passage.text).length;
  const support = (
    index: number,
    temporal: "historical" | "refresh" = "historical",
  ) => ({
    passage_id: passages[index].id,
    quote: passages[index].text,
    temporal_scope: temporal,
    period: temporal === "historical" ? "FY2025" : null,
    observation_date: null,
    source_id: passages[index].source_id,
    start: passages[index].start,
    end: passages[index].end,
    freshness_basis:
      temporal === "historical"
        ? "explicit_requested_period"
        : "fresh_origin_check_required",
    best_before: null,
  });
  run.evidence_review = {
    version: 1,
    checked_at: "2026-10-23T10:00:00Z",
    offset_unit: "unicode_codepoint",
    lookup: { state: "limited", search_limited: true, skipped_count: 3 },
    selection: { limited: true, omitted_count: 2 },
    sources: [
      source,
      {
        ...source,
        id: "c2",
        capture_id: "d".repeat(32),
        title: "Alternative profit disclosure",
        url: "https://example.org/alternative",
        content_hash: "7".repeat(64),
      },
    ],
    passages,
    items: [
      {
        item_id: "revenue",
        state: "covered",
        reason: "The historical period and requested measure match.",
        supports: [support(0)],
      },
      {
        item_id: "boreal-revenue",
        state: "missing",
        reason: "No sufficient support in the inspected candidates.",
        supports: [],
      },
      {
        item_id: "price",
        state: "stale",
        reason: "The old quote does not establish the current price.",
        suitability_reason: "No current observation is available.",
        supports: [support(1, "refresh")],
      },
      {
        item_id: "profit",
        state: "conflicting",
        reason: "The supplied disclosures disagree.",
        supports: [support(2), support(3)],
      },
      {
        item_id: "expenses",
        state: "unavailable",
        reason: "The saved source could not be verified.",
        supports: [],
      },
    ],
  };
  return run;
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
  workflow: "evidence_review";
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

test("Research records requested items and constraints through explicit local evidence review", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  await expect(
    page.getByText(/reviews a bounded selection of saved evidence/),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill(assessed().request);
  await page
    .getByRole("button", { name: "Review saved evidence", exact: true })
    .click();
  await expect(selected(page).getByRole("status")).toHaveText("Queued");
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]).toEqual({
    submission_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    request: assessed().request,
    query: "",
    workflow: "evidence_review",
    chat_id: chatId,
  });
  const done = { ...assessed(), ...state.posts[0] };
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
  const requirements = selected(page).getByRole("region", {
    name: "Requirements assessment",
    exact: true,
  });
  const items = requirements.getByRole("region", {
    name: "Requested items",
    exact: true,
  });
  await expect(items.getByRole("listitem")).toHaveCount(1);
  await expect(items).toContainText("Compare the requested revenue measure");
  await expect(items).toContainText("Northstar Class A");
  await expect(items).toContainText("FY2025");
  await expect(items).toContainText("millions");
  await expect(items).toContainText("USD");
  await expect(requirements).toContainText("NSTA");
  await expect(requirements).toContainText("Example exchange");
  const constraints = requirements.getByRole("region", {
    name: "Research constraints",
  });
  await expect(constraints).toContainText("Annual reports only");
  await expect(constraints).toContainText("Requested items: 1");
  await expect(constraints).toContainText("Do not search the web");
  await expect(constraints).toContainText("Applies to the whole request");
  await expect(requirements).toContainText(
    "No reviewed answer has been produced",
  );
  await expect(
    selected(page).getByRole("region", { name: "Saved-version receipt" }),
  ).toHaveCount(0);
  await expect(
    selected(page).getByText("Saved-evidence keywords", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: "Saved-evidence keywords (optional)" }),
  ).toHaveCount(0);
  await expect(
    selected(page).getByRole("table", { name: "Recorded shared allowance" }),
  ).toBeVisible();
  await expect(
    selected(page).getByText("Not permitted", { exact: true }),
  ).toHaveCount(2);
  expect(state.posts).toHaveLength(1);
  await page.screenshot({
    path: test.info().outputPath("research-requirements.png"),
    fullPage: true,
  });
});

test("per-item evidence decisions preserve qualifiers, exact passages and bounded-lookup limits", async ({
  page,
}) => {
  const run = evidenceReviewed();
  const state = await fixture(page, [run]);
  await open(page, "?research_run=" + firstId);
  const review = selected(page).getByRole("region", {
    name: "Saved-evidence review",
    exact: true,
  });
  await expect(review).toContainText(
    "No answer has been written or finally reviewed",
  );
  await expect(review).toContainText(
    "Supporting evidence may exist outside the inspected candidates",
  );
  await expect(review).toContainText("3 records were skipped");
  await expect(review).toContainText(
    "2 candidates were not included in this assessment",
  );
  await expect(review).toContainText(
    "it does not establish that evidence is absent elsewhere",
  );
  const covered = review.getByRole("region", {
    name: "Evidence for requested item 1",
    exact: true,
  });
  await expect(covered).toContainText("Covered");
  await expect(covered).toContainText(
    "Northstar Class A · FY2025 · millions · USD",
  );
  await expect(covered.getByRole("blockquote")).toHaveText(
    "FY2025 revenue was USD 10.",
  );
  await expect(covered).toContainText("Outside reuse window");
  await expect(covered).toContainText(
    "does not establish suitability for the requested period",
  );
  await expect(covered).toContainText("Historical reporting period");
  await expect(covered).toContainText("Best-before dateNot established");
  await expect(
    covered.getByRole("link", { name: "Open live source" }),
  ).toHaveAttribute("href", "https://example.org/report");
  await covered
    .getByText("Passage and saved-version references", { exact: true })
    .click();
  await expect(
    covered.getByText(candidate.content_hash, { exact: true }),
  ).toBeVisible();
  await expect(
    covered.getByText(candidate.manifest_hash, { exact: true }),
  ).toBeVisible();
  const missing = review.getByRole("region", {
    name: "Evidence for requested item 2",
    exact: true,
  });
  await expect(missing).toContainText("Boreal · FY2024 · millions · SEK");
  await expect(missing).toContainText("Missing");
  const stale = review.getByRole("region", {
    name: "Evidence for requested item 3",
    exact: true,
  });
  await expect(stale).toContainText("Stale for this request");
  await expect(stale).toContainText("No current observation is available");
  await expect(stale).toContainText("The old quote does not establish the current price.");
  const conflicting = review.getByRole("region", {
    name: "Evidence for requested item 4",
    exact: true,
  });
  await expect(conflicting).toContainText("Conflicting");
  await expect(conflicting.getByRole("blockquote")).toHaveCount(2);
  await expect(conflicting).toContainText("Operating profit was 4.");
  await expect(conflicting).toContainText("Operating profit was 6.");
  await expect(
    review.getByRole("region", {
      name: "Evidence for requested item 5",
      exact: true,
    }),
  ).toContainText("Unavailable");
  await expect(selected(page).getByRole("status")).toHaveText(
    "Partial — no reviewed answer",
  );
  expect(state.posts).toHaveLength(0);
});

test("refreshed source withholding removes old passages and uses authoritative suitability reasons", async ({
  page,
}) => {
  const run = evidenceReviewed();
  const state = await fixture(page, [run]);
  await open(page, "?research_run=" + firstId);
  const review = selected(page).getByRole("region", {
    name: "Saved-evidence review",
    exact: true,
  });
  await expect(review).toContainText("FY2025 revenue was USD 10.");
  const withheld = structuredClone(run);
  withheld.evidence_review!.sources = [];
  withheld.evidence_review!.passages = [];
  for (const item of withheld.evidence_review!.items) {
    item.model_state = item.state;
    item.state = "unavailable";
    item.supports = [];
    item.reason = "Source verification failed; coverage must be reassessed.";
    item.suitability_reason =
      "Saved evidence is no longer available for this assessment.";
  }
  state.runs = [withheld];
  await focus(page);
  await expect(review).not.toContainText("FY2025 revenue was USD 10.");
  await expect(review).not.toContainText("Saved Northstar annual report");
  await expect(review).toContainText(
    "Source verification failed; coverage must be reassessed.",
  );
  await expect(review).not.toContainText(
    "The historical period and requested measure match.",
  );
  await expect(review.getByText("Unavailable", { exact: true })).toHaveCount(5);
  await expect(review.getByRole("link")).toHaveCount(0);
  await expect(review).toContainText(
    "Saved evidence is no longer available for this assessment",
  );
  expect(state.posts).toHaveLength(0);
});

test("historical requirements and lookup runs retain their separate readable views", async ({
  page,
}) => {
  const requirements = assessed();
  const lookup = {
    ...recorded(secondId),
    request: "Historical saved-evidence lookup",
  };
  await fixture(page, [requirements, lookup]);
  await open(page, "?research_run=" + firstId);
  await expect(
    selected(page).getByRole("region", {
      name: "Requirements assessment",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    selected(page).getByRole("region", {
      name: "Saved-evidence review",
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("region", { name: "Recent research runs" })
    .getByRole("button", { name: /Historical saved-evidence lookup/ })
    .click();
  await expect(
    selected(page).getByRole("region", {
      name: "Saved-version receipt",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    selected(page).getByRole("region", {
      name: "Requirements assessment",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    selected(page).getByRole("region", {
      name: "Saved-evidence review",
      exact: true,
    }),
  ).toHaveCount(0);
});

test("material ambiguity displays clarification and remains stopped after polling and reload", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill(assessed("clarify").request);
  await page
    .getByRole("button", { name: "Review saved evidence", exact: true })
    .click();
  await expect(selected(page).getByRole("status")).toHaveText("Queued");
  state.runs = [{ ...assessed("clarify"), ...state.posts[0] }];
  await page.clock.runFor(2600);
  await expect(selected(page).getByRole("status")).toHaveText(
    "Clarification needed — no reviewed answer",
  );
  const clarification = selected(page).getByRole("region", {
    name: "Clarification needed",
    exact: true,
  });
  await expect(clarification).toContainText(
    "Which reporting period should be used?",
  );
  await expect(clarification).toContainText("FY2025");
  await expect(clarification).toContainText("Requested items: 1");
  await expect(clarification).toContainText(
    "This run will not continue automatically",
  );
  await expect(
    selected(page).getByRole("button", { name: "Cancel run", exact: true }),
  ).toHaveCount(0);
  await page.clock.runFor(7500);
  await page.reload();
  await expect(selected(page).getByRole("status")).toHaveText(
    "Clarification needed — no reviewed answer",
  );
  await expect(clarification).toContainText(
    "Which reporting period should be used?",
  );
  expect(state.posts).toHaveLength(1);
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...sessionStorage, ...localStorage }),
  );
  expect(storage).not.toContain(state.posts[0].request);
  expect(storage).not.toContain("Which reporting period should be used?");
});

test("failed assessment and rejected submission keep the no-answer boundary", async ({
  page,
}) => {
  const failed = { ...assessed(), state: "failed" as const, assessment: null };
  const state = await fixture(page, [failed]);
  await open(page, "?research_run=" + firstId);
  await expect(selected(page).getByRole("status")).toHaveText("Failed");
  await expect(
    selected(page).getByRole("region", {
      name: "Requirements assessment",
      exact: true,
    }),
  ).toContainText(
    "No validated assessment has been recorded. No answer was produced.",
  );
  await expect(
    selected(page).getByRole("region", { name: "Saved-version receipt" }),
  ).toHaveCount(0);
  state.create = async (route) =>
    route.fulfill({
      status: 409,
      json: {
        detail:
          "Choose an installed local model before assessing requirements.",
      },
    });
  await page
    .getByRole("textbox", { name: "Research request", exact: true })
    .fill("Assess a new request");
  await page
    .getByRole("button", { name: "Review saved evidence", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Choose an installed local model before assessing requirements.",
  );
  await expect(
    page.getByRole("button", { name: "Review saved evidence", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Retry same submission" }),
  ).toHaveCount(0);
  await page.clock.runFor(5100);
  expect(state.posts).toHaveLength(1);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("maestro.research.pending"),
    ),
  ).toBeNull();
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
    .getByRole("button", { name: "Review saved evidence", exact: true })
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
    .getByRole("button", { name: "Review saved evidence", exact: true })
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
    .getByRole("button", { name: "Review saved evidence", exact: true })
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
  await expect(
    selected(page).getByText("Saved-evidence lookup was incomplete.", {
      exact: true,
    }),
  ).toBeVisible();
  // A limited result can reflect withheld evidence rather than a work cap.
  await expect(selected(page)).not.toContainText(/work limit/i);
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
  const historical = recorded();
  historical.created_at = "2020-01-01T12:00:00Z";
  historical.updated_at = "2020-01-01T12:00:01Z";
  historical.result!.candidates = [
    { ...candidate, retrieved_at: "2020-01-01T11:00:00Z", stale: false },
  ];
  state.runs = [historical];
  await focus(page);
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
    selected(page).getByText("Within reuse window", { exact: true }),
  ).toBeVisible();
  // Reopening an old receipt must identify the stored flag's original scope.
  await expect(
    selected(page).getByText("Reuse-window flag at lookup", { exact: true }),
  ).toBeVisible();
  await expect(
    selected(page).getByText("Reuse-window flag", { exact: true }),
  ).toHaveCount(0);
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
