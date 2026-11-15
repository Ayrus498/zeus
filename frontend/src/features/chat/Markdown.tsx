import { createContext, memo, useContext } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { CodeBox } from "../../components/ui/copy";
import { langOf } from "../../lib/filename";
import { SaveCodeButton } from "./SaveCodeButton";

// True while the message is still streaming: code blocks then keep their own scroll pinned to the newest line.
export const StreamingContext = createContext(false);

function Pre({ node: _n, ...p }: React.ComponentPropsWithoutRef<"pre"> & { node?: unknown }) {
  const streaming = useContext(StreamingContext);
  return <CodeBox follow={streaming}><pre {...p} />{!streaming && <SaveCodeButton lang={langOf(p.children)} />}</CodeBox>;
}

// Defined once at module level. An inline `components={{ pre: ... }}` creates a new component type on every render,
// which makes React destroy and rebuild every code block on every streamed token (that was the flicker).
const COMPONENTS: Components = { pre: Pre };
const REMARK = [remarkGfm];
// detect:false - guessing the language from partial code makes colours flip as the block grows.
const REHYPE: [typeof rehypeHighlight, { detect: boolean; ignoreMissing: boolean }][] = [[rehypeHighlight, { detect: false, ignoreMissing: true }]];

export const Markdown = memo(function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div className="prose-zeus">
      <StreamingContext.Provider value={streaming}>
        <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={REHYPE} components={COMPONENTS}>{text}</ReactMarkdown>
      </StreamingContext.Provider>
    </div>
  );
});
