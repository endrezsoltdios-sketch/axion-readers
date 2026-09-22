/* robots_ai.test.mjs - the named-group robots builder (22 Sep 2026). Run: node test/robots_ai.test.mjs */
import { robotsWithAiGroups, AI_CRAWLERS } from "../src/robots_ai.js";

let pass = 0, fail = 0;
const check = (name, ok, extra = "") => { if (ok) pass++; else { fail++; console.log("FAIL", name, extra); } };

const body = "Allow: /\nDisallow: /kit\nDisallow: /kit/\nContent-Signal: search=yes, ai-input=yes, ai-train=no";
const out = robotsWithAiGroups(body);
const groups = out.split("\n\n");
check("one group per crawler plus the wildcard", groups.length === AI_CRAWLERS.length + 1, String(groups.length));
check("wildcard group last", groups[groups.length - 1].startsWith("User-agent: *\n"), groups[groups.length - 1].slice(0, 30));
check("every group carries the same rules", groups.every((g) => g.split("\n").slice(1).join("\n").trim() === body), "");
check("every named crawler present once", AI_CRAWLERS.every((ua) => out.split(`User-agent: ${ua}\n`).length === 2), "");
check("GPTBot and ClaudeBot named", out.includes("User-agent: GPTBot\n") && out.includes("User-agent: ClaudeBot\n"), "");
check("no duplicate blank lines", !out.includes("\n\n\n"), "");
check("ends with one newline", out.endsWith("\n") && !out.endsWith("\n\n"), JSON.stringify(out.slice(-4)));
check("leading and trailing newlines in body tolerated", robotsWithAiGroups("\n" + body + "\n") === out, "");
check("Content-Signal in every group", groups.every((g) => g.includes("Content-Signal:")), "");

/* wildcardOnly / groupRules: the helpers a readiness suite uses. */
{
  const { wildcardOnly, groupRules } = await import("../src/robots_ai.js");
  const served = robotsWithAiGroups("Allow: /\nDisallow: /kit\nContent-Signal: search=yes, ai-input=yes, ai-train=no") + "\nSitemap: https://x.test/sitemap.xml\n";
  const w = wildcardOnly(served);
  check("wildcardOnly gives back the one-group file", w === "User-agent: *\nAllow: /\nDisallow: /kit\nContent-Signal: search=yes, ai-input=yes, ai-train=no\n\nSitemap: https://x.test/sitemap.xml\n", JSON.stringify(w));
  const g = groupRules(served);
  check("groupRules keys every named crawler and *", Object.keys(g).length === AI_CRAWLERS.length + 1 && "*" in g && "GPTBot" in g);
  check("every named group repeats the wildcard rules exactly", AI_CRAWLERS.every((ua) => g[ua] === g["*"]));
  check("PLANT: a narrowed named group is caught", (() => { const bad = served.replace("User-agent: GPTBot\nAllow: /\n", "User-agent: GPTBot\n"); const r = groupRules(bad); return r.GPTBot !== r["*"]; })());
}

/* The report and the exit come last (22 Sep 2026): a suite that exits before its last block is not a gate. */
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
