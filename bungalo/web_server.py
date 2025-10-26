from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bungalo.app_manager import AppManager, TaskNotFoundError

app = FastAPI(title="Bungalo Control Plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskSubmission(BaseModel):
    value: str


@app.get("/api/state")
async def read_state():
    manager = AppManager.get()
    return await manager.get_state()


@app.get("/api/tasks/{task_id}")
async def read_task(task_id: str):
    manager = AppManager.get()
    state = await manager.get_state()
    task = next((task for task in state["tasks"] if task["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/tasks/{task_id}")
async def submit_task(task_id: str, submission: TaskSubmission):
    manager = AppManager.get()
    try:
        await manager.submit_task_value(task_id, submission.value)
    except TaskNotFoundError as exc:  # pragma: no cover - FastAPI handles response
        raise HTTPException(status_code=404, detail="Task not found") from exc
    return {"status": "submitted"}


@app.get("/healthz")
async def healthcheck():
    return {"status": "ok"}


__all__ = ["app"]
