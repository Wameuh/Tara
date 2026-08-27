import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeLink } from "../../../routing/safeLink";

import "./SessionSummaryMarkdown.css";

const allowedElements = [
  "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
  "li", "ol", "p", "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
];

export default function SessionSummaryMarkdown({ markdown, label }: { markdown: string; label: string }) {
  return <section className="summary-document markdown-document" aria-label={label}>
    <Markdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => safeLink(url) ?? ""}
      allowedElements={allowedElements}
      skipHtml
      components={{
        h1: ({ node, ...props }) => { void node; return <h2 {...props} />; },
        h2: ({ node, ...props }) => { void node; return <h3 {...props} />; },
        h3: ({ node, ...props }) => { void node; return <h4 {...props} />; },
        h4: ({ node, ...props }) => { void node; return <h5 {...props} />; },
        h5: ({ node, ...props }) => { void node; return <h6 {...props} />; },
        h6: ({ node, ...props }) => { void node; return <h6 {...props} />; },
        a: ({ children, node, ...props }) => {
          void node;
          return props.href
            ? <a {...props} rel="noopener noreferrer">{children}</a>
            : <span>{children}</span>;
        },
      }}
    >{markdown}</Markdown>
  </section>;
}
