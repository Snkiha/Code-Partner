# Local Terminal Coding Partner (MCP + Ollama)

A private, lightweight, and blazing-fast terminal-based pair programmer that runs 100% locally on your machine. This project utilizes the **Model Context Protocol (MCP)** to securely bridge a local inference engine (**Ollama**) to your computer's filesystem, transforming an isolated LLM into an active, tool-capable programming partner.

## 🚀 Features

- **100% Local & Private:** No API keys required, and your code never leaves your local machine.
- **Native Filesystem Access:** Powered by the official MCP Filesystem Server, allowing the model to list directories, read source code, create scripts, and write patches.
- **Optimized Tool-Calling Architecture:** Tailored handling for local models to prevent structural failure, with automated fallbacks to capture typed plain-text tool blocks.
- **Robust Memory Guardrails:** Programmatically forces expanded context windows (`num_ctx: 16384`) so complex tool schemas don't cause model memory collapse.
- **Beautiful Terminal Interface:** Powered by `rich` for elegant markdown parsing, syntax highlighting, and visual execution statuses.

---

## 🛠️ Architecture Blueprint

The framework acts as an agentic loop implementing the **ReAct (Reason + Action)** pattern:

1. **User Input** is processed and appended to a persistent conversation history.
2. The python client wraps the history alongside the available **MCP Tool Schemas** and targets **Ollama**.
3. **Ollama** decides whether it needs an external resource to fulfill the request.
4. If a tool call is generated, the python host intercepts the instruction, transfers execution to the background **MCP Server** via standard input/output (`stdio`), and returns the raw file/directory data to the LLM.
5. The LLM reviews the resource data and delivers the finalized fix back to your terminal window.

---

## 📦 Prerequisites

Before getting started, ensure you have the following environmental engines installed:

1. **Ollama:** Download and run [Ollama](https://ollama.com/) locally.
2. **Node.js (v18+):** Required to run the background filesystem server dependencies over `npx`. Check compatibility using:
   ```bash
   node -v
   npx -v

## Install Python Dependencies

Requires **Python 3.11+** (uses `ExceptionGroup` handling).

```bash
pip install -r requirements.txt
```

## Running the Agent
```bash
python main.py
```

Commands inside the session:

| Command | Effect |
| --- | --- |
| `--run <request>` | Writer → Execute → Self-Heal loop |
| `--review <request>` | Writer → Reviewer pipeline |
| `+pin <path>` / `+unpin <path>` / `+pins` | Manage permanent-context files |
| anything else | Conversational agent (full filesystem + bash access) |

The Writer / Reviewer / Healer only ever read and write inside a `workspace/`
directory (git-ignored), so a run can't overwrite your source, `README.md`, or
`requirements.txt`. The conversational agent still has full repo access.

## Docker Deployment
```bash 
docker build -t local-coder .
```
### Running the Sandbox

The `run_command` tool executes model-generated code, so run the container with
the tightest limits your workflow allows:

```bash
docker run -it \
  --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/my_project:/workspace" \
  --pids-limit=256 \
  --memory=2g \
  --cpus=2 \
  --read-only --tmpfs /tmp \
  local-coder
```

Notes:
- `--pids-limit` contains fork bombs; `--memory` / `--cpus` cap runaway processes.
- Omit `--network none` only because the filesystem MCP server is fetched via
  `npx` at startup. If you bake it into the image, add `--network none` to cut
  off package downloads and exfiltration entirely.
- The container already runs as a non-root `agent` user.
