import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const artifacts = join(
  process.env.LOCALAPPDATA || join(homedir(), ".local", "share"),
  "Maestro",
  "preview",
  "artifacts",
);

const savedText =
  "<script>window.__evidenceExecuted = true</script>\nRevenue <b>42</b> & margin 7%.";
const source = {
  capture_id: "a".repeat(32),
  title: "Northstar quarterly report",
  url: "https://reports.example.org/northstar/quarterly",
  retrieved_at: "2026-10-05T12:00:00Z",
  published_at: null,
  modified_at: null,
  content_kind: "search_excerpt",
  completeness: { maestro_truncated: false, full_page: false },
  content_hash: "1".repeat(64),
  manifest_hash: "2".repeat(64),
  content_bytes: new TextEncoder().encode(savedText).length,
  body_verified: false,
};
const anotherSource = {
  ...source,
  capture_id: "b".repeat(32),
  title: "Harbour FC official match result",
  url: "https://club.example.org/results/round-7",
  retrieved_at: "",
  content_hash: "3".repeat(64),
  manifest_hash: "4".repeat(64),
};
const ready = (sources = [source], next_cursor: string | null = null) => ({
  state: "ready",
  sources,
  next_cursor,
  skipped_count: 0,
});
type Inventory = ReturnType<typeof ready>;
type SourceReply = { status?: number; body: unknown };

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function evidenceFixture(
  page: Page,
  inventory: (url: URL) => Inventory | object | Promise<Inventory | object>,
  detail = async (_url: URL): Promise<SourceReply> => ({
    body: { ...source, content: savedText },
  }),
) {
  const listings: URL[] = [];
  const openings: URL[] = [];
  await page.clock.install();
  await page.route("**/api/context/sources**", async (route) => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    if (url.pathname === "/api/context/sources") {
      listings.push(url);
      await route.fulfill({ json: await inventory(url) });
    } else {
      openings.push(url);
      const response = await detail(url);
      await route.fulfill({
        status: response.status ?? 200,
        json: response.body,
      });
    }
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Evidence", exact: true }),
  ).toBeVisible();
  return { listings, openings };
}

async function returnToEvidence(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
}

async function setVisible(page: Page, visible: boolean) {
  await page.evaluate((isVisible) => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => !isVisible,
    });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (isVisible ? "visible" : "hidden"),
    });
    document.dispatchEvent(new Event("visibilitychange"));
  }, visible);
}

test("Evidence lists metadata and pages locally before opening exact saved text", async ({
  page,
}) => {
  const cursor = "synthetic +/next-page";
  const requests = await evidenceFixture(page, (url) =>
    url.searchParams.has("cursor")
      ? ready([anotherSource])
      : ready([source], cursor),
  );
  const table = page.getByRole("table", { name: "Saved source versions" });
  await expect(
    page.getByRole("button", { name: /Refresh evidence/i }),
  ).toHaveCount(0);
  await expect(
    table.getByRole("rowheader").filter({ hasText: source.title }),
  ).toBeVisible();
  await expect(table.getByText("Publication date unknown")).toBeVisible();
  await expect(page.getByLabel("Saved source content")).toHaveCount(0);
  expect(requests.openings).toHaveLength(0);
  expect(requests.listings[0].searchParams.get("limit")).toBe("25");

  await page.getByRole("button", { name: "Load more sources" }).click();
  await expect(
    table.getByRole("rowheader").filter({ hasText: anotherSource.title }),
  ).toBeVisible();
  await expect(
    table.getByRole("rowheader").filter({ hasText: source.title }),
  ).toBeVisible();
  await expect(
    table.getByText("Retrieval date unknown", { exact: true }),
  ).toBeVisible();
  expect(requests.listings[1].searchParams.get("cursor")).toBe(cursor);
  expect(requests.openings).toHaveLength(0);

  const row = table.getByRole("row").filter({ hasText: source.title });
  const open = row.getByRole("button", { name: "View saved text" });
  await open.focus();
  await page.keyboard.press("Enter");
  const content = page.getByLabel("Saved source content");
  await expect(content).toHaveText(savedText);
  await expect(content.locator("script, b")).toHaveCount(0);
  expect(await page.evaluate(() => "__evidenceExecuted" in window)).toBe(false);
  expect(requests.openings).toHaveLength(1);
  expect(requests.openings[0].pathname).toBe(
    `/api/context/sources/${source.capture_id}`,
  );
  expect(requests.openings[0].searchParams.get("content_hash")).toBe(
    source.content_hash,
  );
  expect(requests.openings[0].searchParams.get("manifest_hash")).toBe(
    source.manifest_hash,
  );
  await expect(
    page.getByRole("link", { name: "Open live source" }),
  ).toHaveAttribute("href", source.url);
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-evidence.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: join(artifacts, "maestro-evidence-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Close saved text" }).press("Enter");
  await expect(content).toHaveCount(0);
});

