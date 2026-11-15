// Bundle Monaco locally (no CDN) so the desktop app works offline. Core editor + Monarch
// grammars only (no TS/JSON/CSS language-service workers) keeps the bundle small.
import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/basic-languages/monaco.contribution";
import editorWorker from "monaco-editor/editor/editor.worker?worker";
import { loader } from "@monaco-editor/react";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });

const EXT: Record<string, string> = { py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", json: "plaintext", md: "markdown", html: "html", css: "css", rs: "rust", toml: "ini", yml: "yaml", yaml: "yaml", sh: "shell", sql: "sql", txt: "plaintext" };
export const langFor = (path: string) => EXT[path.split(".").pop()?.toLowerCase() ?? ""] ?? "plaintext";
