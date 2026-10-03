import { expect, test, type Page, type Route } from "@playwright/test";
import type { Message, MessageFeedback, Workspace } from "../src/api";

async function feedbackFixture(page: Page) {
  await page.goto("/");
  expect((await page.request.get("/api/session")).ok()).toBeTruthy();
  const state: Workspace = await (
    await page.request.get("/api/workspace")
  ).json();
  state.chats = ["first", "second"].map((id) => ({
    id,
    title: `${id} conversation`,
    created_at: "2026-10-03",
    updated_at: "2026-10-03",
  }));
  Object.assign(state.provider, {
    credentials_required: false,
    models: ["synthetic-model"],
  });
  Object.assign(state.provider.config, {
    protocol: "ollama",
    model: "synthetic-model",
    pricing_verified: true,
  });
  state.work!.config.enabled = false;
  state.work!.queued = 0;
  state.work!.running = false;
  const messages: Record<string, Message[]> = Object.fromEntries(
    state.chats.map(({ id }) => [
      id,
      [
        { id: `${id}-user`, role: "user", text: `A synthetic ${id} question.` },
        { id: "answer", role: "assistant", text: `A synthetic ${id} answer.` },
        {
          id: `${id}-demo`,
          role: "assistant",
          text: "A synthetic archived demo.",
          demo: true,
        },
      ],
    ]),
  );
  let current = "first";
  let workspaceCalls = 0;
  let generationCalls = 0;
  let held: Route | undefined;
  let heldResult:
    { message_id: string; feedback: MessageFeedback | null } | undefined;
  let holdNext = false;
  const snapshot = (id = current) => ({
    ...state,
    active_chat_id: id,
    messages: messages[id] ?? [],
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/session") {
      await route.fulfill({ json: { csrf: "synthetic-csrf" } });
    } else if (url.pathname === "/api/workspace") {
      workspaceCalls += 1;
      current = url.searchParams.get("chat_id") ?? current;
      await route.fulfill({ json: snapshot() });
    } else if (url.pathname.endsWith("/feedback")) {
      expect(route.request().method()).toBe("PATCH");
      const id = url.pathname.split("/")[3];
      const message = messages[id].find((item) => item.role === "assistant")!;
      const { rating, comment } = route.request().postDataJSON();
      message.feedback = rating
        ? { rating, comment, updated_at: "2026-10-03" }
        : null;
      const result = {
        message_id: message.id,
        feedback: message.feedback,
        work: state.work,
        memories: state.memories,
      };
      if (holdNext) {
        holdNext = false;
        held = route;
        heldResult = structuredClone(result);
      } else await route.fulfill({ json: result });
    } else if (url.pathname === "/api/chat") {
      generationCalls += 1;
      const { chat_id: id, text } = route.request().postDataJSON();
      messages[id][1].feedback = null;
      messages[id].push(
        { id: "new-user", role: "user", text },
        {
          id: "new-answer",
          role: "assistant",
          text: "A fresh synthetic answer.",
        },
      );
      await route.fulfill({ json: snapshot(id) });
    } else if (url.pathname.startsWith("/api/chats/")) {
      expect(route.request().method()).toBe("DELETE");
      const id = url.pathname.split("/")[3];
      state.chats = state.chats.filter((chat) => chat.id !== id);
      delete messages[id];
      current = state.chats[0]?.id ?? "";
      await route.fulfill({ json: snapshot() });
    } else throw new Error(`Unexpected feedback request: ${url.pathname}`);
  });
  await page.goto("/?chat=first");
  await expect(
    page.getByText("A synthetic first answer.", { exact: true }),
  ).toBeVisible();
  return {
    state,
    workspaceCalls: () => workspaceCalls,
    generationCalls: () => generationCalls,
    holdNext: () => {
      holdNext = true;
    },
    held: () => held,
    release: async () => {
      expect(held).toBeDefined();
      await held!.fulfill({ json: heldResult });
      held = undefined;
    },
  };
}

