import { describe, expect, it } from "vitest";
import type { TFunction } from "i18next";
import { formatAgentList, formatMemorySearch, getMediaInfos } from "./utils";

const translate = ((key: string) => {
  const translations: Record<string, string> = {
    "tool.formatTable.file": "file",
    "tool.formatTable.lineNumber": "lineNumber",
    "tool.formatTable.score": "score",
    "tool.formatTable.summary": "summary",
    "tool.formatTable.name": "name",
    "tool.formatTable.id": "id",
    "tool.formatTable.description": "description",
    "tool.formatTable.status": "status",
  };

  return translations[key] ?? key;
}) as TFunction;

describe("formatMemorySearch", () => {
  it("renders memory search results as readable markdown cards", () => {
    const memoryResults = [
      {
        path: "memory/2026-06-08.md",
        start_line: 42,
        end_line: 45,
        score: 0.85,
        snippet: "这是实际摘要内容",
      },
    ];
    const rawToolResult = JSON.stringify([
      {
        type: "text",
        text: JSON.stringify(memoryResults),
      },
    ]);

    const formattedResult = formatMemorySearch(rawToolResult, translate);

    expect(formattedResult).toContain("### 1. memory/2026-06-08.md");
    expect(formattedResult).toContain("- **lineNumber**: L42-45");
    expect(formattedResult).toContain("- **score**: 0.85");
    expect(formattedResult).toContain("这是实际摘要内容");
    expect(formattedResult).not.toContain("| memory/2026-06-08.md |");
  });

  it("unwraps plain text tool result blocks instead of showing raw JSON", () => {
    const rawToolResult = JSON.stringify([
      {
        type: "text",
        text: "memory/2026-05-18.md L1-77\\n# 记忆与反思\\n这是很长的内容",
      },
    ]);

    const formattedResult = formatMemorySearch(rawToolResult, translate);

    expect(formattedResult).toBe(
      "memory/2026-05-18.md L1-77\n# 记忆与反思\n这是很长的内容",
    );
    expect(formattedResult).not.toContain('"type":"text"');
    expect(formattedResult).not.toContain("\\n");
  });

  it("formats malformed memory search text without showing metadata prefix", () => {
    // 截图格式：[ { 之间有空格，snippet 含真实换行导致 JSON.parse 失败
    const malformedMemoryText =
      '[ { "path": "/Users/zz/.copaw/workspaces/q88eWE/memory/2026-05-18.md", "start_line": 1, "end_line": 77, "score": 0.625, "snippet": "# 记忆与反思 - 2026-05-18\n\n## 项目信息\n\n项目名称：《弹幕逃生》4分钟短视频';
    const rawToolResult = JSON.stringify([
      {
        type: "text",
        text: malformedMemoryText,
      },
    ]);

    const formattedResult = formatMemorySearch(rawToolResult, translate);

    expect(formattedResult).toContain(
      "### 1. /Users/zz/.copaw/workspaces/q88eWE/memory/2026-05-18.md",
    );
    expect(formattedResult).toContain("- **lineNumber**: L1-77");
    expect(formattedResult).toContain("- **score**: 0.63");
    expect(formattedResult).toContain("# 记忆与反思 - 2026-05-18");
    expect(formattedResult).not.toContain('[ { "path"');
    expect(formattedResult).not.toContain('"snippet":');
  });
});

describe("formatAgentList", () => {
  it("renders agent rows from tool result text blocks", () => {
    const agents = [
      {
        name: "Coder",
        id: "agent-1",
        description: "Coding agent",
        status: "ready",
      },
    ];
    const rawToolResult = JSON.stringify([
      {
        type: "text",
        text: JSON.stringify(agents),
      },
    ]);

    const formattedResult = formatAgentList(rawToolResult, translate);

    expect(formattedResult).toContain(
      "| Coder | `agent-1` | Coding agent | ready |",
    );
    expect(formattedResult).not.toContain("|  | `` |  |  |");
  });
});

describe("getMediaInfos", () => {
  it("extracts every generated image from DataBlock tool results", () => {
    const media = getMediaInfos({
      type: "tool_call",
      id: "call-image",
      name: "image_generate",
      params: {},
      status: "done",
      result: [
        {
          type: "data",
          source: {
            type: "url",
            url: "file:///workspace/resources/generated.png",
            media_type: "image/png",
          },
          name: "generated.png",
        },
        {
          type: "data",
          source: {
            type: "url",
            url: "file:///workspace/resources/generated-2.webp",
            media_type: "image/webp",
          },
          name: "generated-2.webp",
        },
        { type: "text", text: '{"status":"completed"}' },
      ],
    });

    expect(media).toHaveLength(2);
    expect(media[0]).toMatchObject({
      name: "generated.png",
      type: "image",
    });
    expect(media[0].url).toContain(
      "/files/preview/workspace/resources/generated.png",
    );
    expect(media[1]).toMatchObject({
      name: "generated-2.webp",
      type: "image",
    });
  });
});
