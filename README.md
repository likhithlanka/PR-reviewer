# ReviewForge MCP v2.0

An interactive PR review workbench with AST-based impact analysis, vector-indexed codebase and spec search, static analysis integration, and continuous learning from review history. 

This is a Model Context Protocol (MCP) server exposing 9 tools to LLM agents (e.g. Claude Code).

## Requirements

- Python 3.10+
- Git

## Installation

```bash
cd reviewforge-mcp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Static Analysis Dependencies (Optional)
ReviewForge runs whatever static analyzers it finds on your `$PATH`. If they are missing, it gracefully degrades.
```bash
pip install ruff mypy bandit semgrep
brew install hlint  # If using Haskell
npm install -g eslint # If using JS/TS
```

## Configuration

Set up environment variables or place a `.env` file in the root:

```env
# Git Platform
BITBUCKET_TOKEN=
BITBUCKET_WORKSPACE=

GITHUB_TOKEN=

# LLM (Required for Step 11 LLM pass & Playbook generation)
ANTHROPIC_API_KEY=
LLM_MODEL=claude-opus-4-5

# Embedding (Optional, defaults to local sentence-transformers)
EMBEDDING_PROVIDER=local
OPENAI_API_KEY=

# Document Fetchers (Optional)
CONFLUENCE_URL=https://<your-domain>.atlassian.net
CONFLUENCE_USERNAME=
CONFLUENCE_API_TOKEN=

# Settings
REVIEWFORGE_CACHE_DIR=~/.reviewforge
RUN_COMMAND_ENABLED=true
```

## Adding to Claude Code

Update your `~/.config/claude/mcp.json` or `claude.json`:

```json
{
  "mcpServers": {
    "reviewforge": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/reviewforge-mcp/server.py"],
      "env": {
        "BITBUCKET_TOKEN": "BBDC-...",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

## How to Use

Once configured in your MCP client, start a session and prompt:

```text
Run a code review on the main Bitbot PR (repo=backend, pr=123).
```

The agent will automatically call the `review_pr` tool to run the 11-step pipeline. After the initial review, you can ask follow-up questions:
- *"Who calls `processPayment()` and did any of them not get updated?"* -> hits `impact_analysis`
- *"What does the Visa spec say about DE 22?"* -> hits `search` over docs and code
- *"Run the fix for that ruff issue"* -> hits `run_command`

## The 9 Tools

| Tool | Concept |
|------|---------|
| `review_pr` | The 11-step orchestrator pipeline. |
| `search` | Semantic search over Vectorized Code + Specs. |
| `impact_analysis` | AST-graph caller/callee traversal. |
| `read_file` | Read files with loaded AST structural context. |
| `fetch_doc` | Download + vectorize URLs via standard adapters. |
| `run_analysis`| Run custom static analyzer wrappers interactively. |
| `get_review_history` | The Dynamic Playbook engine. |
| `get_pr_comments` | Read current open PR threads. |
| `run_command` | Sandboxed bash executor for automated fixes. |


