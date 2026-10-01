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
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
  await page
    .getByRole("button", { name: "Test connection", exact: true })
    .click();
  await expect(
    page.getByText(
      "Model-list request succeeded. Send a chat message to verify generation.",
    ),
  ).toBeVisible();
  await expect(page.locator("#provider-models option")).toHaveAttribute(
    "value",
    "synthetic-browser-model",
  );
  await page
    .getByLabel("Available models", { exact: true })
    .selectOption("synthetic-browser-model");
  await page.getByLabel("Input USD / 1M tokens", { exact: true }).fill("1");
  await page.getByLabel("Output USD / 1M tokens", { exact: true }).fill("2");
  await page.getByLabel("I verified these prices for this model").check();
  await page
    .getByRole("button", { name: "Save connection", exact: true })
    .click();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Model ID", { exact: true })).toHaveValue(
    "synthetic-browser-model",
  );
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
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
