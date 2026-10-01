import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";

test("workspace layout, usage controls, tasks, and local persistence", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "What’s on your mind?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.42");
  const artifacts = join(
    process.env.LOCALAPPDATA || join(homedir(), ".local", "share"),
    "Maestro",
    "preview",
    "artifacts",
  );
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: join(artifacts, "maestro-desktop.png"),
    fullPage: true,
  });

  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Input tokens", { exact: true })).toBeVisible();
  await page.getByLabel("Per day", { exact: true }).fill("6");
  await page.getByRole("button", { name: "Save limits" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByLabel("Per day", { exact: true })).toHaveValue("6");
  await page.getByRole("button", { name: "Close dialog" }).click();

  await page.getByRole("button", { name: "New task", exact: true }).click();
  await page
    .getByLabel("What would you like to do?")
    .fill("Synthetic browser task");
  await page
    .getByLabel("Context & completion criteria")
    .fill("Verify a usable, private local task flow.");
  await page.getByRole("button", { name: "Save task" }).click();
  await expect(
    page.getByText("Synthetic browser task", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await page.getByRole("button", { name: "Preview run" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Synthetic browser task", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Simulation finished", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toContainText("$0.44");
  await page.screenshot({
    path: join(artifacts, "maestro-runs.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await page
    .getByRole("button", {
      name: "Complete Synthetic browser task",
      exact: true,
    })
    .click();
  await page.reload();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    page.getByRole("button", {
      name: "Reopen Synthetic browser task",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Synthetic browser task", exact: true })
    .click();
  await expect(
    page.getByText("Synthetic browser task", { exact: true }),
  ).not.toBeVisible();
  expect(errors).toEqual([]);
});

test("chat and manual memory stay interactive with clear preview labels", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Help me plan my week" }).click();
  await expect(
    page.getByRole("textbox", { name: "Message Maestro" }),
  ).toHaveValue("Help me plan my priorities for the week.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText(/This is a preview response/)).toBeVisible();
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await page.getByRole("button", { name: "Add memory", exact: true }).click();
  await page
    .getByLabel("What should Maestro remember?")
    .fill("Synthetic browser preference");
  await page.getByRole("button", { name: "Save memory", exact: true }).click();
  await expect(
    page.getByText("Synthetic browser preference", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Forget Synthetic browser preference",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Synthetic browser preference", { exact: true }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "API key setup not yet available" }),
  ).toBeDisabled();
});

test("mobile navigation, usage, and layout remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "View usage and manage budgets" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await expect(
    page.getByRole("heading", { name: "Tasks", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Close navigation" })).not.toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const artifacts = join(
    process.env.LOCALAPPDATA || join(homedir(), ".local", "share"),
    "Maestro",
    "preview",
    "artifacts",
  );
  await page.screenshot({
    path: join(artifacts, "maestro-mobile.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "View usage and manage budgets" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
});
