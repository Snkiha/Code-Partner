# Local Terminal Coding Partner (MCP + Ollama)

A private, terminal-based pair programmer that runs **100% locally**. It bridges
a local model (via [Ollama](https://ollama.com/)) to your filesystem and a
sandboxed shell using the **Model Context Protocol (MCP)**, so the model can
read your code, write scripts, run them, and fix its own mistakes.

- **Local & private** — no API keys, nothing leaves your machine.
- **`--run`** — writes a script, executes it, and self-heals on failure with
  independently verified exit codes (the model's "it works" claims are ignored).
- **`--review`** — writes a script, then a second agent reviews it.
- **Sandboxed** — generated code is confined to a `workspace/` dir and an
  allow-listed set of commands; it can't touch your source tree.

---

## Quickstart

### Option A — Docker (nothing to install but Docker)

```bash
git clone <this-repo> && cd "Code Partner"
docker compose run --rm agent
```

That starts Ollama, downloads the model on first run (~5 GB, cached in a volume),
builds the agent, and drops you into the session. To point it at another project:

```bash
CP_PROJECT=~/code/my-app docker compose run --rm agent
```

### Option B — Local (you run Ollama yourself)

**Prerequisites:** Python 3.11+, Node.js 18+, and [Ollama](https://ollama.com/) running.

```bash
pip install -r requirements.txt
python main.py --pull          # --pull downloads missing models automatically
```

---

## Using it

Once in the session:

| Input | What happens |
| --- | --- |
| `--run <task>` | Writer → Execute → Self-Heal loop |
| `--review <task>` | Writer → Reviewer (writes `workspace/<name>.review.md`) |
| `+pin <path>` / `+unpin <path>` / `+pins` | Keep a file in context permanently |
| anything else | Conversational agent with full project + shell access |
| `exit` | Quit |

The Writer / Reviewer / Healer only read and write inside `workspace/` (git-ignored),
so a run can never overwrite your source, README or `requirements.txt`. Only the
conversational agent sees the whole project.

---

## Configuration

Every setting has an env var and most have a CLI flag (`python main.py --help`).
Copy [`.env.example`](.env.example) to `.env` or export the vars.

| Env | Flag | Default | Purpose |
| --- | --- | --- | --- |
| `CP_CODER_MODEL` | `--model` | `qwen2.5-coder:7b` | model for every role |
| `CP_CHAT_MODEL` | `--chat-model` | = coder model | override just chat (e.g. `qwen2.5-coder:3b`) |
| `CP_PROJECT_DIR` | `--project` | current dir | root the agent may read/write |
| `CP_WORKSPACE` | `--workspace` | `workspace` | subdir for generated code |
| `OLLAMA_HOST` | `--ollama-host` | `http://localhost:11434` | Ollama URL |
| `BASH_MCP_TIMEOUT` | `--timeout` | `20` | per-command timeout (seconds) |
| `BASH_MCP_ALLOWED_EXTRA` | `--allow` | — | extra executables the run sandbox may call |
| `CP_AUTO_PULL` | `--pull` | `0` | download missing models on startup |

---

## Docker: hardening a standalone container

The Compose setup is the easy path. To run the image directly with tight limits:

```bash
docker build -t code-partner .
docker run -it --rm \
  --add-host=host.docker.internal:host-gateway \
  -v "$PWD:/project" \
  --network none \
  --pids-limit=256 --memory=2g --cpus=2 \
  --read-only --tmpfs /tmp \
  code-partner
```

`--network none` works because the MCP filesystem server is baked into the image.
It talks to Ollama on the host via `host.docker.internal`; drop `--network none`
if Ollama is elsewhere.

---

## How it works

An agentic loop (ReAct): the Python host sends the conversation + MCP tool
schemas to Ollama; when the model asks for a tool, the host runs it against the
filesystem or bash MCP server over stdio and feeds the result back. Small local
models rarely emit a *structured* tool call, so the host also recovers tool calls
written as JSON text and nudges past prose-only replies. For `--run`, the host —
not the model — executes the script and checks the real exit code before
declaring success.

## Limitations

- The `--run` pipeline is **Python-only** (`python <file>`); other languages
  in the allowlist aren't wired into it yet.
- Self-heal catches **crashes, not wrong answers** — a script that runs but
  produces an incorrect result is reported as "verified working".
- The command allowlist permits interpreters (`python`, `node`, …) that can run
  arbitrary code — use the container for anything you don't trust.
