# Запрос в Anthropic: авторизация подпиской в самостоятельно размещённом оркестраторе

Отправить через Support Center (support.claude.com) или форму Contact sales на
anthropic.com. Ответ стоит получить письменно **до** того, как из этого
вырастет продукт.

---

**Subject:** Subscription auth (`claude setup-token`) in a self-hosted orchestrator — personal use and BYOK/BYOS distribution

Hello,

I've built a personal voice interface for Claude Code. A small daemon runs on my
own server, drives the official `claude` CLI through the Claude Agent SDK, and
lets me talk to that session from my phone. It does not reimplement the Claude
client, spoof a client identity, or call private endpoints — it orchestrates the
official CLI and reads its output.

I have two questions.

**1. My own use, today.** I authenticate with a long-lived token from
`claude setup-token`, issued against my own subscription, used only by me, on a
server only I can reach. Is that within the terms for Claude Code subscriptions?

**2. If I distribute this.** I would ship it as self-hosted software that each
user installs themselves and points at their own credentials. I would never hold
a user's credentials and would never proxy requests on anyone's behalf — every
user's traffic goes from their own machine to Anthropic under their own account.
Two variants:

- **BYOK** — the user supplies their own Anthropic API key.
- **BYOS** — the user supplies their own token from `claude setup-token`.

For BYOS: is it acceptable for third-party self-hosted software to drive the
official CLI with the user's own subscription credentials under those
conditions? If not, is BYOK sufficient on its own, or are there further
requirements — commercial terms, attribution, anything else — I should meet
first?

I would rather ask before shipping than after. If another team is the right
place for this, I'd be grateful for a pointer.

Thank you,
<имя, ссылка на репозиторий если захочешь>
