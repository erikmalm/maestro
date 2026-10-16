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

async function evidenceFixture(
  page: Page,
  inventory: (url: URL) => Inventory | object,
  detail = async (_url: URL): Promise<SourceReply> => ({
    body: { ...source, content: savedText },
  }),
) {
  const listings: URL[] = [];
  const openings: URL[] = [];
  await page.route("**/api/context/sources**", async (route) => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    if (url.pathname === "/api/context/sources") {
      listings.push(url);
      await route.fulfill({ json: inventory(url) });
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
    await page.getByRole("button", { name: "Refresh evidence" }).click();
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await expect(
      page.getByText("No saved sources yet.", { exact: true }),
    ).toHaveCount(0);
  }
  expect(requests.openings).toHaveLength(0);
});

test("refresh clears saved text and a revoked source cannot restore it", async ({
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
  await page.getByRole("button", { name: "Refresh evidence" }).click();
  await expect(content).toHaveCount(0);
  await expect(open).toBeEnabled();
  await open.click();
  await expect(
    page.getByText("Saved source is no longer available.", { exact: true }),
  ).toBeVisible();
  await expect(content).toHaveCount(0);
  expect(requests.openings).toHaveLength(2);
});

test("a late saved-text response stays discarded after refresh on a narrow screen", async ({
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
  await evidenceFixture(
    page,
    () => ready(),
    async () => {
      started();
      await pending;
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
    await page.getByRole("button", { name: "Refresh evidence" }).click();
    const response = page.waitForResponse((item) =>
      new URL(item.url()).pathname.endsWith(`/${source.capture_id}`),
    );
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise(requestAnimationFrame));
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
