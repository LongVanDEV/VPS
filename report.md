# Community Safety Advisory: "tubers93 hub" Discord Server

*This write-up documents technical evidence gathered from a script-sharing log and a script/form shared directly from this server, plus follow-up analysis. It's a safety advisory for other Roblox players and script-hub communities, not an official Discord ruling — Discord Trust & Safety hasn't reviewed this.*

---

## The server

| Field | Value |
|---|---|
| Server name | tubers93 hub |
| Invite | `discord.gg/94CRECcZE` |
| Self-description | "Its about any roblox scripts, bypass key, trading and much more!" |
| Member count (at time of check) | 157 |

**Everything below was sourced from this server** — the script-sharing log, the flagged stealer script, and the role-application form all came from here.

## What was found

**1. Mass exploit-script distribution.** Over roughly 10 days, accounts posting as "LaggyBot," "Dream," and "Mod" shared 40+ separate `loadstring(game:HttpGet(...))` links in a script-sharing channel, covering dozens of unrelated Roblox games. Multiple entries carried the uploaders' own "not verified" disclaimers. One listing had invisible Unicode characters stuffed through its text — a known trick for dodging simple text filters. Several were explicitly "freeze trade" scripts, a category independently confirmed elsewhere as a scam tool for stealing other players' items mid-trade, not a legitimate feature.

**2. A confirmed credential/inventory stealer.** A script targeting Murder Mystery 2, shared in this same context, shows a clear stealer signature on inspection:
- A hardcoded Discord webhook — a one-way pipe to an external Discord channel, with no legitimate reason to be in a "trade" script
- Abuse of Roblox's `queue_on_teleport` API to make the payload silently re-run every time the player teleports, without needing to be re-injected
- A second "loading" file loaded in parallel — a common way to keep the target's attention elsewhere while the real logic runs in the background

**3. A role-application form with an unconfirmed IP-collection claim.** A JotForm-hosted application tied to this server (`form.jotform.com/260193532924053`) asks for a Discord username, role justification, and directs applicants to open a support ticket afterward. **This is the one item that isn't independently confirmed:** on direct review, the form's visible content didn't show an obvious embedded IP-logging trick, though a static check can't rule out something in the underlying page script or a later step in the ticket flow. Standard form-platform IP logging (visible to whoever owns the form) applies regardless, which is normal across most form tools — not proof of anything unusual on its own.

## Bottom line

Items 1 and 2 are backed by direct technical evidence. Item 3 is a claim worth being cautious about, not a confirmed fact — treat it that way if you're sharing this further. If you're in Roblox scripting communities, exploit/cheat scripts and trade "tools" out of servers like this carry real risk: permanent account bans at minimum, and — per the stealer script above — a real chance of credential or item theft on top of that.

## What to do

- Don't run scripts sourced from this server, and be cautious with any application/ticket flow tied to it.
- Report the server directly to Discord: [dis.gd/report](https://dis.gd/report)
- If you've already run anything from here: change your Roblox password (this invalidates old session cookies), log out of all Discord sessions and rotate any bot tokens you own, and run a malware scan.
