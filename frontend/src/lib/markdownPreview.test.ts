import { describe, expect, it } from "vitest";
import { parseMarkdownPreview } from "./markdownPreview";

describe("parseMarkdownPreview", () => {
  it("parses common PRD markdown blocks without HTML rendering", () => {
    const blocks = parseMarkdownPreview(
      [
        "# Spec",
        "",
        "## Registration Flow",
        "",
        "- Create account",
        "- Email verification required",
        "",
        "After verification, collect address.",
        "",
        "```json",
        '{"ok": true}',
        "```",
      ].join("\n"),
    );

    expect(blocks).toEqual([
      { type: "heading", level: 1, text: "Spec" },
      { type: "heading", level: 2, text: "Registration Flow" },
      {
        type: "list",
        ordered: false,
        items: ["Create account", "Email verification required"],
      },
      { type: "paragraph", text: "After verification, collect address." },
      { type: "code", text: '{"ok": true}' },
    ]);
  });
});
