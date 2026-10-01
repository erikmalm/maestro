import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";

const artifacts = join(
  process.env.LOCALAPPDATA || join(homedir(), ".local", "share"),
  "Maestro",
  "preview",
  "artifacts",
);

test("to-dos and budgets persist without model calls", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.00");
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await page.getByLabel("Per day", { exact: true }).fill("6");
  await page.getByRole("button", { name: "Save limits" }).click();
  await page.reload();
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("6");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByLabel("What would you like to do?")
    .fill("Synthetic persistent to-do");
  await page
    .getByLabel("Context & completion criteria")
    .fill("A real local storage task.");
  await page.getByRole("button", { name: "Save task" }).click();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await page
    .getByRole("button", {
      name: "Complete Synthetic persistent to-do",
      exact: true,
    })
    .click();
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    page.getByRole("button", {
      name: "Reopen Synthetic persistent to-do",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Delete Synthetic persistent to-do",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Synthetic persistent to-do", { exact: true }),
  ).not.toBeVisible();
});

test("connection setup, HTTP chat, conversation context and usage", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save connection", exact: true }),
  ).toBeDisabled();
  const fixture = await (
    await page.request.get("/api/provider-fixture")
  ).json();
  await page.getByLabel("API base URL", { exact: true }).fill(fixture.base_url);
  await page
    .getByLabel("API key", { exact: true })
    .fill("synthetic-browser-key");
  await page
    .getByLabel("Save a new key in Windows Credential Manager")
    .uncheck();
  await page
    .getByRole("button", { name: "Connect and load models", exact: true })
    .click();
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
  await expect(
    page.getByText(
      "API key accepted. Choose a chat model and save; sending a message verifies generation.",
    ),
  ).toBeVisible();
  await expect(page.locator("#provider-models option")).toHaveAttribute(
    "value",
    "synthetic-browser-model",
  );
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("synthetic-browser-model");
  await expect(
    page.getByRole("button", { name: "Save connection", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Input USD / 1M tokens", { exact: true }).fill("1");
  await page.getByLabel("Output USD / 1M tokens", { exact: true }).fill("2");
  await page.getByLabel("Use these prices for cost estimates").check();
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Model ID", { exact: true })).toHaveValue(
    "synthetic-browser-model",
  );
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
  await expect(
    page.getByLabel("Available models", { exact: true }),
  ).toHaveValue("synthetic-browser-model");
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-provider-settings.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await page
    .getByLabel("Message Maestro")
    .fill("Remember the codeword JUNIPER.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(
      "Synthetic HTTP provider received: Remember the codeword JUNIPER.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0014");
  await page.getByLabel("Message Maestro").fill("What was the codeword?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0028");
  await page.screenshot({
    path: join(artifacts, "maestro-provider-chat.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByText("2,000", { exact: true })).toBeVisible();
  await expect(page.getByText("400", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0028");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByRole("button", { name: "Remove API key", exact: true })
    .click();
  await expect(page.getByText("No API key", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
  ).not.toContain("synthetic-browser-key");
});

test("OpenAI model choice loads prices without a paid request", async ({
  page,
}) => {
  const config = {
    base_url: "https://api.openai.com/v1",
    protocol: "responses",
    model: "",
    input_usd_per_million: 0,
    output_usd_per_million: 0,
    pricing_verified: false,
    max_output_tokens: 1024,
  };
  await page.route("**/api/provider", (route) =>
    route.fulfill({
      json: {
        config,
        credentials_present: true,
        credential_source: "session",
        models: ["gpt-6-luna", "specialized-image-model", "constructor"],
        tested_at: new Date().toISOString(),
      },
    }),
  );
  await page.goto("/");
  // Keep the dated price fixture within its documented 30-day freshness window.
  await page.clock.setFixedTime(new Date("2026-10-02T12:00:00Z"));
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("gpt-6-luna");
  await expect(
    page.getByLabel("Input USD / 1M tokens", { exact: true }),
  ).toHaveValue("0.1");
  await expect(
    page.getByLabel("Output USD / 1M tokens", { exact: true }),
  ).toHaveValue("0.5");
  await expect(
    page.getByLabel("Use these prices for cost estimates"),
  ).toBeChecked();
  await expect(
    page.getByText(/Standard OpenAI rates filled automatically/),
  ).toBeVisible();
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("specialized-image-model");
  await expect(
    page.getByLabel("Use these prices for cost estimates"),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Input USD / 1M tokens", { exact: true }),
  ).toHaveValue("0");
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("constructor");
  await expect(
    page.getByLabel("Use these prices for cost estimates"),
  ).not.toBeChecked();
  // Stale price snapshots must not silently enable chat with outdated estimates.
  await page.clock.setFixedTime(new Date("2026-12-01T12:00:00Z"));
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("gpt-6-luna");
  await expect(
    page.getByLabel("Use these prices for cost estimates"),
  ).not.toBeChecked();
});

test("mobile chat, settings and navigation fit the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Model connection" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: join(artifacts, "maestro-provider-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
});

test("local Ollama chat needs no key or USD budget and records native tokens", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByLabel("Model provider", { exact: true })
    .selectOption("ollama");
  await expect(page.getByLabel("API key", { exact: true })).toHaveCount(0);
  await expect(
    page.getByLabel("Save a new key in Windows Credential Manager"),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Input USD / 1M tokens", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("Output USD / 1M tokens", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(/Local inference has \$0 provider API charges/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save connection", exact: true }),
  ).toBeDisabled();
  const fixture = await (
    await page.request.get("/api/provider-fixture")
  ).json();
  await page
    .getByLabel("API base URL", { exact: true })
    .fill(fixture.ollama_url);
  await page
    .getByRole("button", { name: "Connect and load models", exact: true })
    .click();
  await expect(
    page.getByLabel("Available models", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("synthetic-ollama:latest");
  await page.getByText("Local worker settings", { exact: true }).click();
  await expect(page.getByLabel("Context size (tokens)")).toHaveValue("4096");
  await expect(page.getByLabel("CPU threads (0 = automatic)")).toHaveValue("0");
  await expect(page.getByLabel("Keep model loaded (minutes)")).toHaveValue("5");
  await page.getByLabel("Context size (tokens)").fill("8192");
  await page.getByLabel("CPU threads (0 = automatic)").fill("2");
  await page.getByLabel("Keep model loaded (minutes)").fill("0");
  await expect(
    page.getByRole("button", { name: "Save connection", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await expect(
    page.getByText(
      "Chat model settings saved. Return to Workspace to send a message.",
    ),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByLabel("Available models", { exact: true }),
  ).toHaveValue("synthetic-ollama:latest");
  await page.getByText("Local worker settings", { exact: true }).click();
  await expect(page.getByLabel("Context size (tokens)")).toHaveValue("8192");
  await expect(page.getByLabel("CPU threads (0 = automatic)")).toHaveValue("2");
  await expect(page.getByLabel("Keep model loaded (minutes)")).toHaveValue("0");
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-ollama-settings.png"),
    fullPage: true,
    animations: "disabled",
  });

  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/chat") &&
        response.request().method() === "DELETE" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "New chat", exact: true }).click(),
  ]);
  const usageButton = page.getByRole("button", {
    name: "View usage and manage budgets",
  });
  await usageButton.click();
  for (const label of ["Per run", "Per day", "Per month"]) {
    await page.getByLabel(label, { exact: true }).fill("0");
  }
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(
    page.getByRole("button", { name: "Close dialog" }),
  ).not.toBeVisible();
  const before = (await (await page.request.get("/api/workspace")).json())
    .usage;
  const previousCostLabel = (await usageButton.innerText()).match(
    /\$\d+(?:\.\d+)?/,
  )![0];
  await page.getByLabel("Message Maestro").fill("Remember codeword CEDAR");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(/^Synthetic Ollama received: Remember codeword CEDAR/),
  ).toBeVisible();
  await page.getByLabel("Message Maestro").fill("What was the codeword?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(/Earlier message: Remember codeword CEDAR/),
  ).toBeVisible();
  await expect(
    page.getByText("100 input · 20 output tokens · $0.00 estimated", {
      exact: true,
    }),
  ).toHaveCount(2);
  await expect(usageButton).toContainText(previousCostLabel);
  const after = (await (await page.request.get("/api/workspace")).json()).usage;
  expect(after.today_usd).toBe(before.today_usd);
  expect(after.month_usd).toBe(before.month_usd);
  expect(after.input_tokens).toBe(before.input_tokens + 200);
  expect(after.output_tokens).toBe(before.output_tokens + 40);
  expect(after.calls).toBe(before.calls + 2);
  await page.reload();
  await expect(
    page.getByText(/Earlier message: Remember codeword CEDAR/),
  ).toBeVisible();
  await expect(usageButton).toContainText(previousCostLabel);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Model provider", { exact: true })).toHaveValue(
    "ollama",
  );
  await expect(page.getByLabel("Model ID", { exact: true })).toHaveValue(
    "synthetic-ollama:latest",
  );
  await expect(page.getByLabel("API key", { exact: true })).toHaveCount(0);
});

