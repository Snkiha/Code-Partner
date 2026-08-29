import ast
import asyncio
import json
import os
import sys

# The UI (rich panels, rules) uses box-drawing chars and emoji. On Windows those
# crash with UnicodeEncodeError the moment stdout is not UTF-8 — a plain cmd.exe
# (cp1252), or any redirected / piped output. Force UTF-8 before anything prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from ollama import AsyncClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from pathlib import Path

console = Console()
ollama_client = AsyncClient()

# mistral:7b is the most reliable *structured* tool-caller of the small local
# models tried here (qwen2.5-coder:7b tends to echo the tool schema back as a
# string). Its weak spots — narrating a fix as prose, or emitting the call as
# JSON text, or mangling indentation — are handled downstream by the text
# tool-call recovery, the require_tool nudge, and the Writer syntax gate.
WRITER_MODEL = "qwen2.5-coder:7b"
REVIEWER_MODEL = "qwen3.5:0.8b"
CHAT_MODEL = "qwen3.5:0.8b"
HEALER_MODEL = "qwen2.5-coder:7b"

MAX_HEAL_ROUNDS = 5  # max write->output cycles before giving up
HEAL_WRITE_RETRIES = 2  # within one round, re-prompt if the Healer narrates instead of calling a write tool

SCRIPT_DIR = Path(__file__).resolve().parent
BASH_SERVER_ERRLOG = SCRIPT_DIR / "bash_mcp_server.stderr.log"

# Define the MCP Server parameters.
# Pin the filesystem server version — an unpinned "@latest" via npx means a
# silent dependency upgrade (and potential tool-name/behaviour drift) on any run.
FS_SERVER_PKG = "@modelcontextprotocol/server-filesystem@2026.7.10"
server_params = StdioServerParameters(
    command="npx",
    args=["-y", FS_SERVER_PKG, os.getcwd()]
)

# IMPORTANT: use sys.executable, not a re-resolved "python"/"python3" from PATH.
# shutil.which("python") can find a *different* interpreter (e.g. the Windows
# Store alias, or another install) that doesn't have `mcp` installed, which
# makes the subprocess crash on import before it ever prints anything —
# the symptom is a ClosedResourceError with no visible traceback.
# Path is made absolute so this also doesn't depend on the cwd you launch from.
bash_server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(SCRIPT_DIR / "bash_mcp_server.py")],
    cwd=str(SCRIPT_DIR),
)

# --HELPER FUNCTIONS-- #

def _tool_schema(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        }
        for tool in mcp_tools.tools
    ]

def _merge_tools(*schemas: list) -> list:
    seen, merged = set(), []
    for schema in schemas:
        for tool in schema:
            name = tool["function"]["name"]
            if name not in seen:
                seen.add(name)
                merged.append(tool)
    return merged

def _filter_tools(schema: list, names: set[str]) -> list:
    """
    Narrow a merged tool schema down to just the tools a specific sub-agent
    needs. Small local models (7B and under) get noticeably less reliable at
    emitting a real tool call as the number of tool schemas offered grows —
    handing every agent the full 15-tool fs+bash set caused the Writer to
    consistently narrate a fake tool call instead of invoking write_file.
    """
    return [tool for tool in schema if tool["function"]["name"] in names]

async def _call_tool(sessions: dict, name: str, args: dict) -> tuple[str, bool]:
    """
    Call `name` on whichever session supports it.
    Returns (text, is_error) — callers must check is_error explicitly instead of
    guessing from the text (server error messages don't reliably start with
    the word "Error", e.g. Node's `ENOENT: no such file or directory`).
    """
    last_error = None
    for session in sessions.values():
        try:
            result = await session.call_tool(name, arguments=args)
        except Exception as exc:
            last_error = str(exc)
            continue

        text = "".join(getattr(item, "text", str(item)) for item in result.content)
        if getattr(result, "isError", False):
            last_error = text
            continue

        return text, False

    return f"no session could handle tool '{name}' ({last_error})", True