test("Evidence distinguishes an empty archive from disabled or unavailable storage", async ({
  page,
}) => {
  let inventory: Inventory | object = ready([]);
  const requests = await evidenceFixture(page, () => inventory);
  await expect(
    page.getByText("No saved sources yet.", { exact: true }),
  ).toBeVisible();
  for (const [state, message] of [
    ["disabled", "Source saving is disabled."],
    ["unconfigured", "No source archive is configured."],
    ["unavailable", "Source archive is unavailable."],
  ]) {
    inventory = { ...ready([]), state };
    await returnToEvidence(page);
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(
      page.getByText("No saved sources yet.", { exact: true }),
    ).toHaveCount(0);
  }
  expect(requests.openings).toHaveLength(0);
});

test("automatic revalidation removes text when the same listed version becomes unavailable", async ({
  page,
}) => {
  let revoked = false;
  const requests = await evidenceFixture(
    page,
    () => ready(),
    async () =>
      revoked
        ? {
            status: 404,
            body: { detail: "Saved source is no longer available." },
          }
        : { body: { ...source, content: savedText } },
  );
  const open = page.getByRole("button", { name: "View saved text" });
  await open.click();
  const content = page.getByLabel("Saved source content");
  await expect(content).toHaveText(savedText);
  revoked = true;
  await page.clock.fastForward(15_000);
  await expect(
    page.getByText("Saved source is no longer available.", { exact: true }),
  ).toBeVisible();
  await expect(content).toHaveCount(0);
  expect(requests.openings).toHaveLength(2);
});

test("a late saved-text response stays discarded after returning to changed evidence", async ({
  page,
}) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started!: () => void;
  const loading = new Promise<void>((resolve) => {
    started = resolve;
  });
  let revoked = false;
  let detailCalls = 0;
  let originalReturned = false;
  const requests = await evidenceFixture(
    page,
    () => (revoked ? ready([]) : ready()),
    async () => {
      detailCalls += 1;
      if (detailCalls > 1)
        return {
          status: 404,
          body: { detail: "Saved source is no longer available." },
        };
      started();
      await pending;
      originalReturned = true;
      return { body: { ...source, content: savedText } };
    },
  );
  await page.setViewportSize({ width: 375, height: 812 });
  try {
    await page.getByRole("button", { name: "View saved text" }).click();
    await loading;
    await expect(
      page.getByText("Loading saved text…", { exact: true }),
    ).toBeVisible();
    await setVisible(page, false);
    await expect(page.getByLabel("Saved source content")).toHaveCount(0);
    revoked = true;
    await setVisible(page, true);
    await expect(
      page.getByText("No saved sources yet.", { exact: true }),
    ).toBeVisible();
    expect(requests.listings).toHaveLength(2);
    await expect.poll(() => requests.openings.length).toBe(2);
    await expect(
      page.getByText("Saved source is no longer available.", { exact: true }),
    ).toBeVisible();
    expect(requests.openings[1].searchParams.get("content_hash")).toBe(
      source.content_hash,
    );
    expect(requests.openings[1].searchParams.get("manifest_hash")).toBe(
      source.manifest_hash,
    );
    release();
    await expect.poll(() => originalReturned).toBe(true);
    await page.clock.runFor(20);
    await expect(page.getByLabel("Saved source content")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  } finally {
    release();
  }
});

