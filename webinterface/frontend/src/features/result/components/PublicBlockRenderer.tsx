import { Fragment, type ReactNode } from "react";

type Link = { label: string; href: string };
export type PublicBlock =
  | { type: "text" | "paragraph"; text: string; links?: Link[] }
  | { type: "list" | "orderedList"; items: string[] }
  | { type: "keyValue"; entries: { key: string; value: string }[] }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "callout"; title: string; text: string }
  | { type: "unknown" };

const safeLink = (href: string) => {
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
};

function highlighted(text: string, query: string): ReactNode {
  if (query.length < 2) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.split(new RegExp(`(${escaped})`, "gi")).map((part, index) =>
    part.toLocaleLowerCase() === query.toLocaleLowerCase() ? <mark key={index}>{part}</mark> : part,
  );
}

export function PublicBlockRenderer({ blocks, query }: { blocks: PublicBlock[]; query: string }) {
  return <>{blocks.map((block, index) => {
    if (block.type === "text" || block.type === "paragraph") return <p key={index}>{highlighted(block.text, query)}{block.links?.map((link) => { const href = safeLink(link.href); return href ? <a key={`${link.href}-${link.label}`} href={href} target="_blank" rel="noopener noreferrer">{link.label}</a> : null; })}</p>;
    if (block.type === "list" || block.type === "orderedList") { const Tag = block.type === "list" ? "ul" : "ol"; return <Tag key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{highlighted(item, query)}</li>)}</Tag>; }
    if (block.type === "keyValue") return <dl key={index}>{block.entries.map((entry, entryIndex) => <Fragment key={entryIndex}><dt>{entry.key}</dt><dd>{entry.value}</dd></Fragment>)}</dl>;
    if (block.type === "table") return <div key={index} className="table-scroll"><table><thead><tr>{block.headers.map((value, cellIndex) => <th key={cellIndex}>{value}</th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cellIndex) => <td key={cellIndex}>{value}</td>)}</tr>)}</tbody></table></div>;
    if (block.type === "callout") return <aside key={index}><strong>{block.title}</strong><p>{block.text}</p></aside>;
    return null;
  })}</>;
}