test("verified search key enables bounded local-model search with persistent sources", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Model provider", { exact: true })).toHaveValue(
    "ollama",
  );
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).toBeDisabled();
  const searchKey = "synthetic-browser-search-key";
  await page
    .getByLabel("Ollama search API key", { exact: true })
    .fill(searchKey);
  await page
    .getByLabel("Save search key in Windows Credential Manager")
    .uncheck();
  await page.getByLabel("Daily search cap").fill("2");
  await page.getByLabel("Results per search").fill("1");
  await page
    .getByRole("button", { name: "Save search settings", exact: true })
    .click();
  await expect(
    page.getByLabel("Ollama search API key", { exact: true }),
  ).toHaveValue("");
  await expect(
    page.getByText("Search settings saved.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Test search connection", exact: true })
    .click();
  await expect(
    page.getByText(
      "Search connection verified. The test counted toward your daily cap.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).toBeEnabled();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).not.toBeChecked();
  const tested = await (await page.request.get("/api/workspace")).json();
  expect(tested.web_search.config.enabled).toBe(false);
  expect(tested.web_search.searches_today).toBe(1);
  expect(tested.web_search).not.toHaveProperty("credentials_present");
  expect(JSON.stringify(tested)).not.toContain(searchKey);
  await page.getByLabel("Let Maestro decide when to search").check();
  await page
    .getByRole("button", { name: "Save search settings", exact: true })
    .click();
  await expect(
    page.getByText("Search settings saved.", { exact: true }),
  ).toBeVisible();
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-search-settings.png"),
    fullPage: true,
    animations: "disabled",
  });

  await page.getByRole("button", { name: "Workspace", exact: true }).click();
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/chat") &&
        response.request().method() === "DELETE" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "New chat", exact: true }).click(),
  ]);
  const before = await (await page.request.get("/api/workspace")).json();
  await page
    .getByLabel("Message Maestro")
    .fill("Find current Ollama web search documentation");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("Synthetic search-assisted answer [1]", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Search query: Ollama official web search documentation", {
      exact: true,
    }),
  ).toBeVisible();
  const source = page.getByRole("link", {
    name: "Ollama web search documentation",
    exact: true,
  });
  await expect(source).toHaveAttribute(
    "href",
    "https://docs.ollama.com/capabilities/web-search",
  );
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(source).toHaveAttribute("rel", "noopener noreferrer");
  await expect(
    page.getByText("200 input · 30 output tokens · $0.00 estimated", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/Automatic web search.*2 \/ 2 today/),
  ).toBeVisible();
  const after = await (await page.request.get("/api/workspace")).json();
  expect(after.usage.input_tokens).toBe(before.usage.input_tokens + 200);
  expect(after.usage.output_tokens).toBe(before.usage.output_tokens + 30);
  expect(after.usage.today_usd).toBe(before.usage.today_usd);
  expect(after.web_search.config.enabled).toBe(true);
  expect(after.web_search.searches_today).toBe(2);
  expect(after.web_search.remaining_today).toBe(0);
  expect(JSON.stringify(after)).not.toContain(searchKey);
  expect(
    await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
  ).not.toContain(searchKey);
  await page.reload();
  await expect(
    page.getByText("Synthetic search-assisted answer [1]", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Search query: Ollama official web search documentation", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(source).toHaveAttribute(
    "href",
    "https://docs.ollama.com/capabilities/web-search",
  );
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(
    page.getByText("Web searches today", { exact: true }).locator(".."),
  ).toContainText("2 / 2");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).toBeChecked();
  await page
    .getByRole("button", { name: "Remove search key", exact: true })
    .click();
  await expect(
    page.getByText("Search key removed. Automatic search is disabled.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("Let Maestro decide when to search"),
  ).toBeDisabled();
  await expect(page.getByLabel("Model ID", { exact: true })).toHaveValue(
    "synthetic-ollama:latest",
  );
  await expect(page.getByLabel("API key", { exact: true })).toHaveCount(0);
  const removed = await (await page.request.get("/api/workspace")).json();
  expect(removed.web_search.config.enabled).toBe(false);
  expect(removed.provider.credentials_required).toBe(false);
});
