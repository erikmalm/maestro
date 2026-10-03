import { expect, test } from "@playwright/test";

test("private memory persists, can be corrected and forgotten, and follows chat deletion", async ({
  page,
}) => {
  await page.goto("/");
  const { csrf } = await (await page.request.get("/api/session")).json();
  const initial = await (await page.request.get("/api/workspace")).json();
  const headers = { "X-Maestro-CSRF": csrf };
  const ownedMemories = new Set<string>();
  let ownedChat: string | null = null;
  const memory = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Private memory", exact: true }),
  });
  const content = "Synthetic preference: keep implementations simple.";
  const corrected = "Synthetic preference: keep code simple and concise.";
  const scoped = "Synthetic fact available only in this conversation.";

  try {
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await memory.getByLabel("What should Maestro remember?").fill(content);
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/memory") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await memory.getByRole("button", { name: "Save memory" }).click();
    const record = await (await saved).json();
    ownedMemories.add(record.id);
    expect(record.scope).toBe("workspace");
    await expect(memory.getByText(content, { exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await expect(memory.getByText(content, { exact: true })).toBeVisible();

    const entry = memory.locator(".memory-entry").filter({ hasText: content });
    await entry.getByRole("button", { name: "Edit saved memory" }).click();
    await memory
      .getByRole("textbox", { name: "Edit memory", exact: true })
      .fill(corrected);
    await memory.getByRole("button", { name: "Update memory" }).click();
    await expect(memory.getByText(corrected, { exact: true })).toBeVisible();
    await expect(memory.getByText(content, { exact: true })).not.toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await expect(memory.getByText(corrected, { exact: true })).toBeVisible();
    await memory
      .locator(".memory-entry")
      .filter({ hasText: corrected })
      .getByRole("button", { name: "Forget saved memory" })
      .click();
    await expect(
      memory.getByText(corrected, { exact: true }),
    ).not.toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await expect(
      memory.getByText(corrected, { exact: true }),
    ).not.toBeVisible();

    await page.getByRole("button", { name: "New chat", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "New chat", exact: true }),
    ).toBeVisible();
    ownedChat = new URL(page.url()).searchParams.get("chat");
    expect(ownedChat).toBeTruthy();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await memory.getByLabel("What should Maestro remember?").fill(scoped);
    await memory.getByLabel("Use this memory in").selectOption("conversation");
    const savedConversation = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/memory") &&
        response.request().method() === "POST" &&
        response.ok(),
    );
    await memory.getByRole("button", { name: "Save memory" }).click();
    const conversation = await (await savedConversation).json();
    await expect(
      memory.locator(".memory-entry").getByText(scoped, { exact: true }),
    ).toBeVisible();
    expect(conversation).toMatchObject({
      scope: "conversation",
      chat_id: ownedChat,
    });
    ownedMemories.add(conversation.id);
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await expect(memory.getByText(scoped, { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Workspace", exact: true }).click();
    await page
      .getByRole("button", { name: "Delete chat", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Delete chat" });
    await dialog
      .getByRole("button", { name: "Delete chat", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await memory.getByRole("button", { name: "List", exact: true }).click();
    await expect(memory.getByText(scoped, { exact: true })).not.toBeVisible();
    await page.reload();
    const after = await (await page.request.get("/api/workspace")).json();
    expect(after.memories).toEqual(initial.memories);
    expect(after.chats).toEqual(initial.chats);
    expect(after.usage).toEqual(initial.usage);
  } finally {
    for (const id of ownedMemories)
      await page.request
        .delete(`/api/memory/${encodeURIComponent(id)}`, { headers })
        .catch(() => {});
    if (ownedChat)
      await page.request
        .delete(`/api/chats/${encodeURIComponent(ownedChat)}`, { headers })
        .catch(() => {});
  }
});
