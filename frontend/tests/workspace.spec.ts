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

test("empty workspace, manual tasks and budgets persist without model calls", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Start a conversation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).not.toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button"),
  ).toHaveCount(4);
  const initial = await (await page.request.get("/api/workspace")).json();
  expect(initial.tasks).toEqual([]);
  expect(initial.messages).toEqual([]);
  expect(initial.chats).toEqual([]);
  expect(initial.active_chat_id).toBeNull();
  expect(initial.usage.calls).toBe(0);
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-workspace.png"),
    fullPage: true,
    animations: "disabled",
  });
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.00");
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeDisabled();
  await page.getByLabel("Message Maestro").fill("No model configured");
  await page.getByLabel("Message Maestro").press("Enter");
  await page.getByLabel("Message Maestro").fill("");
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Per day", { exact: true }).fill("6");
  await dialog.getByRole("button", { name: "Save limits" }).click();
  await page.reload();
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(dialog.getByLabel("Per day", { exact: true })).toHaveValue("6");
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Per day", { exact: true }).fill("6.00");
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("6");
  await page.getByLabel("Per chat request", { exact: true }).fill("2");
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await dialog.getByLabel("Per chat request", { exact: true }).fill("3");
  await dialog.getByLabel("Per day", { exact: true }).fill("9");
  await dialog.getByLabel("Tokens per chat request").fill("110000");
  await dialog.getByRole("button", { name: "Save limits" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("9");
  await expect(page.getByLabel("Tokens per chat request")).toHaveValue(
    "110000",
  );
  await expect(
    page.getByLabel("Per chat request", { exact: true }),
  ).toHaveValue("2");
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(page.getByRole("button", { name: "Save limits" })).toBeEnabled();
  const savedLimits = await (await page.request.get("/api/workspace")).json();
  expect(savedLimits.limits).toEqual({
    ...initial.limits,
    run_usd: 2,
    daily_usd: 9,
    max_tokens: 110000,
  });
  await page.getByLabel("Per chat request", { exact: true }).fill("1");
  await page.getByLabel("Per day", { exact: true }).fill("6");
  await page.getByLabel("Tokens per chat request").fill("100000");
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(page.getByRole("button", { name: "Save limits" })).toBeEnabled();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    page.getByRole("heading", { name: "No tasks yet" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByLabel("What would you like to do?")
    .fill("Synthetic persistent to-do");
  await page
    .getByLabel("Context & completion criteria")
    .fill("A real local storage task.");
  await page
    .getByRole("combobox", { name: "Priority", exact: true })
    .selectOption("high");
  await page.getByRole("button", { name: "Save task" }).click();
  await expect(
    page.getByText("A real local storage task.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("High priority", { exact: true })).toBeVisible();
  await page.screenshot({
    path: join(artifacts, "maestro-tasks.png"),
    fullPage: true,
    animations: "disabled",
  });
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
  const after = await (await page.request.get("/api/workspace")).json();
  expect(after.tasks).toEqual([]);
  expect(after.chats).toEqual([]);
  expect(after.messages).toEqual([]);
  expect(after.usage).toEqual(initial.usage);
});

test("connection setup, named persistent chats, isolated context and usage", async ({
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
  // Saving or testing the provider refreshes workspace accounting, not unsaved limit fields.
  await page.getByLabel("Per day", { exact: true }).fill("7");
  const refreshed = page.waitForResponse(
    (response) => response.url().includes("/api/workspace") && response.ok(),
  );
  await page
    .getByRole("button", { name: "Connect and load models", exact: true })
    .click();
  await refreshed;
  await expect(
    page.getByText(
      "API key accepted. Choose a chat model and save; sending a message verifies generation.",
    ),
  ).toBeVisible();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("7");
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
  const firstSend = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/chat") && request.method() === "POST",
  );
  let failRefresh = true;
  await page.route("**/api/**", async (route) => {
    if (
      failRefresh &&
      new URL(route.request().url()).pathname === "/api/workspace"
    ) {
      failRefresh = false;
      await route.fulfill({
        status: 500,
        json: { detail: "Refresh interrupted." },
      });
    } else await route.continue();
  });
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(
      "Synthetic HTTP provider received: Remember the codeword JUNIPER.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByLabel("Message Maestro")).toHaveValue("");
  await page.unroute("**/api/**");
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0015");
  await expect(
    page.getByRole("heading", { name: "Juniper codeword", exact: true }),
  ).toBeVisible();
  const juniperId = new URL(page.url()).searchParams.get("chat");
  expect(juniperId).toBeTruthy();
  expect((await firstSend).postDataJSON().chat_id).toBe(juniperId);
  let releaseReply!: () => void;
  const replyHeld = new Promise<void>((resolve) => {
    releaseReply = resolve;
  });
  await page.route("**/api/chat", async (route) => {
    await replyHeld;
    await route.continue();
  });
  await page.getByLabel("Message Maestro").fill("What was the codeword?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeDisabled();
  await page
    .getByLabel("Message Maestro")
    .fill("Draft written while awaiting the reply");
  releaseReply();
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  await expect(page.getByLabel("Message Maestro")).toHaveValue(
    "Draft written while awaiting the reply",
  );
  await page.unroute("**/api/chat");
  await page.reload();
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0029");
  await page.screenshot({
    path: join(artifacts, "maestro-provider-chat.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByText("2,100", { exact: true })).toBeVisible();
  await expect(page.getByText("405", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByLabel("Message Maestro").fill("Unsent Juniper draft");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "New chat", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Message Maestro")).toHaveValue("");
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.0029");
  await expect(
    page.getByRole("button", {
      name: "Open chat: Juniper codeword",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByLabel("Message Maestro")
    .fill("Keep this conversation separate.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText(
      "Synthetic HTTP provider received: Keep this conversation separate.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Independent conversation",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByLabel("Message Maestro").fill("Unsent independent draft");
  await page
    .getByRole("button", { name: "Open chat: Juniper codeword", exact: true })
    .click();
  await expect(page.getByLabel("Message Maestro")).toHaveValue("");
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("chat")).toBe(juniperId);
  // Saving settings must preserve an older selected thread even when mutations return the latest one.
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await page.getByRole("button", { name: "Save limits", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Close dialog" }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Juniper codeword", exact: true }),
  ).toBeVisible();
  const beforeMetadata = await (
    await page.request.get("/api/workspace")
  ).json();
  expect(beforeMetadata.usage.input_tokens).toBe(3200);
  expect(beforeMetadata.usage.output_tokens).toBe(610);
  expect(beforeMetadata.usage.calls).toBe(5);
  expect(beforeMetadata.chats).toHaveLength(2);
  await page.getByRole("button", { name: "Rename chat", exact: true }).click();
  await page.getByLabel("Chat title", { exact: true }).fill("Juniper notes");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Juniper notes", exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("chat")).toBe(juniperId);
  await expect(
    page.getByText(/Earlier message: Remember the codeword JUNIPER/),
  ).toBeVisible();
  await page.screenshot({
    path: join(artifacts, "maestro-chat-threads.png"),
    fullPage: true,
    animations: "disabled",
  });
  await page
    .getByRole("button", {
      name: "Open chat: Independent conversation",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Independent conversation",
      exact: true,
    }),
  ).toBeVisible();
  const deletedId = new URL(page.url()).searchParams.get("chat");
  await page.getByRole("button", { name: "Delete chat", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Delete chat", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Juniper notes", exact: true }),
  ).toBeVisible();
  const afterMetadata = await (await page.request.get("/api/workspace")).json();
  expect(afterMetadata.chats).toHaveLength(1);
  expect(afterMetadata.usage).toEqual(beforeMetadata.usage);
  await expect(
    page.getByRole("button", {
      name: "Open chat: Independent conversation",
      exact: true,
    }),
  ).not.toBeVisible();
  await page.goto(`/?chat=${deletedId}`);
  await expect(
    page.getByRole("heading", { name: "Juniper notes", exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("chat")).toBe(juniperId);
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
  await page.route("**/api/workspace*", async (route) => {
    const response = await route.fetch();
    const workspace = await response.json();
    await route.fulfill({
      response,
      json: {
        ...workspace,
        provider: {
          config,
          credentials_present: true,
          credential_source: "session",
          models: ["gpt-6-luna", "specialized-image-model", "constructor"],
          tested_at: new Date().toISOString(),
        },
      },
    });
  });
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
    page.getByText(/Standard OpenAI short-context rates filled automatically/),
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
  // Hidden navigation must also be removed from keyboard focus and the accessibility tree.
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).not.toBeVisible();
  const menu = page.getByRole("button", { name: "Open navigation" });
  await menu.focus();
  // Tabbing through the closed page cannot reach the off-screen sidebar.
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(() => !!document.activeElement?.closest(".sidebar")),
    ).toBe(false);
  }
  await expect(page.locator(".usage-tokens")).toBeVisible();
  await page.screenshot({
    path: join(artifacts, "maestro-workspace-mobile.png"),
    fullPage: true,
    animations: "disabled",
  });
  await menu.click();
  const drawer = page.getByRole("dialog", { name: "Navigation", exact: true });
  await expect(drawer).toBeVisible();
  await expect(
    drawer.getByRole("button", { name: "Close navigation" }),
  ).toBeFocused();
  await expect(page.locator(".main-shell")).toHaveJSProperty("inert", true);
  const drawerButtons = drawer.getByRole("button");
  await page.keyboard.press("Shift+Tab");
  await expect(drawerButtons.last()).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(drawerButtons.first()).toBeFocused();
  for (let index = 0; index < (await drawerButtons.count()) + 2; index++) {
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(() => !!document.activeElement?.closest(".sidebar")),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(menu).toBeFocused();
  await expect(page.locator(".main-shell")).toHaveJSProperty("inert", false);
  await menu.click();
  await drawer.getByRole("button", { name: "Close navigation" }).click();
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Model connection" }),
  ).toBeVisible();
  await expect(menu).toBeFocused();
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
  await expect(page.getByLabel("Context size (tokens)")).toHaveValue("32768");
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
  await expect(page.getByRole("alert")).toContainText(
    /output.*context|context.*output/i,
  );
  const unchanged = await (await page.request.get("/api/workspace")).json();
  expect(unchanged.provider.config.ollama_context_tokens).toBe(32768);
  await page.getByLabel("Maximum output tokens per reply").fill("4096");
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
        response.url().endsWith("/api/chats") &&
        response.request().method() === "POST" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "New chat", exact: true }).click(),
  ]);
  const usageButton = page.getByRole("button", {
    name: "View usage and manage budgets",
  });
  await usageButton.click();
  const dialog = page.getByRole("dialog");
  for (const label of ["Per chat request", "Per day", "Per month"]) {
    await dialog.getByLabel(label, { exact: true }).fill("0");
  }
  await dialog.getByRole("button", { name: "Save limits" }).click();
  await expect(dialog).not.toBeVisible();
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
  expect(after.input_tokens).toBe(before.input_tokens + 220);
  expect(after.output_tokens).toBe(before.output_tokens + 45);
  expect(after.calls).toBe(before.calls + 3);
  await expect(
    page.getByRole("heading", { name: "Cedar codeword", exact: true }),
  ).toBeVisible();
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

test("chat model picker preserves conversation and draft", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const fixture = await (
    await page.request.get("/api/provider-fixture")
  ).json();
  await page
    .getByLabel("Model provider", { exact: true })
    .selectOption("ollama");
  await page
    .getByLabel("API base URL", { exact: true })
    .fill(fixture.ollama_url);
  await page.getByRole("button", { name: "Connect and load models" }).click();
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("synthetic-ollama:latest");
  await page
    .getByRole("combobox", { name: "Chat routing", exact: true })
    .selectOption("orchestrator");
  await page
    .getByLabel("Orchestrator default model")
    .selectOption("synthetic-devstral:latest");
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
  await expect(page.getByLabel("Orchestrator default model")).toHaveValue(
    "synthetic-devstral:latest",
  );
  await expect(
    page.getByRole("combobox", { name: "Chat routing", exact: true }),
  ).toHaveValue("orchestrator");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(page.getByLabel("Message Maestro")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Choose model:" }),
  ).toContainText("synthetic-devstral:latest");
  const before = await (await page.request.get("/api/workspace")).json();
  await expect(
    page.getByRole("combobox", { name: "Role", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Refresh installed models" }),
  ).toHaveCount(0);
  for (const model of [
    "synthetic-ollama:latest",
    "synthetic-devstral:latest",
    "synthetic-ollama:latest",
  ]) {
    const text = `Synthetic chat request ${model}`;
    await page.getByLabel("Message Maestro").fill(text);
    await page.getByRole("button", { name: "Choose model:" }).click();
    await page
      .locator(".model-picker")
      .getByRole("button", { name: model, exact: false })
      .click();
    await expect(page.getByLabel("Message Maestro")).toHaveValue(text);
    await expect(
      page.getByRole("button", { name: "Choose model:" }),
    ).toContainText(model);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByLabel("Message Maestro")).toHaveValue("");
    const saved = await (
      await page.request.get(`/api/workspace?chat_id=${before.active_chat_id}`)
    ).json();
    expect(saved.messages.at(-1)).toMatchObject({
      role: "assistant",
      model,
      kind: "chat",
    });
    expect(saved.active_chat_id).toBe(before.active_chat_id);
    expect(saved.provider.config.orchestrator_model).toBe(
      "synthetic-devstral:latest",
    );
    expect(saved.provider.config.model).toBe("synthetic-ollama:latest");
    expect(saved.provider.config.chat_routing).toBe("orchestrator");
  }
  await expect(page.locator(".chat-message.assistant")).toHaveCount(3);
  await page
    .getByLabel("Message Maestro")
    .fill("Keep this draft while choosing models");
  await page.getByRole("button", { name: "Choose model:" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Message Maestro")).toHaveValue(
    "Keep this draft while choosing models",
  );
  await expect(page.locator(".chat-message.assistant")).toHaveCount(3);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole("button", { name: "Choose model:" }).click();
  await expect(page.locator(".model-picker")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(375);
});

test("verified search key enables bounded local-model search with persistent sources", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const fixture = await (
    await page.request.get("/api/provider-fixture")
  ).json();
  await page
    .getByLabel("Model provider", { exact: true })
    .selectOption("ollama");
  await page
    .getByLabel("API base URL", { exact: true })
    .fill(fixture.ollama_url);
  await page.getByRole("button", { name: "Connect and load models" }).click();
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("synthetic-ollama:latest");
  await page
    .getByRole("combobox", { name: "Chat routing", exact: true })
    .selectOption("orchestrator");
  await page
    .getByLabel("Orchestrator default model")
    .selectOption("synthetic-devstral:latest");
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await expect(
    page.getByText(
      "Chat model settings saved. Return to Workspace to send a message.",
      { exact: true },
    ),
  ).toBeVisible();
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
  expect(tested.web_search.credentials_present).toBe(true);
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
        response.url().endsWith("/api/chats") &&
        response.request().method() === "POST" &&
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
  await page.getByRole("button", { name: /^Search options:/ }).click();
  await expect(
    page.getByText(/Automatic web search.*2 \/ 2 today/),
  ).toBeVisible();
  const after = await (await page.request.get("/api/workspace")).json();
  expect(after.usage.input_tokens).toBe(before.usage.input_tokens + 220);
  expect(after.usage.output_tokens).toBe(before.usage.output_tokens + 35);
  expect(after.usage.calls).toBe(before.usage.calls + 3);
  await expect(
    page.getByRole("heading", {
      name: "Ollama search documentation",
      exact: true,
    }),
  ).toBeVisible();
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
