from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Bin, ReplenishConfig, ReplenishTask
from schemas import (
    ReplenishConfigCreate,
    ReplenishConfigUpdate,
    ReplenishConfigResponse,
    ReplenishTaskResponse,
    ReplenishTaskCompleteRequest,
)

router = APIRouter(prefix="/api/replenishment", tags=["replenishment"])

DEFAULT_THRESHOLD = 5
DEFAULT_TARGET = 30


def get_replenish_config(db: Session, sku_code: str) -> ReplenishConfig:
    config = db.query(ReplenishConfig).filter(ReplenishConfig.sku_code == sku_code).first()
    if config:
        return config
    return ReplenishConfig(
        sku_code=sku_code,
        threshold=DEFAULT_THRESHOLD,
        target_quantity=DEFAULT_TARGET,
    )


def check_and_create_replenish_task(db: Session, bin_obj: Bin) -> ReplenishTask | None:
    if not bin_obj.sku_code:
        return None

    available = bin_obj.quantity - bin_obj.frozen_quantity
    config = get_replenish_config(db, bin_obj.sku_code)

    if available >= config.threshold:
        return None

    existing = (
        db.query(ReplenishTask)
        .filter(
            ReplenishTask.bin_coordinate == bin_obj.coordinate,
            ReplenishTask.sku_code == bin_obj.sku_code,
            ReplenishTask.status.in_(["pending", "in_progress"]),
        )
        .first()
    )
    if existing:
        return None

    required_qty = config.target_quantity - available
    if required_qty <= 0:
        return None

    task = ReplenishTask(
        bin_coordinate=bin_obj.coordinate,
        sku_code=bin_obj.sku_code,
        required_quantity=required_qty,
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(task)
    db.flush()
    return task


@router.post("/configs", response_model=ReplenishConfigResponse)
def create_replenish_config(req: ReplenishConfigCreate, db: Session = Depends(get_db)):
    existing = db.query(ReplenishConfig).filter(ReplenishConfig.sku_code == req.sku_code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"SKU {req.sku_code} 补货配置已存在")
    if req.threshold <= 0:
        raise HTTPException(status_code=400, detail="阈值必须大于0")
    if req.target_quantity <= req.threshold:
        raise HTTPException(status_code=400, detail="目标补货量必须大于阈值")

    config = ReplenishConfig(
        sku_code=req.sku_code,
        threshold=req.threshold,
        target_quantity=req.target_quantity,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/configs", response_model=list[ReplenishConfigResponse])
def list_replenish_configs(db: Session = Depends(get_db)):
    return db.query(ReplenishConfig).order_by(ReplenishConfig.sku_code).all()


@router.get("/configs/{sku_code}", response_model=ReplenishConfigResponse)
def get_replenish_config_api(sku_code: str, db: Session = Depends(get_db)):
    config = db.query(ReplenishConfig).filter(ReplenishConfig.sku_code == sku_code).first()
    if not config:
        return ReplenishConfigResponse(
            id=0,
            sku_code=sku_code,
            threshold=DEFAULT_THRESHOLD,
            target_quantity=DEFAULT_TARGET,
        )
    return config


@router.put("/configs/{sku_code}", response_model=ReplenishConfigResponse)
def update_replenish_config(sku_code: str, req: ReplenishConfigUpdate, db: Session = Depends(get_db)):
    config = db.query(ReplenishConfig).filter(ReplenishConfig.sku_code == sku_code).first()
    if not config:
        raise HTTPException(status_code=404, detail=f"SKU {sku_code} 补货配置不存在")

    if req.threshold is not None:
        if req.threshold <= 0:
            raise HTTPException(status_code=400, detail="阈值必须大于0")
        config.threshold = req.threshold
    if req.target_quantity is not None:
        if req.target_quantity <= config.threshold:
            raise HTTPException(status_code=400, detail="目标补货量必须大于阈值")
        config.target_quantity = req.target_quantity

    db.commit()
    db.refresh(config)
    return config


@router.post("/trigger-full-scan")
def trigger_full_replenishment_scan(db: Session = Depends(get_db)):
    all_bins = db.query(Bin).filter(Bin.sku_code.isnot(None)).all()
    created_tasks = []

    for bin_obj in all_bins:
        task = check_and_create_replenish_task(db, bin_obj)
        if task:
            created_tasks.append(task)

    db.commit()

    return {
        "message": f"全仓补货检测完成,扫描了{len(all_bins)}个库位",
        "created_count": len(created_tasks),
        "created_tasks": [
            {
                "id": t.id,
                "bin_coordinate": t.bin_coordinate,
                "sku_code": t.sku_code,
                "required_quantity": t.required_quantity,
            }
            for t in created_tasks
        ],
    }


@router.post("/scan-bin/{coordinate}")
def scan_single_bin(coordinate: str, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")

    task = check_and_create_replenish_task(db, bin_obj)
    db.commit()

    if task:
        return {
            "message": "已生成补货任务",
            "task": {
                "id": task.id,
                "bin_coordinate": task.bin_coordinate,
                "sku_code": task.sku_code,
                "required_quantity": task.required_quantity,
                "status": task.status,
            },
        }
    else:
        available = bin_obj.quantity - bin_obj.frozen_quantity if bin_obj.sku_code else 0
        return {
            "message": "无需补货",
            "bin_coordinate": coordinate,
            "sku_code": bin_obj.sku_code,
            "available": available,
        }


@router.get("/tasks", response_model=list[ReplenishTaskResponse])
def list_replenish_tasks(status: str = None, db: Session = Depends(get_db)):
    query = db.query(ReplenishTask)
    if status:
        query = query.filter(ReplenishTask.status == status)
    return query.order_by(ReplenishTask.id.desc()).all()


@router.get("/tasks/{task_id}", response_model=ReplenishTaskResponse)
def get_replenish_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ReplenishTask).filter(ReplenishTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="补货任务不存在")
    return task


@router.post("/tasks/{task_id}/start", response_model=ReplenishTaskResponse)
def start_replenish_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ReplenishTask).filter(ReplenishTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="补货任务不存在")
    if task.status != "pending":
        raise HTTPException(status_code=400, detail=f"任务状态为{task.status}, 无法开始执行")

    task.status = "in_progress"
    task.started_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/complete", response_model=ReplenishTaskResponse)
def complete_replenish_task(task_id: int, req: ReplenishTaskCompleteRequest, db: Session = Depends(get_db)):
    task = db.query(ReplenishTask).filter(ReplenishTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="补货任务不存在")
    if task.status not in ("pending", "in_progress"):
        raise HTTPException(status_code=400, detail=f"任务状态为{task.status}, 无法确认完成")
    if req.actual_quantity <= 0:
        raise HTTPException(status_code=400, detail="实际补货数量必须大于0")

    bin_obj = db.query(Bin).filter(Bin.coordinate == task.bin_coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail=f"库位{task.bin_coordinate}不存在")

    if bin_obj.sku_code and bin_obj.sku_code != task.sku_code:
        raise HTTPException(
            status_code=400,
            detail=f"库位{task.bin_coordinate}当前SKU为{bin_obj.sku_code}, 与任务SKU{task.sku_code}不一致",
        )

    bin_obj.sku_code = task.sku_code
    bin_obj.quantity += req.actual_quantity

    task.status = "completed"
    task.actual_quantity = req.actual_quantity
    task.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/cancel", response_model=ReplenishTaskResponse)
def cancel_replenish_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ReplenishTask).filter(ReplenishTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="补货任务不存在")
    if task.status not in ("pending", "in_progress"):
        raise HTTPException(status_code=400, detail=f"任务状态为{task.status}, 无法取消")

    task.status = "cancelled"
    task.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task
