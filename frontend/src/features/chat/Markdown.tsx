import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { CodeBox } from "../../components/ui/copy";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-zeus">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{ pre: ({ node: _n, ...p }) => <CodeBox><pre {...p} /></CodeBox> }}>{text}</ReactMarkdown>
    </div>
  );
}
