import { expect, test, type Page } from "@playwright/test";

const originalConfig = {
  enabled: false,
  capture_policy: "approved_sources",
  public_sources: ["https://reports.example.org/approved"],
  reuse_hours: 31,
  max_bytes: 123456789,
  max_items: 2345,
};
const initial = {
  configured: true,
  available: false,
  database_path: "C:\\Private synthetic fixture\\workspace.sqlite3",
  index_path: "C:\\Private synthetic fixture\\context\\index.sqlite3",
  path: "C:\\Synthetic archive with a long directory name\\saved-public-source-evidence\\archive",
  path_error: null,
  config: originalConfig,
  config_error: null,
  indexed_count: 0,
  archive_bytes: 0,
  archive_items: 0,
  missing_count: 0,
  corrupt_count: 0,
  last_indexed_at: null,
  last_capture: null,
  last_error: null,
};
type Archive = Omit<
  typeof initial,
  | "config"
  | "config_error"
  | "path_error"
  | "path"
  | "last_error"
  | "last_capture"
> & {
  config: typeof originalConfig | null;
  config_error: string | null;
  path_error: string | null;
  path: string | null;
  last_error: string | null;
  last_capture: { retrieved_at: string } | null;
};
type Reply = { body: Archive | { detail: string }; status?: number };

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function archiveFixture(
  page: Page,
  read: () => Reply | Promise<Reply>,
  save = async (_body: object): Promise<Reply> => ({ body: initial }),
) {
  const requests = { reads: 0, writes: [] as object[] };
  await page.clock.install();
  await page.route("**/api/context", async (route) => {
    let reply;
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      requests.writes.push(body);
      reply = await save(body);
    } else {
      expect(route.request().method()).toBe("GET");
      requests.reads += 1;
      reply = await read();
    }
    await route.fulfill({ status: reply.status ?? 200, json: reply.body });
  });
  await page.route("**/api/context/sources?*", (route) =>
    route.fulfill({
      json: {
        state: "ready",
        sources: [],
        next_cursor: null,
        skipped_count: 0,
      },
    }),
  );
  await page.goto("/");
  return requests;
}

const archiveCard = (page: Page) =>
  page.getByRole("region", { name: "Source archive", exact: true });
const focusReturn = (page: Page) =>
  page.evaluate(() => window.dispatchEvent(new Event("focus")));