test("answer feedback can be rated, edited and cleared without inference or replacing the composer draft", async ({
  page,
}) => {
  const fixture = await feedbackFixture(page);
  const draft = page.getByRole("textbox", { name: "Message Maestro" });
  await draft.fill("Keep this unsent composer draft.");
  const calls = fixture.workspaceCalls();
  await expect(page.locator(".chat-message.user .answer-feedback")).toHaveCount(
    0,
  );
  await expect(
    page.locator(".chat-message.assistant .answer-feedback"),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "Helpful", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Helpful", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Add feedback comment" }).click();
  const comment = page.getByRole("textbox", {
    name: "Feedback comment (optional)",
  });
  await expect(comment).toHaveAttribute("maxlength", "4000");
  await comment.fill("The explanation was clear; include a source next time.");
  await page.route(
    "**/api/chats/first/messages/answer/feedback",
    (route) =>
      route.fulfill({
        status: 409,
        json: { detail: "Synthetic feedback validation failed." },
      }),
    { times: 1 },
  );
  await page
    .getByRole("button", { name: "Save feedback", exact: true })
    .click();
  await expect(page.getByRole("alert")).toHaveText(
    "Synthetic feedback validation failed.",
  );
  await expect(comment).toHaveValue(
    "The explanation was clear; include a source next time.",
  );
  await expect(draft).toHaveValue("Keep this unsent composer draft.");
  await page
    .getByRole("button", { name: "Save feedback", exact: true })
    .click();
  await expect(comment).not.toBeVisible();
  await page
    .getByRole("button", { name: "Needs improvement", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Needs improvement", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Edit feedback comment" }).click();
  await expect(comment).toHaveValue(
    "The explanation was clear; include a source next time.",
  );
  await comment.fill("Correct the synthetic example.");
  await page
    .getByRole("button", { name: "Save feedback", exact: true })
    .click();
  await expect(draft).toHaveValue("Keep this unsent composer draft.");
  expect(fixture.workspaceCalls()).toBe(calls);
  expect(fixture.generationCalls()).toBe(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Needs improvement", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Edit feedback comment" }).click();
  await expect(comment).toHaveValue("Correct the synthetic example.");
  await page.getByRole("button", { name: "Clear feedback" }).click();
  await expect(
    page.getByRole("button", { name: "Clear feedback" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Needs improvement", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  expect(fixture.generationCalls()).toBe(0);
});

test("feedback clear refreshes derived memory and journal while paused without replacing settings drafts", async ({
  page,
}) => {
  const fixture = await feedbackFixture(page);
  fixture.state.memories = [
    {
      id: "derived-note",
      content: "A synthetic practice derived from feedback.",
      scope: "workspace",
      chat_id: null,
      source_message_id: null,
      origin: "reflective",
      kind: "lesson",
      created_at: "2026-10-03",
      updated_at: "2026-10-03",
    },
  ];
  fixture.state.work!.journal = [
    {
      id: "derived-journal",
      created_at: "2026-10-03",
      kind: "reflection",
      summary: "A synthetic journal based on the old feedback.",
      changes: [],
      sources: [],
      models: [],
    },
  ];
  await page.getByRole("button", { name: "Helpful", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Clear feedback" }),
  ).toBeVisible();
  fixture.state.memories = [];
  fixture.state.work!.journal = [];
  fixture.holdNext();
  await page.getByRole("button", { name: "Clear feedback" }).click();
  await expect.poll(fixture.held).toBeTruthy();
  const calls = fixture.workspaceCalls();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .locator(".memory-setup")
    .getByRole("button", { name: "List", exact: true })
    .click();
  await expect(
    page.getByText("A synthetic practice derived from feedback.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByText("Work limits & memory recall", { exact: true }).click();
  await page.getByLabel("Daily reflection jobs", { exact: true }).fill("93");
  await fixture.release();
  await expect(
    page.getByText("A synthetic practice derived from feedback.", {
      exact: true,
    }),
  ).toHaveCount(0);
  await page.getByText("Private reflection journal", { exact: true }).click();
  await expect(
    page.getByText("No reflections recorded yet.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Daily reflection jobs", { exact: true }),
  ).toHaveValue("93");
  await expect(page.getByText(/Unsaved changes/)).toBeVisible();
  expect(fixture.workspaceCalls()).toBe(calls);
  expect(fixture.generationCalls()).toBe(0);
});

for (const action of ["switch", "delete", "generate"] as const) {
  test(`late feedback does not overwrite the workspace after ${action}`, async ({
    page,
  }) => {
    const fixture = await feedbackFixture(page);
    fixture.holdNext();
    await page
      .getByRole("button", { name: "Needs improvement", exact: true })
      .click();
    await expect.poll(fixture.held).toBeTruthy();
    if (action === "switch") {
      await page
        .getByRole("button", { name: "Open chat: second conversation" })
        .click();
    } else if (action === "delete") {
      await page
        .getByRole("button", { name: "Delete chat", exact: true })
        .click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Delete chat", exact: true })
        .click();
    } else {
      await page
        .getByRole("textbox", { name: "Message Maestro" })
        .fill("A new synthetic question.");
      await page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      await expect(
        page.getByText("A fresh synthetic answer.", { exact: true }),
      ).toBeVisible();
    }
    await fixture.release();
    await expect(
      page
        .getByRole("button", { name: "Needs improvement", exact: true })
        .first(),
    ).toHaveAttribute("aria-pressed", "false");
    await expect(
      page.getByRole("button", { name: "Clear feedback" }),
    ).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    if (action !== "generate")
      await expect(
        page.getByRole("heading", { name: "second conversation", exact: true }),
      ).toBeVisible();
  });
}

test("expanded review and text limits are available in the forms", async ({
  page,
}) => {
  await feedbackFixture(page);
  await expect(
    page.getByRole("textbox", { name: "Message Maestro" }),
  ).toHaveAttribute("maxlength", "32000");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const memory = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Private memory", exact: true }),
  });
  if (
    !(await memory
      .getByRole("textbox", { name: "What should Maestro remember?" })
      .isVisible())
  )
    await memory.getByText("Add a memory", { exact: true }).click();
  await expect(
    memory.getByRole("textbox", { name: "What should Maestro remember?" }),
  ).toHaveAttribute("maxlength", "8000");
  await page.getByText("Work limits & memory recall", { exact: true }).click();
  for (const [label, minimum, maximum] of [
    ["Background context tokens", "8192", "131072"],
    ["Exchanges per periodic review", "1", "32"],
    ["Conversation review characters", "1000", "200000"],
    ["Reflection output tokens", "1", "16384"],
    ["Memories per reply", "0", "100"],
    ["Memory context characters", "0", "200000"],
  ] as const) {
    const input = page.getByLabel(label, { exact: true });
    await expect(input).toHaveAttribute("min", minimum);
    await expect(input).toHaveAttribute("max", maximum);
  }
  await page.getByRole("button", { name: /^Tasks/ }).click();
  await page.getByRole("button", { name: "New task", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByLabel("Context & completion criteria"),
  ).toHaveAttribute("maxlength", "16000");
});