test("a slow inventory poll still revalidates a revoked selection when another poll is queued", async ({
  page,
}) => {
  const firstPoll = deferred();
  const nextPoll = deferred();
  let calls = 0;
  let revoked = false;
  const requests = await evidenceFixture(
    page,
    async () => {
      calls += 1;
      if (calls === 2) await firstPoll.promise;
      if (calls === 3) await nextPoll.promise;
      return ready();
    },
    async () =>
      revoked
        ? {
            status: 404,
            body: { detail: "Saved source is no longer available." },
          }
        : { body: { ...source, content: savedText } },
  );
  try {
    await page.getByRole("button", { name: "View saved text" }).click();
    await expect(page.getByLabel("Saved source content")).toHaveText(savedText);
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(2);
    // Every held response takes longer than the polling interval.
    await page.clock.fastForward(15_000);
    expect(requests.listings).toHaveLength(2);
    revoked = true;
    firstPoll.release();
    await expect.poll(() => requests.listings.length).toBe(3);
    await expect(
      page.getByText("Saved source is no longer available.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("Saved source content")).toHaveCount(0);
    expect(requests.openings).toHaveLength(2);
  } finally {
    firstPoll.release();
    nextPoll.release();
  }
});

test("Load more retains keyboard focus and ignores activation during a quiet inventory refresh", async ({
  page,
}) => {
  const poll = deferred();
  let calls = 0;
  const requests = await evidenceFixture(page, async () => {
    calls += 1;
    if (calls === 2) await poll.promise;
    return ready([source], "next-page");
  });
  const more = page.getByRole("button", { name: "Load more sources" });
  try {
    await more.focus();
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(2);
    await expect(more).toHaveAttribute("aria-disabled", "true");
    await expect(more).toHaveJSProperty("disabled", false);
    await expect(more).toBeFocused();
    await page.keyboard.press("Enter");
    await more.click({ force: true });
    expect(requests.listings).toHaveLength(2);
    await expect(more).toBeFocused();
    poll.release();
    await expect(more).not.toHaveAttribute("aria-disabled", "true");
    await expect(more).toBeFocused();
    expect(requests.listings).toHaveLength(2);
  } finally {
    poll.release();
  }
});

test("a selected version outside the refreshed page stays pinned and is checked without expanding the inventory", async ({
  page,
}) => {
  const validation = deferred();
  const quietValidation = deferred();
  const newSource = {
    ...anotherSource,
    capture_id: "c".repeat(32),
    title: "Newer report pushes the selected version onto a later page",
  };
  let changed = false;
  let detailCalls = 0;
  const requests = await evidenceFixture(
    page,
    () => ready(changed ? [newSource] : [source], "more-sources"),
    async () => {
      detailCalls += 1;
      if (detailCalls === 2) await validation.promise;
      if (detailCalls === 3) await quietValidation.promise;
      return { body: { ...source, content: savedText } };
    },
  );
  const content = page.getByLabel("Saved source content");
  const close = page.getByRole("button", { name: "Close saved text" });
  try {
    await page.getByRole("button", { name: "View saved text" }).click();
    await expect(content).toHaveText(savedText);
    await close.focus();
    changed = true;
    await page.clock.fastForward(15_000);
    await expect(
      page.getByRole("rowheader").filter({ hasText: newSource.title }),
    ).toBeVisible();
    await expect(
      page.getByRole("rowheader").filter({ hasText: source.title }),
    ).toHaveCount(0);
    await expect.poll(() => requests.openings.length).toBe(2);
    await expect(content).toHaveCount(0);
    await expect(close).toBeVisible();
    await expect(close).toBeFocused();
    expect(requests.listings).toHaveLength(2);
    expect(requests.listings[1].searchParams.has("cursor")).toBe(false);
    expect(requests.openings[1].pathname).toBe(
      `/api/context/sources/${source.capture_id}`,
    );
    expect(requests.openings[1].searchParams.get("content_hash")).toBe(
      source.content_hash,
    );
    expect(requests.openings[1].searchParams.get("manifest_hash")).toBe(
      source.manifest_hash,
    );
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(3);
    expect(requests.openings).toHaveLength(2);
    await expect(content).toHaveCount(0);
    await expect(close).toBeFocused();
    validation.release();
    await expect(content).toHaveText(savedText);
    await expect(close).toBeFocused();
    expect(requests.listings).toHaveLength(3);
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(4);
    await expect.poll(() => requests.openings.length).toBe(3);
    await expect(content).toHaveText(savedText);
    await expect(close).toBeFocused();
    quietValidation.release();
    await expect(content).toHaveText(savedText);
  } finally {
    validation.release();
    quietValidation.release();
  }
});

test("slow selected-text validation neither blocks inventory and pagination nor overlaps or starves on later polls", async ({
  page,
}) => {
  const firstValidation = deferred();
  const secondValidation = deferred();
  let detailCalls = 0;
  const requests = await evidenceFixture(
    page,
    (url) =>
      url.searchParams.has("cursor")
        ? ready([anotherSource])
        : ready([source], "next-page"),
    async () => {
      detailCalls += 1;
      if (detailCalls === 2) await firstValidation.promise;
      if (detailCalls === 3) await secondValidation.promise;
      return { body: { ...source, content: savedText } };
    },
  );
  const content = page.getByLabel("Saved source content");
  const more = page.getByRole("button", { name: "Load more sources" });
  try {
    await page.getByRole("button", { name: "View saved text" }).click();
    await expect(content).toHaveText(savedText);
    await returnToEvidence(page);
    await expect.poll(() => requests.openings.length).toBe(2);
    await expect(content).toHaveCount(0);
    await expect.poll(() => requests.listings.length).toBe(2);
    for (const expected of [3, 4]) {
      await page.clock.fastForward(15_000);
      await expect.poll(() => requests.listings.length).toBe(expected);
      expect(requests.openings).toHaveLength(2);
    }
    firstValidation.release();
    await expect(content).toHaveText(savedText);

    await returnToEvidence(page);
    await expect.poll(() => requests.openings.length).toBe(3);
    await expect.poll(() => requests.listings.length).toBe(5);
    await page.getByRole("button", { name: "Close saved text" }).click();
    await expect(content).toHaveCount(0);
    await more.click();
    await expect(
      page.getByRole("rowheader").filter({ hasText: anotherSource.title }),
    ).toBeVisible();
    expect(requests.listings[5].searchParams.get("cursor")).toBe("next-page");
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(8);
    expect(requests.openings).toHaveLength(3);
    secondValidation.release();
    await page.clock.runFor(20);
    await expect(content).toHaveCount(0);
  } finally {
    firstValidation.release();
    secondValidation.release();
  }
});

test("automatic updates follow a fresh cursor chain across all loaded pages and preserve valid selection", async ({
  page,
}) => {
  let changed = false;
  const newSource = {
    ...source,
    capture_id: "c".repeat(32),
    title: "Newly saved report",
  };
  const requests = await evidenceFixture(page, (url) => {
    const cursor = url.searchParams.get("cursor");
    if (!cursor)
      return ready(
        changed ? [newSource, source] : [source],
        changed ? "new-2" : "old-2",
      );
    if (cursor.endsWith("-2")) return ready([], changed ? "new-3" : "old-3");
    return ready([anotherSource]);
  });
  const loadMore = page.getByRole("button", { name: "Load more sources" });
  await loadMore.click();
  await expect.poll(() => requests.listings.length).toBe(2);
  await expect(loadMore).toBeEnabled();
  await loadMore.click();
  await expect(
    page.getByRole("rowheader").filter({ hasText: anotherSource.title }),
  ).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: source.title })
    .getByRole("button", { name: "View saved text" })
    .click();
  const content = page.getByLabel("Saved source content");
  await expect(content).toHaveText(savedText);
  const close = page.getByRole("button", { name: "Close saved text" });
  await close.focus();

  changed = true;
  await page.clock.fastForward(15_000);
  await expect(
    page.getByRole("rowheader").filter({ hasText: newSource.title }),
  ).toBeVisible();
  await expect(
    page.getByRole("rowheader").filter({ hasText: anotherSource.title }),
  ).toBeVisible();
  await expect.poll(() => requests.openings.length).toBe(2);
  await expect(content).toHaveText(savedText);
  await expect(close).toBeFocused();
  expect(
    requests.listings.slice(3).map((url) => url.searchParams.get("cursor")),
  ).toEqual([null, "new-2", "new-3"]);
  await expect(loadMore).toHaveCount(0);
});

