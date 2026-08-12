# Graph Report - .  (2026-08-08)

## Corpus Check
- Corpus is ~3,054 words - fits in a single context window. You may not need a graph.

## Summary
- 51 nodes · 75 edges · 8 communities (7 shown, 1 thin omitted)
- Extraction: 93% EXTRACTED · 5% INFERRED · 1% AMBIGUOUS · INFERRED: 4 edges (avg confidence: 0.9)
- Token cost: 44,693 input · 0 output

## Community Hubs (Navigation)
- Agent Entry & Pin Management
- Agent Mode Orchestration
- Bash MCP Server
- Tool Execution & Healing
- Agent Architecture Rationale
- Rich Pydantic Dependencies
- MCP Dependency Stack
- Ollama Dependency

## God Nodes (most connected - your core abstractions)
1. `main()` - 10 edges
2. `agent.py (Python Host/Client)` - 8 edges
3. `_run_tool_loop()` - 7 edges
4. `run_execute_heal()` - 6 edges
5. `_call_tool()` - 5 edges
6. `run_writer()` - 5 edges
7. `run_mode()` - 5 edges
8. `review_mode()` - 5 edges
9. `_filter_tools()` - 4 edges
10. `run_reviewer()` - 4 edges

## Surprising Connections (you probably didn't know these)
- `Ollama` --shares_data_with--> `ollama (pinned dependency)`  [INFERRED]
  README.md → codePartner/requirements.txt
- `Model Context Protocol (MCP)` --shares_data_with--> `mcp (pinned dependency)`  [INFERRED]
  README.md → codePartner/requirements.txt
- `rich (Python terminal library)` --shares_data_with--> `rich (pinned dependency)`  [INFERRED]
  README.md → codePartner/requirements.txt

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **ReAct Agentic Loop Components** — readme_agent_py, readme_ollama, readme_mcp, readme_mcp_filesystem_server, readme_react_pattern [INFERRED 0.85]
- **Shared Python Dependency Stack (README install cmd + requirements.txt)** — readme_ollama, readme_mcp, readme_rich_lib, codepartner_requirements_ollama, codepartner_requirements_mcp, codepartner_requirements_rich [INFERRED 0.85]

## Communities (8 total, 1 thin omitted)

### Community 0 - "Agent Entry & Pin Management"
Cohesion: 0.33
Nodes (9): _build_messages(), main(), _merge_tools(), _pin_add(), _pin_list(), _pin_remove(), # IMPORTANT: use sys.executable, not a re-resolved "python"/"python3" from PATH., Prepend a pinned-files system message if anything is pinned (+1 more)

### Community 1 - "Agent Mode Orchestration"
Cohesion: 0.24
Nodes (10): _filter_tools(), Drive a single agent through up to 'max_rounds' of tool-call cycles. Returns…, Writer Agent: Generates code and saves it to disk via MCP. Returns…, Reviewer Agent: Read the written file, critique it, write a .review.md file., Narrow a merged tool schema down to just the tools a specific sub-agent needs.…, review_mode(), run_mode(), run_reviewer() (+2 more)

### Community 2 - "Bash MCP Server"
Cohesion: 0.29
Nodes (6): call_tool, call_tool(), list_tools(), bash_mcp_server.py — A minimal MCP server that exposes a controlled subprocess…, TextContent, Tool

### Community 3 - "Tool Execution & Healing"
Cohesion: 0.29
Nodes (8): _call_tool(), _file_exists(), Ground-truth check via the filesystem MCP server, not model claims., Actually execute the command via the bash MCP tool ourselves and parse the real…, Self-healing loop with independent verification: 1. Confirm the file actually…, Call `name` on whichever session supports it. Returns (text, is_error) —…, _run_and_capture(), run_execute_heal()

### Community 4 - "Agent Architecture Rationale"
Cohesion: 0.40
Nodes (5): agent.py (Python Host/Client), Docker Deployment, Robust Memory Guardrails (num_ctx=16384), ReAct (Reason + Action) Pattern, Optimized Tool-Calling Architecture

### Community 5 - "Rich Pydantic Dependencies"
Cohesion: 0.50
Nodes (3): rich (pinned dependency), pydantic (Python library), rich (Python terminal library)

### Community 6 - "MCP Dependency Stack"
Cohesion: 0.50
Nodes (4): mcp (pinned dependency), Model Context Protocol (MCP), MCP Filesystem Server, Node.js (v18+)

## Ambiguous Edges - Review These
- `pydantic (Python library)` → `codePartner/requirements.txt`  [AMBIGUOUS]
  README.md · relation: references

## Knowledge Gaps
- **2 isolated node(s):** `Docker Deployment`, `Node.js (v18+)`
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `pydantic (Python library)` and `codePartner/requirements.txt`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `agent.py (Python Host/Client)` connect `Agent Architecture Rationale` to `Rich Pydantic Dependencies`, `MCP Dependency Stack`, `Ollama Dependency`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Why does `_run_tool_loop()` connect `Agent Mode Orchestration` to `Agent Entry & Pin Management`, `Tool Execution & Healing`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Why does `run_execute_heal()` connect `Tool Execution & Healing` to `Agent Entry & Pin Management`, `Agent Mode Orchestration`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **What connects `Docker Deployment`, `Node.js (v18+)` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._