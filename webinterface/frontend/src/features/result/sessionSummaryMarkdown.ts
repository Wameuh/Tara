import type { ResultSnapshot } from "../../api/client";

type Section = ResultSnapshot["sections"][number];
type Block = Section["blocks"][number];

const tableCell = (value: string) => value.replaceAll("|", "\\|").replaceAll("\n", " ");

function blockMarkdown(block: Block): string {
  if (block.type === "paragraph") return block.text;
  if (block.type === "list" || block.type === "orderedList") {
    return block.items
      .map((item, index) => block.type === "list" ? `- ${item}` : `${index + 1}. ${item}`)
      .join("\n");
  }
  if (block.type === "keyValue") return block.entries.map((entry) => `- **${entry.key} :** ${entry.value}`).join("\n");
  if (block.type === "callout") return `### ${block.title}\n\n${block.text}`;
  if ("headers" in block) {
    const headers = `| ${block.headers.map(tableCell).join(" | ")} |`;
    const separator = `| ${block.headers.map(() => "---").join(" | ")} |`;
    const rows = block.rows.map((row) => `| ${row.map(tableCell).join(" | ")} |`);
    return [headers, separator, ...rows].join("\n");
  }
  return "";
}

export function buildSessionSummaryMarkdown(result: ResultSnapshot, title: string): string {
  const sections = result.sections.slice().sort((left, right) => left.order - right.order);
  const body = sections.map((section) => {
    const content = section.blocks.map(blockMarkdown).join("\n\n");
    return `## ${section.title}\n\n${content || section.text}`;
  });
  return [`# ${title}`, ...body, ""].join("\n\n");
}

export function downloadSessionSummary(result: ResultSnapshot, title: string): void {
  const blob = new Blob([buildSessionSummaryMarkdown(result, title)], {
    type: "text/markdown;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "session_summary.md";
  anchor.click();
  URL.revokeObjectURL(url);
}