test("archive paths and zero counts connect Evidence to settings; keyboard toggles send only the enabled flag", async ({
  page,
}) => {
  let server: Archive = {
    ...structuredClone(initial),
    last_capture: { retrieved_at: "2026-10-16T14:30:00Z" },
  };
  const requests = await archiveFixture(
    page,
    () => ({ body: server }),
    async (body) => {
      server = {
        ...server,
        available: true,
        config: { ...server.config!, ...body },
      };
      return { body: server };
    },
  );
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  const card = archiveCard(page);
  await expect(card.getByText(initial.path, { exact: true })).toBeVisible();
  await expect(
    card.getByText(initial.database_path, { exact: true }),
  ).toHaveCount(0);
  await expect(
    card.getByText("Recorded logical archive bytes", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("No saved sources yet.", { exact: true }),
  ).toBeVisible();
  await expect(card.getByRole("button", { name: /source saving/ })).toHaveCount(
    0,
  );
  await card.getByRole("button", { name: "Open archive settings" }).click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText(initial.database_path, { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText(initial.index_path, { exact: true }),
  ).toBeVisible();
  await expect(
    card
      .locator("dl > div")
      .filter({ has: page.getByText("Last capture", { exact: true }) })
      .locator("dd"),
  ).not.toHaveText("Not recorded");
  for (const label of [
    "Recorded logical archive bytes",
    "Archive items",
    "Indexed sources",
    "Missing records",
    "Corrupt records",
  ]) {
    await expect(
      card
        .locator("dl > div")
        .filter({ has: page.getByText(label, { exact: true }) })
        .locator("dd"),
    ).toHaveText("0");
  }
  await expect(card.getByRole("textbox")).toHaveCount(0);
  // Simulate a separate client changing permissions and limits after this page loaded.
  server = {
    ...server,
    config: {
      ...originalConfig,
      public_sources: ["https://club.example.org/results"],
      capture_policy: "all_public",
      reuse_hours: 47,
      max_bytes: 987654321,
      max_items: 8765,
    },
  };
  const expectedConfig = { ...server.config, enabled: true };
  const enable = card.getByRole("button", { name: "Enable source saving" });
  await expect(enable).toBeEnabled(); // A configured, not-yet-created archive can be enabled.
  await enable.focus();
  await page.keyboard.press("Enter");
  await expect(
    card.getByText("Source saving enabled.", { exact: true }),
  ).toBeVisible();
  expect(requests.writes).toEqual([{ enabled: true }]);
  expect(server.config).toEqual(expectedConfig);
  await expect(
    card.getByText("All eligible public sources", { exact: true }),
  ).toBeVisible();
  await card.getByRole("button", { name: "Disable source saving" }).click();
  await expect(
    card.getByText("Source saving disabled.", { exact: true }),
  ).toBeVisible();
  expect(requests.writes[1]).toEqual({ enabled: false });
  expect(server.config).toEqual({ ...expectedConfig, enabled: false });
  await expect(
    card.getByText(/MAESTRO_DATA_DIR.*MAESTRO_CONTEXT_ARCHIVE_DIR/),
  ).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(requests.reads).toBeGreaterThanOrEqual(2);
});

test("archive failures recover on focus, while invalid startup paths prevent enabling", async ({
  page,
}) => {
  let reply: Reply = {
    status: 503,
    body: { detail: "Local archive status is unavailable." },
  };
  const requests = await archiveFixture(page, () => reply);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = archiveCard(page);
  await expect(card.getByRole("alert")).toHaveText(
    "Local archive status is unavailable.",
  );
  await expect(card.getByRole("button")).toHaveCount(0);
  reply = {
    body: {
      ...initial,
      configured: false,
      path: null,
      path_error: "The source archive startup path is invalid.",
    },
  };
  await focusReturn(page);
  await expect(card.getByRole("alert")).toHaveText(
    "The source archive startup path is invalid.",
  );
  await expect(
    card.getByRole("button", { name: "Enable source saving" }),
  ).toBeDisabled();
  reply = {
    body: {
      ...initial,
      available: false,
      config: { ...originalConfig, enabled: true },
      last_error: "The archive is currently unavailable.",
    },
  };
  await focusReturn(page);
  await expect(
    card.getByText("The archive is currently unavailable.", { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByRole("button", { name: "Disable source saving" }),
  ).toBeEnabled();
  await expect(
    card.getByRole("button", { name: /refresh|retry|update/i }),
  ).toHaveCount(0);
  expect(requests.writes).toHaveLength(0);
});

test("a late safe snapshot cannot restore controls after settings are withheld", async ({
  page,
}) => {
  const stale = deferred();
  let reads = 0;
  const hidden: Archive = {
    ...initial,
    config: null,
    config_error:
      "Archive settings are hidden because a saved source scope matches a current credential.",
  };
  const requests = await archiveFixture(page, async () => {
    reads += 1;
    if (reads === 2) {
      await stale.promise;
      return { body: initial };
    }
    return { body: reads > 2 ? hidden : initial };
  });
  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const card = archiveCard(page);
    await expect(
      card.getByRole("button", { name: "Enable source saving" }),
    ).toBeVisible();
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.reads).toBe(2);
    await focusReturn(page);
    await expect(
      card.getByRole("button", { name: /source saving/ }),
    ).toHaveCount(0);
    await expect(card.getByRole("alert")).toHaveText(hidden.config_error!);
    stale.release();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(
      card.getByRole("button", { name: /source saving/ }),
    ).toHaveCount(0);
    expect(requests.writes).toHaveLength(0);
  } finally {
    stale.release();
  }
});

test("a withheld save receipt clears controls without submitting retained permissions", async ({
  page,
}) => {
  let server: Archive = initial;
  const requests = await archiveFixture(
    page,
    () => ({ body: server }),
    async () => ({ body: server }),
  );
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = archiveCard(page);
  await expect(
    card.getByRole("button", { name: "Enable source saving" }),
  ).toBeVisible();
  server = {
    ...initial,
    config: null,
    config_error: "Archive settings are hidden.",
  };
  await card.getByRole("button", { name: "Enable source saving" }).click();
  await expect(card.getByRole("alert")).toHaveText(
    "Archive settings are hidden.",
  );
  await expect(card.getByRole("button", { name: /source saving/ })).toHaveCount(
    0,
  );
  expect(requests.writes).toEqual([{ enabled: true }]);
});

test("navigation fences a pending status read, and a committed write is reread before publishing", async ({
  page,
}) => {
  const statusRead = deferred();
  const write = deferred();
  let reads = 0;
  let holdRead = true;
  let server: Archive = initial;
  const requests = await archiveFixture(
    page,
    async () => {
      reads += 1;
      if (reads === 2 && holdRead) await statusRead.promise;
      return { body: server };
    },
    async (body) => {
      await write.promise;
      server = {
        ...server,
        available: true,
        config: { ...server.config!, ...body },
      };
      return { body: server };
    },
  );
  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const card = archiveCard(page);
    await expect(
      card.getByRole("button", { name: "Enable source saving" }),
    ).toBeVisible();
    await page.clock.fastForward(15_000);
    await expect.poll(() => requests.reads).toBe(2);
    await page.getByRole("button", { name: "Memory", exact: true }).click();
    statusRead.release();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect(requests.writes).toHaveLength(0);
    holdRead = false;
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await card.getByRole("button", { name: "Enable source saving" }).click();
    await expect.poll(() => requests.writes.length).toBe(1);
    await page.getByRole("button", { name: "Evidence", exact: true }).click();
    await expect(
      card.getByText("Loading source archive…", { exact: true }),
    ).toBeVisible();
    write.release();
    await expect(card.getByText("Enabled", { exact: true })).toBeVisible();
    await expect(
      card.getByText("Source saving enabled.", { exact: true }),
    ).toHaveCount(0);
    await expect(
      card.getByRole("button", { name: /source saving/ }),
    ).toHaveCount(0);
    expect(requests.writes).toHaveLength(1);
  } finally {
    statusRead.release();
    write.release();
  }
});
