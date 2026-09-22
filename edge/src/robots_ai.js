/* robots_ai.js - robots.txt that names the AI crawlers it welcomes, one group each (22 Sep 2026).
 *
 * (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
 *
 * Why: a site with one wildcard group reads to Cloudflare's Agent Readiness diagnostic, and to
 * tools/agent_ready_scan.py, as "no AI-specific bot rules". A site that is open to agents by policy
 * (Content-Signal ai-input=yes, ai-train=no) pays nothing for saying so by name, and it then reads as a
 * decision rather than an omission. The Robots Exclusion Protocol (RFC 9309) applies the most specific matching group only, so each
 * named group repeats the wildcard group's rules exactly; a named group with fewer lines would silently widen
 * or narrow access for that one agent. The Content-Signal line rides in every group for the same reason.
 */
export const AI_CRAWLERS = [
  "GPTBot", "OAI-SearchBot", "ChatGPT-User",
  "ClaudeBot", "Claude-User", "Claude-SearchBot",
  "PerplexityBot", "Perplexity-User",
  "Google-Extended", "GoogleOther",
  "Bytespider", "CCBot", "meta-externalagent", "Amazonbot", "Applebot-Extended", "DuckAssistBot", "MistralAI-User",
];

/* body: the rule lines of the wildcard group (Allow, Disallow, Content-Signal), no User-agent line, no Sitemap. */
export function robotsWithAiGroups(body) {
  const rules = String(body).replace(/^\n+|\n+$/g, "");
  const groups = AI_CRAWLERS.map((ua) => `User-agent: ${ua}\n${rules}\n`);
  groups.push(`User-agent: *\n${rules}\n`);
  return groups.join("\n");
}

/* The served file with the named groups removed: what the wildcard byte pins compare against. Each door's
 * readiness suite pins the earlier bytes of its wildcard group; the named groups sit before it, so stripping
 * them (each is "User-agent: <name>\n<rules>\n\n") gives back the file the pin was written for. */
export function wildcardOnly(served) {
  const groups = String(served).split("\n\n");
  return groups.filter((g) => !g.startsWith("User-agent: ") || g.startsWith("User-agent: *")).join("\n\n");
}

/* The rule lines of each named group, keyed by user-agent, plus "*": the check that every named group repeats
 * the wildcard group exactly (RFC 9309 applies the most specific group only). */
export function groupRules(served) {
  const out = {};
  for (const g of String(served).split("\n\n")) {
    const lines = g.split("\n").filter((l) => l !== "");
    if (!lines.length || !lines[0].startsWith("User-agent: ")) continue;
    out[lines[0].slice("User-agent: ".length)] = lines.slice(1).filter((l) => !l.startsWith("Sitemap:")).join("\n");
  }
  return out;
}
