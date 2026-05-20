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

# Standout Features to add
1. Sequential Multi-Agent "Reviewer" Mode
Don't just have one model write the code. When a user asks for a feature, use your framework to instantiate two internal roles:

The Writer: (qwen2.5-coder:7b) generates the script and saves it via the filesystem MCP.

The Reviewer: (llama3.1:8b) reads the saved script, reviews it for security flaws or optimizations, and automatically appends a .review.md file or chats back fixes to the Writer.

2. A Local "Bash Execution" Sandbox Server
Build or attach a standard command-line execution MCP server (like the community mcp-server-commands or building a basic one using Python's subprocess). This allows the LLM to write code, call a tool to execute python script.py or npm test, read the terminal error messages directly, and patch its own bugs without you lifting a finger.

3. Contextual File Pinning
In your CLI loop, catch special command syntax. For example, if a user types:
+pin ./src/auth.py
Your framework intercepts this string, loads the content of auth.py, and pins it statically as a "system context message" at the top of the chat stack so the model always acts with explicit awareness of your core codebase structure.
