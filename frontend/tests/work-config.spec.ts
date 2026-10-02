import { expect, test } from "@playwright/test";
import type { Workspace } from "../src/api";

let restore:
  { workspace: Workspace; headers: { "X-Maestro-CSRF": string } } | undefined;

test.afterEach(async ({ page }) => {
  if (!restore) return;
  const { workspace, headers } = restore;
  restore = undefined;
  const provider = await page.request.put("/api/provider", {
    headers,
    data: { config: workspace.provider.config, api_key: "", persist: false },
  });
  const work = await page.request.put("/api/work-config", {
    headers,
    data: workspace.work!.config,
  });
  expect(provider.ok()).toBeTruthy();
  expect(work.ok()).toBeTruthy();
  if (workspace.provider.tested_at)
    expect(
      (
        await page.request.post("/api/provider/test", { headers, data: {} })
      ).ok(),
    ).toBeTruthy();
});

test("task models and work limits persist without starting inference", async ({
  page,
}) => {
  await page.goto("/");
  const session = await page.request.get("/api/session");
  expect(session.ok()).toBeTruthy();
  const { csrf } = await session.json();
  const workspace = await page.request.get("/api/workspace");
  expect(workspace.ok()).toBeTruthy();
  const initial: Workspace = await workspace.json();
  const headers = { "X-Maestro-CSRF": csrf };
  restore = { workspace: initial, headers };
  const fixture = await (
    await page.request.get("/api/provider-fixture")
  ).json();
  const section = page.locator("section").filter({
    has: page.getByRole("heading", {
      name: "Task models & reflection",
      exact: true,
    }),
  });
  const selections = [
    ["Reflection model", "synthetic-devstral:latest"],
    ["Memory extraction model", "synthetic-ollama:latest"],
    ["Coding model", "synthetic-devstral:latest"],
  ] as const;
  const limits = [
    ["Reflection output tokens", "256"],
    ["Daily reflection jobs", "4"],
    ["Daily reflection tokens", "3000"],
    ["Debounce (seconds)", "90"],
    ["Chat idle time (seconds)", "45"],
    ["Job timeout (seconds)", "120"],
    ["Memories per reply", "2"],
    ["Memory context characters", "400"],
  ] as const;

  expect(
    (
      await page.request.put("/api/provider", {
        headers,
        data: {
          config: {
            ...initial.provider.config,
            base_url: fixture.ollama_url,
            protocol: "ollama",
            model: "synthetic-ollama:latest",
            orchestrator_model: "",
            input_usd_per_million: 0,
            output_usd_per_million: 0,
            pricing_verified: true,
          },
          api_key: "",
          persist: false,
        },
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (await page.request.post("/api/provider/test", { headers, data: {} })).ok(),
  ).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(section.getByText("Not running", { exact: true })).toBeVisible();
  await expect(
    section.getByLabel("Enable background reflection"),
  ).toBeDisabled();
  for (const [label, model] of selections)
    await section
      .getByRole("combobox", { name: label, exact: true })
      .selectOption(model);
  await section
    .getByText("Work limits & memory recall", { exact: true })
    .click();
  for (const [label, value] of limits)
    await section.getByLabel(label, { exact: true }).fill(value);
  await section
    .getByRole("button", { name: "Save task settings", exact: true })
    .click();
  await expect(section.getByRole("status")).toHaveText("Task settings saved.");
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  for (const [label, model] of selections)
    await expect(
      section.getByRole("combobox", { name: label, exact: true }),
    ).toHaveValue(model);
  await section
    .getByText("Work limits & memory recall", { exact: true })
    .click();
  for (const [label, value] of limits)
    await expect(section.getByLabel(label, { exact: true })).toHaveValue(value);
  const after = await (await page.request.get("/api/workspace")).json();
  expect(after.work.config.enabled).toBe(false);
  expect(after.work.worker_available).toBe(false);
  expect(after.usage).toEqual(initial.usage);
  expect(after.chats).toEqual(initial.chats);

  await page.route("**/api/workspace*", (route) =>
    route.fulfill({
      json: { ...after, provider: { ...after.provider, models: [] } },
    }),
  );
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    section.getByRole("combobox", { name: "Reflection model", exact: true }),
  ).toHaveValue("synthetic-devstral:latest");
  await expect(
    section
      .getByRole("combobox", { name: "Reflection model", exact: true })
      .locator("option:checked"),
  ).toHaveText("synthetic-devstral:latest (unavailable)");
});
