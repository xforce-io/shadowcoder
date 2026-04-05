"""FastAPI dashboard server with SSE support."""
from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from sse_starlette.sse import EventSourceResponse

from shadowcoder.dashboard.state import DashboardState
from shadowcoder.dashboard.watcher import FileWatcher

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"


def create_app(repo_path: str) -> FastAPI:
    app = FastAPI(title="ShadowCoder Dashboard")
    state = DashboardState(repo_path)

    jinja_env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # SSE subscribers: {issue_id: [asyncio.Queue]}
    subscribers: dict[int, list[asyncio.Queue]] = {}

    async def broadcast(issue_id: int, event_type: str, data: str) -> None:
        for q in subscribers.get(issue_id, []):
            await q.put({"event": event_type, "data": data})

    async def on_file_change(event: dict) -> None:
        issue_id = event["issue_id"]
        file_type = event["file_type"]
        detail = state.get_issue_detail(issue_id)
        if detail is None:
            return

        if file_type == "log":
            template = jinja_env.get_template("partials/log.html")
            if detail["log_entries"]:
                entry = detail["log_entries"][-1]
                html = template.render(entry=entry)
                await broadcast(issue_id, "log", html)

        if file_type == "issue":
            sb_template = jinja_env.get_template("partials/status_bar.html")
            await broadcast(issue_id, "status", sb_template.render(issue=detail))
            pl_template = jinja_env.get_template("partials/pipeline.html")
            await broadcast(issue_id, "pipeline", pl_template.render(issue=detail))

        if file_type == "feedback":
            rv_template = jinja_env.get_template("partials/review.html")
            await broadcast(issue_id, "review", rv_template.render(issue=detail))

        if file_type == "metrics":
            gt_template = jinja_env.get_template("partials/gate.html")
            await broadcast(issue_id, "gate", gt_template.render(issue=detail))

        files_template = jinja_env.get_template("partials/files.html")
        await broadcast(issue_id, "files", files_template.render(issue=detail))

    watcher = FileWatcher(repo_path, on_file_change)

    @app.on_event("startup")
    async def startup():
        watcher.start()

    @app.on_event("shutdown")
    async def shutdown():
        watcher.stop()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, issue: int | None = None):
        issues = state.get_issue_list()
        template = jinja_env.get_template("dashboard.html")

        issue_detail = None
        if issue is not None:
            issue_detail = state.get_issue_detail(issue)
        elif issues:
            latest = max(issues, key=lambda i: i.get("updated", ""))
            issue_detail = state.get_issue_detail(latest["id"])

        return HTMLResponse(template.render(
            repo_path=repo_path,
            issues=issues,
            issue=issue_detail,
        ))

    @app.get("/api/issues")
    async def api_issues():
        return JSONResponse(state.get_issue_list())

    @app.get("/api/issues/{issue_id}")
    async def api_issue_detail(issue_id: int):
        detail = state.get_issue_detail(issue_id)
        if detail is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Serialize dataclasses to dicts for JSON
        result = {**detail}
        result["log_entries"] = [
            {"timestamp": e.timestamp, "text": e.text, "category": e.category,
             "continuation": e.continuation}
            for e in detail["log_entries"]
        ]
        result["pipeline"] = [
            {"name": s.name, "state": s.state}
            for s in detail["pipeline"]
        ]
        result["changed_files"] = [
            {"status": f.status, "path": f.path, "stat": f.stat}
            for f in detail["changed_files"]
        ]
        return JSONResponse(result)

    @app.get("/sse/issue/{issue_id}")
    async def sse_issue(request: Request, issue_id: int):
        queue: asyncio.Queue = asyncio.Queue()
        if issue_id not in subscribers:
            subscribers[issue_id] = []
        subscribers[issue_id].append(queue)

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield event
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
            finally:
                subscribers[issue_id].remove(queue)
                if not subscribers[issue_id]:
                    del subscribers[issue_id]

        return EventSourceResponse(event_generator())

    return app


def run_server(repo_path: str, host: str = "127.0.0.1", port: int = 8420) -> None:
    import uvicorn
    app = create_app(repo_path)
    print(f"Dashboard: http://{host}:{port}")
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
