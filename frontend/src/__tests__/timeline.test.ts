import { describe, expect, it } from "vitest";
import { timelineFromMessages } from "../stores/app";

describe("timelineFromMessages", () => {
  it("rebuilds user/assistant/tool items and attaches results in order", () => {
    const t = timelineFromMessages([
      { role: "user", agent: "user", content: "make a.py", tool_calls: null, tool_name: null },
      { role: "assistant", agent: "coder", content: "Plan", tool_calls: [{ function: { name: "create_file", arguments: { path: "a.py", content: "x" } } }, { function: { name: "run_command", arguments: { command: "ls" } } }], tool_name: null },
      { role: "tool", agent: "coder", content: "Created a.py", tool_calls: null, tool_name: "create_file" },
      { role: "tool", agent: "coder", content: "Permission denied: rejected by user. Command was NOT executed.", tool_calls: null, tool_name: "run_command" },
    ]);
    expect(t.map((i) => i.kind)).toEqual(["user", "assistant", "tool", "tool"]);
    expect(t[2]).toMatchObject({ name: "create_file", result: "Created a.py", ok: true });
    expect(t[3]).toMatchObject({ name: "run_command", ok: false });
  });
  it("marks non-zero exit codes as failed", () => {
    const t = timelineFromMessages([
      { role: "assistant", agent: "coder", content: "", tool_calls: [{ function: { name: "run_command", arguments: { command: "false" } } }], tool_name: null },
      { role: "tool", agent: "coder", content: "exit code: 1\n", tool_calls: null, tool_name: "run_command" },
    ]);
    expect(t[0]).toMatchObject({ kind: "tool", ok: false });
  });
});