def _unwrap_arg(value):
    """
    Some local models wrap a scalar arg as {"type": "string", "content": "..."}
    or {"value": ...}. Unwrap so write_file gets a plain string for 'content'.
    """
    if isinstance(value, dict):
        for key in ("content", "value", "text"):
            inner = value.get(key)
            if isinstance(inner, (str, int, float, bool)):
                return inner
    return value


def _first_json_object(text: str) -> dict | None:
    """Return the first brace-balanced JSON object in `text` that parses, else None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    for kw in ({}, {"strict": False}):  # strict=False tolerates literal newlines in strings
                        try:
                            obj = json.loads(chunk, **kw)
                            if isinstance(obj, dict):
                                return obj
                        except json.JSONDecodeError:
                            continue
                    break
        start = text.find("{", start + 1)
    return None


def _recover_text_tool_call(content: str) -> tuple[str, dict] | None:
    """
    Small local models via Ollama frequently emit a tool call as JSON *text* in
    the message body instead of in the structured tool_calls field — e.g.
        {"name": "write_file", "arguments": {"path": "x.py", "content": "..."}}
    (bare or inside a ```json fence). Recover it so the agent still acts.
    """
    if not content or '"name"' not in content:
        return None
    obj = _first_json_object(content)
    if not obj:
        return None
    name = obj.get("name") or obj.get("tool")
    if not isinstance(name, str):
        return None
    raw_args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError:
            raw_args = {}
    if not isinstance(raw_args, dict):
        return None
    return name, {k: _unwrap_arg(v) for k, v in raw_args.items()}


async def _run_tool_loop(
    sessions: dict, ollama_tools: list, messages: list, model: str, label: str,
    *, max_rounds: int = 6, require_tool: set[str] | None = None,
) -> tuple[str, list]:
    """
    Drive a single agent through up to 'max_rounds' of tool-call cycles.
    Returns (final_text, tool_calls_made) where tool_calls_made is a list of
    (name, args) tuples in the order they were called — callers can inspect
    this instead of parsing the model's natural-language summary.

    require_tool: if given, a text-only response does NOT end the loop until at
    least one of those tools has actually been called. Small local models love
    to answer "the fix is X" in prose without ever emitting the tool call; this
    nudges them back on task instead of returning an empty-handed result.
    """
    tool_calls_made: list = []

    for _ in range(max_rounds):
        response = await ollama_client.chat(
            model=model,
            messages=messages,
            tools=ollama_tools,
            options={"num_ctx": 16384, "temperature": 0.1}  # Forces a larger memory buffer
        )

        # Normalise structured tool_calls and text-embedded ones into one list.
        calls: list[tuple[str, dict]] = []
        for tc in response.message.tool_calls or []:
            calls.append((tc.function.name, tc.function.arguments))
        recovered = False
        if not calls:
            salvaged = _recover_text_tool_call(response.message.content or "")
            if salvaged:
                calls = [salvaged]
                recovered = True

        if not calls:
            content = response.message.content or ""
            messages.append(response.message)

            required_done = not require_tool or any(n in require_tool for n, _ in tool_calls_made)
            if required_done:
                return content, tool_calls_made

            console.print(f"[dim]  [{label}] answered in prose without a tool call — nudging…[/dim]")
            messages.append({
                "role": "user",
                "content": (
                    "You replied with text but made no tool call. You MUST call one of "
                    f"[{', '.join(sorted(require_tool))}] now to actually apply the change. "
                    "Do not explain — just make the call."
                ),
            })
            continue

        if recovered:
            console.print(f"[dim]  [{label}] recovered a text-embedded tool call: {calls[0][0]}[/dim]")

        # Agent wants to call one or more tools
        messages.append(response.message)
        for name, args in calls:
            if not isinstance(args, dict):
                args = {}
            tool_calls_made.append((name, args))
            console.print(f"[yellow]⚡ [{label}] calling '{name}' → {args}[/yellow]")
            try:
                result_text, is_error = await _call_tool(sessions, name, args)
                if is_error:
                    console.print(f"[red]Tool '{name}' returned an error: {result_text}[/red]")
            except Exception as exc:
                result_text = f"Tool error: {exc}"
                console.print(f"[red]Tool error: {exc}[/red]")

            messages.append({"role": "tool", "content": result_text, "name": name})

    # Max rounds hit - ask for final summary with no tools
    final = await ollama_client.chat(model=model, messages=messages, options={"num_ctx": 16384})
    messages.append(final.message)
    return final.message.content or "", tool_calls_made

WRITER_SYSTEM_PROMPT = (
    "You are a code-writing tool. For every request, respond ONLY by calling the write_file tool — "
    "never write code, explanations, or commentary in your message text. "
    "Put the complete, working Python source in the 'content' argument and a snake_case '<name>.py' "
    "filename in the 'path' argument. Do not narrate the call.\n"
    "Indentation rules (strict): use exactly 4 spaces per indentation level, never tab characters, "
    "and keep it consistent. The file you write must parse and run as-is."
)

WRITER_MAX_ATTEMPTS = 3  # retry when the model emits no write_file call, or writes code that doesn't parse


_MEANINGFUL_NODES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Import, ast.ImportFrom,
    ast.Assign, ast.AnnAssign, ast.AugAssign,
    ast.For, ast.AsyncFor, ast.While, ast.If, ast.With, ast.AsyncWith, ast.Try,
)


def _python_content_problem(source: str) -> str | None:
    """
    Return a one-line reason the source is unusable as a script, else None.
    Catches both parse failures (mangled indentation, tabs) and "parses fine but
    isn't real code" — e.g. a model that echoed the tool schema as a bare dict
    literal, which ast.parse happily accepts and `python file.py` runs to a
    no-op exit 0.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # covers IndentationError / TabError
        where = f" (line {exc.lineno})" if exc.lineno else ""
        return f"{type(exc).__name__}: {exc.msg}{where}"

    for node in tree.body:
        if isinstance(node, _MEANINGFUL_NODES):
            return None
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return None  # a top-level call like print(...) counts
    return "file has no runnable statements (no function, call, import, loop or assignment)"


# -- WRITER AGENT -- #
async def run_writer(sessions, ollama_tools, user_request: str) -> tuple[str, str | None]:
    """
    Writer Agent: Generates code and saves it to disk via MCP.
    Returns (response_text, filename) — filename is taken directly from the
    write_file tool call's arguments (ground truth), never parsed from the
    model's text response. A .py file that does not parse is rejected and the
    Writer is retried with the syntax error, so execution never receives
    structurally broken code (e.g. mangled indentation).
    """
    console.print(Rule("[bold cyan]Writer agent[/bold cyan]"))

    response_text = ""
    saved_filename = None
    syntax_feedback = None

    for attempt in range(1, WRITER_MAX_ATTEMPTS + 1):
        user_content = user_request
        if syntax_feedback:
            user_content = (
                f"{user_request}\n\n"
                f"Your previous file did NOT parse — {syntax_feedback}\n"
                "Rewrite the COMPLETE file as valid Python: 4-space indents, no tabs."
            )
        messages = [
            {"role": "system", "content": WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        response_text, tool_calls = await _run_tool_loop(
            sessions, ollama_tools, messages, WRITER_MODEL, "Writer",
            require_tool={"write_file"},
        )

        saved_content = None
        for name, args in tool_calls:
            if name == "write_file" and isinstance(args, dict) and args.get("path"):
                saved_filename = args["path"]
                saved_content = args.get("content")

        if not saved_filename:
            console.print(f"[dim]Writer attempt {attempt}/{WRITER_MAX_ATTEMPTS} produced no write_file call — retrying…[/dim]")
            continue

        content_ok = True
        if str(saved_filename).endswith(".py") and isinstance(saved_content, str):
            syntax_feedback = _python_content_problem(saved_content)
            if syntax_feedback:
                content_ok = False
                console.print(
                    f"[yellow]Writer attempt {attempt}/{WRITER_MAX_ATTEMPTS}: "
                    f"{saved_filename} rejected ({syntax_feedback}) — retrying…[/yellow]"
                )
                continue

        if content_ok:
            console.print("\n[bold cyan]Writer:[/bold cyan]")
            console.print(Markdown(response_text or "No commentary."))
            return response_text, saved_filename

    console.print("\n[bold cyan]Writer:[/bold cyan]")
    console.print(Markdown(response_text or "No commentary."))
    # Only hand off a file if the last attempt's content actually passed the gate.
    # A file that never parsed / had no real code is worse than an honest failure —
    # `python file.py` on a bare dict literal exits 0 and looks like success.
    if saved_filename and not syntax_feedback:
        return response_text, saved_filename
    if saved_filename:
        console.print(
            f"[red]✗ Writer never produced usable code for [bold]{saved_filename}[/bold] "
            f"({syntax_feedback}).[/red]"
        )
    return response_text, None

# -- REVIEWER AGENT -- #
async def run_reviewer(sessions, ollama_tools, filename: str) -> None:
    """
    Reviewer Agent: Read the written file, critique it, write a .review.md file.
    """
    console.print(Rule("[bold magenta]Reviewer agent[/bold magenta]"))

    # Path-aware: filename.rsplit(".", 1) breaks on a dot in a parent directory
    # (e.g. "pkg.v2/main.py" -> "pkg"). with_suffix only touches the final component.
    review_file = str(Path(filename).with_suffix(".review.md"))

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior security-focused code reviewer. "
                "Use read_file to read the source file you are given, then write a thorough review covering:\n"
                "1. Security vulnerabilities (injection, path traversal, auth issues, secrets in code, etc.)\n"
                "2. Correctness bugs and edge cases\n"
                "3. Performance and resource concerns\n"
                "4. Code quality and maintainability\n"
                "5. A VERDICT: APPROVED / APPROVED_WITH_NOTES / NEEDS_CHANGES\n\n"
                f"Save the review as '{review_file}' using write_file. "
                "Use Markdown with clear headings for each section."
            )
        },
        {
            "role": "user",
            "content": f"Please review the file: {filename}"
        }
    ]
    response_text, _ = await _run_tool_loop(sessions, ollama_tools, messages, REVIEWER_MODEL, "Reviewer")

    console.print("\n[bold magenta]Reviewer:[/bold magenta]")
    console.print(Markdown(response_text or "*No commentary.*"))
    console.print(f"\n[green]✓ Review saved to [bold]{review_file}[/bold][/green]")

async def _file_exists(sessions: dict, filename: str) -> bool:
    """Ground-truth check via the filesystem MCP server, not model claims."""
    _, is_error = await _call_tool(sessions, "read_text_file", {"path": filename})
    return not is_error


async def _run_and_capture(sessions: dict, run_cmd: str) -> tuple[bool, str]:
    """
    Actually execute the command via the bash MCP tool ourselves and
    parse the real exit code out of run_command's own output format,
    instead of asking the model whether it succeeded.
    """
    raw, is_error = await _call_tool(sessions, "run_command", {"command": run_cmd})
    if is_error:
        return False, raw

    success = False
    for line in raw.splitlines():
        if line.startswith("Exit code:"):
            try:
                code = int(line.split(":", 1)[1].strip())
                success = (code == 0)
            except ValueError:
                pass
            break
    return success, raw


HEAL_SYSTEM_PROMPT = (
    "You are an autonomous debugging engineer. You are given the exact "
    "stdout/stderr/exit code from a script that FAILED. Follow these steps every time:\n"
    "1. Call read_text_file to read the current source of the script.\n"
    "2. Find the single root cause of the error.\n"
    "3. Call edit_file (preferred) or write_file to apply the fix to that same file. "
    "edit_file takes a list of {oldText, newText} pairs; write_file replaces the WHOLE file, "
    "so if you use write_file you must include the entire corrected source.\n"
    "You are NOT finished until you have made a tool call that writes the file. "
    "Do not run the script and do not claim success. "
    "Once the file is written, reply with exactly: PATCHED"
)


async def _heal_once(
    sessions, healer_tools, filename: str, run_cmd: str, raw_output: str, attempt: int
) -> bool:
    """
    One healing round as a self-contained conversation (fresh messages each time
    so the context stays small and focused). Returns True only if the Healer
    actually called a file-writing tool on the target file — ground truth, not
    the model's 'PATCHED' claim.
    """
    target = Path(filename).name

    for retry in range(1, HEAL_WRITE_RETRIES + 1):
        nudge = "" if retry == 1 else (
            "\n\nYour previous reply did NOT include a write_file/edit_file tool call. "
            "You must call the tool now — narrating the fix is not enough."
        )
        messages = [
            {"role": "system", "content": HEAL_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Script: {filename}\nCommand: {run_cmd}\n"
                f"Heal round {attempt}/{MAX_HEAL_ROUNDS} — earlier fixes have not resolved it.\n\n"
                f"Verified failure output (not self-reported):\n{raw_output}\n\n"
                f"Read the file, then patch it with edit_file or write_file.{nudge}"
            )},
        ]

        _, tool_calls = await _run_tool_loop(
            sessions, healer_tools, messages, HEALER_MODEL,
            f"Heal-{attempt}.{retry}", max_rounds=5,
            require_tool={"write_file", "edit_file"},
        )

        wrote_target = any(
            name in ("write_file", "edit_file")
            and isinstance(args, dict)
            and args.get("path")
            and Path(args["path"]).name == target
            for name, args in tool_calls
        )
        if wrote_target:
            return True

        console.print(
            f"[dim]Heal {attempt}.{retry}: Healer made no write to {target} — re-prompting…[/dim]"
        )

    return False


async def run_execute_heal(sessions, healer_tools, filename: str, run_cmd: str) -> None:
    """
    Self-healing loop with independent verification:
    1. Confirm the file actually exists before doing anything.
    2. WE run the command via _run_and_capture — not the model.
    3. If it fails, hand the REAL output to the Healer and require a real patch
       (verified by inspecting its tool calls, not its text).
    4. WE re-run and re-check. The model's claims of success are ignored.
    """
    console.print(Rule("[bold green]Executor + Self-Heal loop[/bold green]"))

    if not await _file_exists(sessions, filename):
        console.print(Panel(
            f"[bold red]✗ '{filename}' does not exist on disk — the Writer never wrote it "
            "(likely a narrated/hallucinated tool call instead of a real one).[/bold red]",
            title="Self-Heal aborted"
        ))
        return

    for attempt in range(1, MAX_HEAL_ROUNDS + 1):
        console.print(f"\n[dim]── Attempt {attempt}/{MAX_HEAL_ROUNDS} ──[/dim]")

        success, raw_output = await _run_and_capture(sessions, run_cmd)
        console.print(Markdown(f"```\n{raw_output}\n```"))

        if success:
            console.print(Panel(
                f"[bold green]✓ Script verified working after {attempt} attempt(s) "
                f"(exit code 0, confirmed independently).[/bold green]",
                title="Self-Heal"
            ))
            return

        if attempt == MAX_HEAL_ROUNDS:
            break

        patched = await _heal_once(sessions, healer_tools, filename, run_cmd, raw_output, attempt)
        if not patched:
            console.print(Panel(
                "[bold yellow]⚠ The Healer did not modify the file this round[/bold yellow] "
                f"(no verified write to {Path(filename).name} after {HEAL_WRITE_RETRIES} tries). "
                "Small local models sometimes narrate a fix without calling the tool. "
                "Moving to the next attempt anyway.",
                title="Self-Heal"
            ))

    console.print(Panel(
        f"[bold red]✗ Still failing after {MAX_HEAL_ROUNDS} verified attempts.[/bold red]\n"
        "Check the error output above for manual inspection.",
        title="Self-Heal"
    ))

async def run_mode(sessions, ollama_tools, user_request: str) -> None:
    writer_tools = _filter_tools(ollama_tools, {"write_file"})
    # Healer needs to read the current source and patch it. edit_file (line-based
    # oldText/newText edits) is far more reliable for a 7B model than making it
    # regenerate the whole file through write_file.
    healer_tools = _filter_tools(
        ollama_tools, {"read_text_file", "read_file", "edit_file", "write_file"}
    )

    _, filename = await run_writer(sessions, writer_tools, user_request)
    if not filename:
        console.print(Panel(
            "[bold red]✗ The Writer did not call write_file — no file was saved.[/bold red]\n"
            "This can happen with small local models on very simple requests. "
            "Try rephrasing the request or asking again.",
            title="Writer failed to act"
        ))
        return

    run_cmd = f"python {filename}"
    console.print(f"\n[dim]Running [bold]{run_cmd}[/bold]…[/dim]")
    await run_execute_heal(sessions, healer_tools, filename, run_cmd)

# -- PIN STORE -- #

pins: list[dict] = []

def _pin_add(raw_path: str) -> str:
    path = Path(raw_path).resolve()
    if not path.is_file():
        return f"[red]Not a file: {raw_path}[/red]"
    # if already pinned
    if any(p["path"] == str(path) for p in pins):
        return f"[yellow]Already pinned: {path.name}[/yellow]"
    try:
        content = path.read_text(errors="replace")
    except Exception as exc:
        return f"[red]Could not read {raw_path}: {exc}[/red]"
    pins.append({"path": str(path), "content": content, "name": path.name})
    return f"[green]Pinned: {path.name} ({len(content):,} chars)[/green]"

def _pin_remove(raw_path: str) -> str:
    path = str(Path(raw_path).resolve())
    before = len(pins)
    pins[:] = [p for p in pins if p["path"] != path]
    if len(pins) == before:
        return f"[yellow]Not pinned: {raw_path}[/yellow]"
    return f"[green]Unpinned: {Path(raw_path).name}[/green]"

def _pin_list() -> str:
    if not pins:
        return "[dim]No files pinned.[/dim]"
    lines = ["[bold]Pinned files:[/bold]"]
    for p in pins:
        lines.append(f"- {p['name']}  [dim]({len(p['content']):,} chars)  {p['path']}[/dim]")
    return "\n".join(lines)

def _build_messages(base_messages: list) -> list:
    """Prepend a pinned-files system message if anything is pinned"""
    if not pins:
        return base_messages
    sections = "\n\n".join(f"## {p['name']} ({p['path']})\n```python\n{p['content']}\n```" for p in pins)
    pin_msg = {
        "role": "system",
        "content": (
            "The following files have been pinned by the user as permanent context. "
            "Always take them into account when answering.\n\n"
            f"PINNED FILES:\n{sections}"
        )
    }
    # Insert after the first system message (the role prompt)
    return [base_messages[0], pin_msg] + base_messages[1:]

# -- REVIEW-MODE ORCHESTRATOR -- #

async def review_mode(sessions, ollama_tools, user_request: str) -> None:
    writer_tools = _filter_tools(ollama_tools, {"write_file"})
    reviewer_tools = _filter_tools(ollama_tools, {"read_text_file", "read_file", "write_file"})

    _, filename = await run_writer(sessions, writer_tools, user_request)

    if not filename:
        console.print(Panel(
            "[bold red]✗ The Writer did not call write_file — no file was saved. "
            "Skipping review step.[/bold red]",
            title="Writer failed to act"
        ))
        return

    console.print(f"\n[dim]Writer saved: [bold]{filename}[/bold]. Handing off to Reviewer…[/dim]")
    await run_reviewer(sessions, reviewer_tools, filename)


async def _preflight() -> bool:
    """
    Verify Ollama is reachable and every model this app uses is already pulled.
    Fail here with an actionable message instead of cryptically deep inside a
    pipeline on the first ollama_client.chat() call.
    """
    required = sorted({WRITER_MODEL, REVIEWER_MODEL, CHAT_MODEL, HEALER_MODEL})

    try:
        listed = await ollama_client.list()
    except Exception as exc:
        console.print(Panel(
            f"[bold red]Cannot reach Ollama:[/bold red] {type(exc).__name__}: {exc}\n\n"
            "Start it with [bold]ollama serve[/bold], or install from https://ollama.com/.",
            title="Preflight failed", border_style="red",
        ))
        return False

    available: set[str] = set()
    for m in getattr(listed, "models", []):
        name = getattr(m, "model", None) or getattr(m, "name", None)
        if name:
            available.add(name)
            available.add(name.split(":", 1)[0])  # tolerate an implicit ":latest"

    missing = [m for m in required if m not in available and m.split(":", 1)[0] not in available]
    if missing:
        pulls = "\n".join(f"  ollama pull {m}" for m in missing)
        console.print(Panel(
            f"[bold red]Missing Ollama models:[/bold red] {', '.join(missing)}\n\n"
            f"Pull them first:\n{pulls}",
            title="Preflight failed", border_style="red",
        ))
        return False

    return True


async def main():
    try:
        if not await _preflight():
            return

        with open(BASH_SERVER_ERRLOG, "w", encoding="utf-8") as bash_errlog:
            async with stdio_client(server_params) as (read_stream, write_stream), \
                    stdio_client(bash_server_params, errlog=bash_errlog) as (bash_read, bash_write):

                async with ClientSession(read_stream, write_stream) as fs_session, \
                        ClientSession(bash_read, bash_write) as bash_session:

                    await fs_session.initialize()
                    await bash_session.initialize()

                    all_sessions = {"fs": fs_session, "bash": bash_session}

                    fs_tools   = _tool_schema(await fs_session.list_tools())
                    bash_tools = _tool_schema(await bash_session.list_tools())
                    all_tools  = _merge_tools(fs_tools, bash_tools)

                    # System prompt optimized for a terminal developer environment
                    messages = [{
                        "role": "system",
                        "content": (
                            "You are an elite terminal-based pair programmer with access to "
                            "filesystem tools (read/write files) and a bash execution tool "
                            "(run shell commands). Use them freely. Be concise and direct."
                            )
                    }]

                    console.print(Panel(
                            "[bold green]Local Coding Partner — Bash Sandbox Edition[/bold green]\n\n"
                            "[bold]--review[/bold]〈request〉→ Writer → Reviewer pipeline\n"
                            "[bold]--run[/bold]〈request〉→ Write → Execute → Self-Heal loop\n"
                            "[bold]+pin[/bold]〈path〉→ Pin file as permanent context\n"
                            "[bold]+unpin[/bold]〈path〉→ Remove a pinned file\n"
                            "[bold]+pins[/bold] → List pinned files\n"
                            "Normal input → conversational agent\n\n"
                            "Type [bold]exit[/bold] to quit.",
                            title="System"
                    ))

                    # Interactive Terminal Loop
                    while True:
                        raw = console.input("\n[bold blue]You:[/bold blue]")
                        if raw.lower().strip() in ("exit", "quit"):
                            break

                        # Pin commands
                        if raw == "+pins":
                            console.print(_pin_list())
                            continue

                        if raw.startswith("+pin "):
                            console.print(_pin_add(raw.removeprefix("+pin ").strip()))
                            continue

                        if raw.startswith("+unpin "):
                            console.print(_pin_remove(raw.removeprefix("+unpin ").strip()))
                            continue

                        # Review Mode
                        if raw.lstrip().startswith("--review"):
                            user_request = raw.lstrip().removeprefix("--review").strip()
                            if not user_request:
                                console.print("[yellow]Please describe what you want to build.[/yellow]")
                                continue
                            await review_mode(all_sessions, all_tools, user_request)
                            continue

                        elif raw.startswith("--run"):
                            request = raw.removeprefix("--run").strip()
                            if not request:
                                console.print("[yellow]Usage: --run <what to build>[/yellow]")
                                continue
                            await run_mode(all_sessions, all_tools, request)

                        else:
                            # Normal conversational/agentic mode
                            messages.append({"role": "user", "content": raw})
                            output, _ = await _run_tool_loop(all_sessions, all_tools, _build_messages(messages), CHAT_MODEL, "Chat")

                            console.print("\n[bold magenta]Coding Partner:[/bold magenta]")
                            console.print(Markdown(output or "No response."))
    except Exception as exc:
        console.print(f"[bold red]Failed to start:[/bold red] {type(exc).__name__}: {exc}")
        if hasattr(exc, "exceptions"):
            for sub in exc.exceptions:
                console.print(f"  [red]→ {type(sub).__name__}: {sub}[/red]")
        bash_err_text = ""
        try:
            bash_err_text = BASH_SERVER_ERRLOG.read_text(errors="replace").strip()
        except Exception:
            pass
        if bash_err_text:
            console.print(Panel(bash_err_text, title=f"bash_mcp_server.py stderr ({BASH_SERVER_ERRLOG.name})", border_style="red"))
        import traceback
        traceback.print_exc()
        return

if __name__ == "__main__":
    asyncio.run(main())