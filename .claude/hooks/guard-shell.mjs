// PreToolUse guard for Bash/PowerShell.
//
// Why this exists: permission `deny` rules are prefix matches on the Bash tool only.
// They miss (a) every PowerShell tool call, and (b) flags that arrive out of prefix
// order, e.g. `git push origin main --force`. This closes both holes with regexes.
//
// The rules mirror the "never do this" list in STRUCT_2.md §10 / §85 — the shared DGX
// Spark and the shared GitHub branches are team infrastructure.

const RULES = [
  [/\bgit\b[^|;&]*\bpush\b[^|;&]*(--force\b|--force-with-lease\b|(?:^|\s)-f(?:\s|$))/i,
    "force-push to a shared branch"],
  [/\bgit\b[^|;&]*\breset\b[^|;&]*--hard\b/i, "git reset --hard discards work irrecoverably"],
  [/\bgit\s+clean\b/i, "git clean deletes untracked files"],
  [/\bgit\s+worktree\s+remove\b/i, "removing a worktree may be a teammate's"],
  [/\bgit\s+push\b[^|;&]*(--delete\b|(?:^|\s):\S)/i, "deleting a remote branch"],
  [/\bRemove-Item\b[^|;&]*-Recurse/i, "recursive delete"],
  [/\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r/i, "recursive force delete"],
  [/\bStop-Process\b|\bpkill\b|\bkillall\b/i, "killing a process this session did not start"],
  [/\bdocker\s+(rm\b|kill\b|system\s+prune\b|volume\s+rm\b)/i,
    "removing Docker resources that may belong to a teammate"],
  [/(^|\s)sudo\s/i, "sudo on shared infrastructure"],
  [/\bapt(-get)?\s+(install|upgrade|remove|purge|dist-upgrade)\b/i,
    "changing system packages on shared infrastructure"],
];

let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let cmd = "";
  try {
    cmd = JSON.parse(raw)?.tool_input?.command ?? "";
  } catch {
    process.exit(0); // unparseable input is not our call to block on
  }
  for (const [re, why] of RULES) {
    if (re.test(cmd)) {
      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason:
            `Blocked by .claude/hooks/guard-shell.mjs: ${why}. This repo shares a DGX ` +
            `Spark and a GitHub remote with other developers (see CLAUDE.md > Dangerous ` +
            `areas). If this is genuinely intended, ask the user to run it themselves ` +
            `with a leading "!".`,
        },
      }));
      process.exit(0);
    }
  }
  process.exit(0);
});
