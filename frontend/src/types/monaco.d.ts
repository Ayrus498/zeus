declare module "monaco-editor/editor/editor.api" { export * from "monaco-editor"; }
declare module "monaco-editor/basic-languages/monaco.contribution";
declare module "monaco-editor/editor/editor.worker?worker" { const W: { new (): Worker }; export default W; }
