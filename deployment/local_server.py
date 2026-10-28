"""Loopback control panel for the existing runtime; samples are deployment checks only."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_runtime import __version__
from agent_runtime.config import load_config
from agent_runtime.evaluation.decision_workload import make_tasks
from agent_runtime.evaluation.runner import _source_tree_sha256
from agent_runtime.runtime.builder import build_context, build_default_agents
from agent_runtime.runtime.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / ".local" / "runs"
LOGS.mkdir(parents=True, exist_ok=True)
MODELS = {"M1": "doubao-seed-2-0-mini-260215", "M2": "qwen3.7-flash"}
SOURCE_SHA = _source_tree_sha256(ROOT / "final-submit" / "src")
ARMS = {"T0": ("text", False, False), "S0": ("structured", False, False),
        "SS0": ("structured", True, False), "T1": ("text", False, True),
        "S1": ("structured", False, True), "SS1": ("structured", True, True)}
app = FastAPI(title="灵犀栈本地运行控制台", version=__version__)
gate = asyncio.Lock()


class RunRequest(BaseModel):
    task_index: int = Field(default=0, ge=0, le=7)
    arm: str = Field(default="SS0", pattern="^(T0|S0|SS0|T1|S1|SS1)$")
    model: str = Field(default="M1", pattern="^(M1|M2)$")


@app.get("/api/health")
def health():
    cfg = load_config()
    return {"status": "ok", "version": __version__, "platform": platform.platform(),
            "source_tree_sha256": SOURCE_SHA,
            "provider": cfg.llm.provider, "base_url": cfg.llm.base_url,
            "model": cfg.llm.model, "api_key_configured": bool(cfg.llm.api_key),
            "max_tokens": cfg.llm.max_tokens, "timeout_sec": cfg.llm.timeout_sec,
            "max_attempts": cfg.llm.max_retries + 1, "extra_body": cfg.llm.extra_body,
            "control_transport": cfg.control_transport,
            "notice": "部署验证；Windows 进程内路径，不代表 openEuler IPC 或论文正式实验。"}


@app.get("/api/tasks")
def tasks():
    return [{"index": i, "task_id": t.task_id,
             "symptom": t.payload["facts"]["observations"]["symptom"]}
            for i, t in enumerate(make_tasks())]


def run_case(req: RunRequest):
    cfg = load_config()
    if cfg.llm.provider != "openai" or not cfg.llm.api_key:
        raise ValueError("请先在项目 .env 配置真实模型接口和密钥")
    body = {"thinking": {"type": "disabled"}} if req.model == "M1" else {"enable_thinking": False}
    cfg = cfg.with_overrides(
        llm=replace(cfg.llm, model=MODELS[req.model], extra_body=body),
        llm_coalesce="off", llm_advisory=False, llm_judge=False,
        llm_context_mode="inbound", text_style="lean", agent_processes=False,
        pipeline_topology="request_reply", state_shared_memory=False,
    )
    task = make_tasks()[req.task_index]
    mode, state, memory = ARMS[req.arm]
    ctx = build_context(cfg, mode, task_id=task.task_id, use_state_exchange=state,
                        use_memory=memory, use_sandbox=False)
    calls = []
    original_complete = ctx.llm.complete

    def audited_complete(prompt, **kwargs):
        started = time.perf_counter()
        call = {"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "model": cfg.llm.model}
        try:
            response = original_complete(prompt, **kwargs)
            call.update(prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
                        metadata=response.metadata,
                        response_sha256=hashlib.sha256(response.text.encode()).hexdigest())
            return response
        except Exception as exc:
            call["error_type"] = type(exc).__name__
            raise
        finally:
            call["wall_sec"] = time.perf_counter() - started
            calls.append(call)

    ctx.llm.complete = audited_complete
    run_id = uuid.uuid4().hex
    started = time.perf_counter()
    record = {"run_id": run_id, "purpose": "deployment_smoke_only", "real_provider": True,
              "source_tree_sha256": SOURCE_SHA,
              "created_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "task_id": task.task_id, "arm": req.arm, "model": cfg.llm.model,
              "memory_scope": "fresh store per request; cross-task reuse is not measured",
              "control_transport": cfg.control_transport, "agent_processes": False,
              "request_config": {"temperature": cfg.llm.temperature, "max_tokens": cfg.llm.max_tokens,
                                 "timeout_sec": cfg.llm.timeout_sec, "max_attempts": cfg.llm.max_retries+1,
                                 "extra_body": cfg.llm.extra_body}}
    try:
        result = asyncio.run(Orchestrator(build_default_agents(cfg)).run_task(task, ctx))
        record.update(success=result.success, outputs=result.outputs,
                      metrics=asdict(result.metrics), error=result.error)
    except Exception as exc:
        record.update(success=False, error={"type": type(exc).__name__})
    finally:
        record.update(wall_sec=time.perf_counter()-started, provider_calls=calls)
        (LOGS / f"{run_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


@app.post("/api/run")
async def run(req: RunRequest, request: Request):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Only same-origin local requests are accepted")
    async with gate:
        return await asyncio.to_thread(run_case, req)


@app.get("/")
def index():
    return FileResponse(ROOT / "deployment" / "index.html")


app.mount("/archive", StaticFiles(directory=ROOT / "final-submit" / "demo" / "dashboard", html=True), name="archive")
