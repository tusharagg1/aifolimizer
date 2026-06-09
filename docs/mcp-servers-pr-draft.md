# MCP-Servers Awesome List PR Draft

Use this to submit aifolimizer to https://github.com/modelcontextprotocol/servers

## Step 1: Fork the upstream repo

```bash
gh repo fork modelcontextprotocol/servers --clone --remote
cd servers
git checkout -b add-aifolimizer
```

## Step 2: Edit `README.md`

Find the **Community Servers** section (search for `## 🌎 Community Servers` or `## Community Servers`). Entries are alphabetical.

Add this line in the correct alphabetical position (likely between "ai-..." and "aws-..." or wherever lowercase "a" entries sit):

```markdown
- **[aifolimizer](https://github.com/tusharagg1/aifolimizer)** - Portfolio analysis for retail investors. 103 MCP tools, 27 analysis skills (allocation health, risk, adversarial bull/bear, earnings, macro, sector rotation, tax-loss harvesting), walk-forward OOS validation, deflated-Sharpe gating, runs locally in Claude Code/Desktop with no API key. Optional Wealthsimple integration; ticker-level skills work on any broker.
```

Confirm the punctuation/format matches surrounding entries before committing - some MCP-servers repos use slightly different bullet style.

## Step 3: Commit + push

```bash
git add README.md
git commit -m "Add aifolimizer to Community Servers"
git push -u origin add-aifolimizer
```

## Step 4: Open PR

```bash
gh pr create --repo modelcontextprotocol/servers \
  --title "Add aifolimizer to Community Servers" \
  --body "$(cat <<'EOF'
## What

Adds [aifolimizer](https://github.com/tusharagg1/aifolimizer) to the Community Servers list.

## What it does

Local MCP server for retail-investor portfolio analysis. 103 tools spanning live prices, fundamentals, technicals, macro (FRED), crowding/positioning, crypto, options, news/sentiment. 27 analysis skills (allocation health, risk, adversarial research, earnings, sector rotation, tax-loss harvesting, more). Forward-tests trade-oriented skills via a recommendations log marked to market nightly.

Optional Wealthsimple broker integration via the unofficial \`ws-api\` library; ticker-level skills (stock-analysis, adversarial-research, macro-impact, etc.) work standalone with no broker connection.

Built with FastMCP, Python 3.12, walk-forward OOS validation, deflated-Sharpe gate (Bailey & López de Prado 2014), Brier+ECE calibration. MIT licensed.

## Status

Alpha. Single-user. Wealthsimple integration is reverse-engineered (ToS risk noted in README). Released v0.1.0 on 2026-06-01.

## Checklist

- [x] Server is public and MIT licensed
- [x] README has install + usage instructions
- [x] Entry placed alphabetically in Community Servers section
- [x] Description follows the existing format
EOF
)"
```

## Tips

- If the upstream repo uses `awesome-mcp-servers` style instead, adjust the entry to match
- If they have a CONTRIBUTING.md, skim it first - some require a specific category tag
- Maintainers often ask for "real-world use" evidence; link to TRACK_RECORD.md as proof
- Be patient - average merge time is 3-7 days for community-server PRs

## Bonus: also submit to these

After the official one merges (or in parallel):

- https://github.com/punkpeye/awesome-mcp-servers (largest community-curated MCP list)
- https://github.com/wong2/awesome-mcp-servers
- https://github.com/appcypher/awesome-mcp-servers

Same entry, different PR each.
