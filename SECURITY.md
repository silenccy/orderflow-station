# Security

## The thing that actually matters here

`data/subscribe_*.txt` is **not a config file — it is a live credential.** It embeds a
JWT tied to your Stockbit account, valid for roughly 24 hours. Anyone holding it can use
your market-data session until it expires.

So:

- **Never paste one into an issue, a pull request, a chat, a gist or a log.**
- **Never commit one.** `.gitignore` covers `data/`, `subscribe*.txt`, `*.csv` and
  `*.jsonl`. Do not work around it.
- `data/capture_raw.jsonl` records only *received* frames, so it carries no token — the
  subscribe frame is outbound and is never logged. Still avoid attaching it wholesale; it
  is large and contains your full session's market data.
- Nothing in this project ever prints the token. `--doctor` reports the frame's *size,
  age and symbol* only, which is why it is safe to paste into a bug report.

If you think you have leaked a frame: log out of Stockbit everywhere, which rotates the
session and invalidates it. Rotating is instant and free — do it rather than hoping.

## Reporting a vulnerability

Use **[GitHub's private vulnerability reporting](https://github.com/silenccy/orderflow-station/security/advisories/new)**
rather than a public issue.

Please include what an attacker could do and how you got there. A proof of concept helps —
but strip any real session frame from it first.

Expect an acknowledgement within a week. This is a personal project maintained in spare
time, not a funded product; treat that timeline as best effort rather than a commitment.

## Scope

In scope: anything that leaks a session frame, writes outside `ORDERFLOW_DATA`, executes
code from captured market data, or corrupts an archive that appears intact.

Out of scope, because they are known and documented:

- The websocket protocol is **reverse-engineered and unofficial**. It can change or break
  without notice. That is a stated property of the project, not a vulnerability.
- The TREND/CHOP regime filter is **unvalidated on IDX data** and may be wrong. Trading
  losses are not a security issue.
- There is **no reconnect backfill** beyond what the server replays (~40 trades). Tape
  missed during an outage is gone; the capture-integrity panel exists to make that visible.