test("polling and focus share one inventory request and pause when hidden or unmounted", async ({
  page,
}) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let releaseFresh!: () => void;
  const fresh = new Promise<void>((resolve) => {
    releaseFresh = resolve;
  });
  let calls = 0;
  const requests = await evidenceFixture(page, async () => {
    calls += 1;
    if (calls === 2) {
      await pending;
      return ready([anotherSource]);
    }
    if (calls === 3) await fresh;
    return ready();
  });
  await expect(
    page.getByRole("table", { name: "Saved source versions" }),
  ).toBeVisible();
  try {
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.listings.length).toBe(2);
    await returnToEvidence(page);
    await page.clock.runFor(45_000);
    expect(requests.listings).toHaveLength(2);
    await setVisible(page, false);
    await page.clock.fastForward(60_000);
    expect(requests.listings).toHaveLength(2);
    await setVisible(page, true);
    expect(requests.listings).toHaveLength(2);
    release();
    await expect.poll(() => requests.listings.length).toBe(3);
    await expect(
      page.getByRole("rowheader").filter({ hasText: anotherSource.title }),
    ).toHaveCount(0);
    releaseFresh();
    await expect(
      page.getByRole("table", { name: "Saved source versions" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Workspace", exact: true }).click();
    await page.clock.fastForward(60_000);
    await returnToEvidence(page);
    expect(requests.listings).toHaveLength(3);
  } finally {
    release();
    releaseFresh();
  }
});
