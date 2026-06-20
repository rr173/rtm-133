import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Order, OrderItem, Picker, Bin, PickTask, Wave, WarehouseConfig
from schemas import (
    PickConfirmRequest,
    PickTaskResponse,
    WaveCreateRequest,
    WaveResponse,
    PathStep,
    PathPlanResponse,
)
from replenishment import check_and_create_replenish_task

router = APIRouter(prefix="/api", tags=["allocation"])


def _get_warehouse_config(db: Session):
    config = db.query(WarehouseConfig).filter(WarehouseConfig.name == "default").first()
    if not config:
        raise HTTPException(status_code=500, detail="默认仓库未配置")
    return config


def _compute_aisle_distance(aisle_idx_a: int, aisle_idx_b: int, aisle_spacing: int):
    return abs(aisle_idx_a - aisle_idx_b) * aisle_spacing


def plan_path_s_shape(bin_list: list[dict], aisle_spacing: int, aisle_length: int):
    if not bin_list:
        return [], 0

    by_aisle: dict[int, list[dict]] = {}
    for b in bin_list:
        aid = b["x"] // aisle_spacing if aisle_spacing > 0 else b["x"]
        by_aisle.setdefault(aid, []).append(b)

    for aid in by_aisle:
        by_aisle[aid].sort(key=lambda b: b["y"])

    sorted_aisles = sorted(by_aisle.keys())
    path = []
    total_distance = 0
    cur_x, cur_y = 0, 0

    for i, aid in enumerate(sorted_aisles):
        target_x = aid * aisle_spacing
        target_bins = by_aisle[aid]

        if i % 2 == 0:
            dist_to_aisle = abs(cur_y) + abs(target_x - cur_x) + 0
            total_distance += abs(target_x - cur_x) + cur_y
            cur_x = target_x
            cur_y = 0
            for b in target_bins:
                total_distance += b["y"] - cur_y
                cur_y = b["y"]
                path.append(b)
        else:
            total_distance += abs(target_x - cur_x) + (aisle_length - cur_y)
            cur_x = target_x
            cur_y = aisle_length
            for b in reversed(target_bins):
                total_distance += cur_y - b["y"]
                cur_y = b["y"]
                path.append(b)

    total_distance += cur_y + cur_x
    return path, total_distance


def plan_path_shortest(bin_list: list[dict], aisle_spacing: int, aisle_length: int,
                        start_x: int = 0, start_y: int = 0):
    if not bin_list:
        return [], 0

    by_aisle: dict[int, list[dict]] = {}
    for b in bin_list:
        aid = b["x"] // aisle_spacing if aisle_spacing > 0 else b["x"]
        by_aisle.setdefault(aid, []).append(b)

    for aid in by_aisle:
        by_aisle[aid].sort(key=lambda b: b["y"])

    remaining_aisles = set(by_aisle.keys())
    path = []
    total_distance = 0
    cur_x, cur_y = start_x, start_y

    while remaining_aisles:
        best_aid = None
        best_cost = float("inf")
        best_entry_end = "south"

        for aid in remaining_aisles:
            target_x = aid * aisle_spacing
            bins_in_aisle = by_aisle[aid]
            max_row = max(b["y"] for b in bins_in_aisle)
            min_row = min(b["y"] for b in bins_in_aisle)

            cost_south = abs(target_x - cur_x) + cur_y + max_row * 2
            cost_north = abs(target_x - cur_x) + (aisle_length - cur_y) + (aisle_length - min_row) * 2

            south_exit_back = max_row
            north_exit_back = aisle_length - min_row

            cost_south_total = abs(target_x - cur_x) + cur_y + south_exit_back * 2
            cost_north_total = abs(target_x - cur_x) + (aisle_length - cur_y) + north_exit_back * 2

            if cost_south_total <= cost_north_total:
                cost = cost_south_total
                entry = "south"
            else:
                cost = cost_north_total
                entry = "north"

            if cost < best_cost:
                best_cost = cost
                best_aid = aid
                best_entry_end = entry

        aid = best_aid
        remaining_aisles.remove(aid)
        target_x = aid * aisle_spacing
        bins_in_aisle = by_aisle[aid]
        max_row = max(b["y"] for b in bins_in_aisle)
        min_row = min(b["y"] for b in bins_in_aisle)

        if best_entry_end == "south":
            total_distance += abs(target_x - cur_x) + cur_y
            cur_x = target_x
            cur_y = 0
            total_distance += max_row
            cur_y = max_row
            for b in bins_in_aisle:
                path.append(b)
            total_distance += max_row
            cur_y = 0
        else:
            total_distance += abs(target_x - cur_x) + (aisle_length - cur_y)
            cur_x = target_x
            cur_y = aisle_length
            total_distance += (aisle_length - min_row)
            cur_y = min_row
            for b in reversed(bins_in_aisle):
                path.append(b)
            total_distance += (aisle_length - min_row)
            cur_y = aisle_length

    total_distance += cur_y + cur_x
    return path, total_distance


def allocate_order(db: Session, order: Order, picker: Picker, config: WarehouseConfig):
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()

    assigned_bins_map: dict[int, list[dict]] = {}
    bin_list = []
    ref_x, ref_y = picker.current_x, picker.current_y

    for item in items:
        candidate_bins = (
            db.query(Bin)
            .filter(
                Bin.sku_code == item.sku_code,
                Bin.quantity - Bin.frozen_quantity > 0,
            )
            .all()
        )

        remaining_qty = item.quantity
        item_assigned = []

        sorted_candidates = sorted(
            candidate_bins,
            key=lambda b: abs(b.x - ref_x) + abs(b.y - ref_y),
        )

        for cbin in sorted_candidates:
            if remaining_qty <= 0:
                break
            available = cbin.quantity - cbin.frozen_quantity
            take = min(available, remaining_qty)
            if take <= 0:
                continue

            item_assigned.append({
                "coordinate": cbin.coordinate,
                "allocated": take,
                "x": cbin.x,
                "y": cbin.y,
            })
            bin_list.append({
                "coordinate": cbin.coordinate,
                "sku_code": item.sku_code,
                "pick_quantity": take,
                "order_item_id": item.id,
                "x": cbin.x,
                "y": cbin.y,
            })
            remaining_qty -= take
            ref_x, ref_y = cbin.x, cbin.y

        if remaining_qty > 0:
            for alloc in item_assigned:
                bin_obj = db.query(Bin).filter(Bin.coordinate == alloc["coordinate"]).first()
                if bin_obj:
                    bin_obj.frozen_quantity -= alloc["allocated"]
            return False

        assigned_bins_map[item.id] = item_assigned

    for item in items:
        assigned = assigned_bins_map[item.id]
        item.assigned_bins = json.dumps(assigned)
        item.allocated_quantity = sum(a["allocated"] for a in assigned)
        item.status = "allocated"
        for alloc in assigned:
            bin_obj = db.query(Bin).filter(Bin.coordinate == alloc["coordinate"]).first()
            if bin_obj:
                bin_obj.frozen_quantity += alloc["allocated"]

    path, total_distance = plan_path_shortest(
        bin_list, config.aisle_spacing, config.aisle_length,
        picker.current_x, picker.current_y,
    )

    task = PickTask(
        order_id=order.id,
        picker_id=picker.id,
        status="allocated",
        path=json.dumps([{
            "coordinate": p["coordinate"],
            "sku_code": p["sku_code"],
            "pick_quantity": p["pick_quantity"],
            "order_item_id": p["order_item_id"],
            "x": p["x"],
            "y": p["y"],
        } for p in path]),
        path_details=json.dumps(path),
        total_distance=total_distance,
        current_step=0,
        created_at=datetime.utcnow(),
    )
    db.add(task)
    db.flush()

    order.status = "allocated"
    order.allocated_at = datetime.utcnow()
    order.picker_id = picker.id
    picker.status = "busy"
    picker.current_task_id = task.id

    db.commit()
    return True


def trigger_allocation(db: Session):
    config = _get_warehouse_config(db)
    pending_orders = (
        db.query(Order)
        .filter(Order.status == "pending")
        .order_by(Order.created_at)
        .all()
    )
    idle_pickers = db.query(Picker).filter(Picker.status == "idle").all()

    results = []
    for picker in idle_pickers:
        if not pending_orders:
            break
        for order in pending_orders[:]:
            success = allocate_order(db, order, picker, config)
            if success:
                pending_orders.remove(order)
                results.append({
                    "order_id": order.id,
                    "picker_id": picker.id,
                    "status": "allocated",
                })
                break

    return results


@router.post("/allocation/trigger")
def api_trigger_allocation(db: Session = Depends(get_db)):
    results = trigger_allocation(db)
    return {"allocated": results, "count": len(results)}


@router.post("/path/plan", response_model=PathPlanResponse)
def api_plan_path(
    coordinates: list[str],
    strategy: str = "shortest",
    db: Session = Depends(get_db),
):
    config = _get_warehouse_config(db)
    bins = db.query(Bin).filter(Bin.coordinate.in_(coordinates)).all()
    if not bins:
        raise HTTPException(status_code=404, detail="未找到指定库位")

    bin_list = [{"coordinate": b.coordinate, "x": b.x, "y": b.y, "sku_code": b.sku_code, "pick_quantity": 0} for b in bins]

    if strategy == "s_shape":
        path, distance = plan_path_s_shape(bin_list, config.aisle_spacing, config.aisle_length)
    else:
        path, distance = plan_path_shortest(bin_list, config.aisle_spacing, config.aisle_length)

    steps = [
        PathStep(
            coordinate=p["coordinate"],
            sku_code=p.get("sku_code"),
            pick_quantity=p.get("pick_quantity", 0),
            x=p["x"],
            y=p["y"],
        )
        for p in path
    ]
    return PathPlanResponse(steps=steps, total_distance=distance, strategy=strategy)


@router.post("/picking/{task_id}/start", response_model=PickTaskResponse)
def start_picking(task_id: int, db: Session = Depends(get_db)):
    task = db.query(PickTask).filter(PickTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="拣货任务不存在")
    if task.status != "allocated":
        raise HTTPException(status_code=400, detail="任务状态不允许开始拣货")

    task.status = "in_progress"
    task.started_at = datetime.utcnow()
    task.current_step = 0

    if task.order_id:
        order = db.query(Order).filter(Order.id == task.order_id).first()
        if order:
            order.status = "picking"
            order.picking_started_at = datetime.utcnow()
            items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
            for item in items:
                item.status = "picking"

    db.commit()
    db.refresh(task)
    return task


@router.post("/picking/{task_id}/pick/{coordinate}")
def confirm_pick(
    task_id: int,
    coordinate: str,
    req: PickConfirmRequest,
    db: Session = Depends(get_db),
):
    task = db.query(PickTask).filter(PickTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="拣货任务不存在")
    if task.status != "in_progress":
        raise HTTPException(status_code=400, detail="任务不在拣货中")

    path = json.loads(task.path)
    step_idx = task.current_step
    if step_idx >= len(path):
        raise HTTPException(status_code=400, detail="已超出路径范围")

    current_step = path[step_idx]
    if current_step["coordinate"] != coordinate:
        raise HTTPException(status_code=400, detail=f"当前应拣取库位{current_step['coordinate']}")

    bin_obj = db.query(Bin).filter(Bin.coordinate == coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")

    order_item_id = current_step.get("order_item_id")
    expected_qty = current_step["pick_quantity"]

    if req.exception:
        if order_item_id:
            item = db.query(OrderItem).filter(OrderItem.id == order_item_id).first()
            if item:
                item.status = "exception"
        unfreeze_qty = expected_qty
        if bin_obj.frozen_quantity >= unfreeze_qty:
            bin_obj.frozen_quantity -= unfreeze_qty
        else:
            bin_obj.frozen_quantity = 0
    else:
        actual_qty = req.actual_quantity if req.actual_quantity > 0 else expected_qty
        if bin_obj.quantity < actual_qty:
            if order_item_id:
                item = db.query(OrderItem).filter(OrderItem.id == order_item_id).first()
                if item:
                    item.status = "exception"
            if bin_obj.frozen_quantity >= expected_qty:
                bin_obj.frozen_quantity -= expected_qty
            else:
                bin_obj.frozen_quantity = 0
        else:
            bin_obj.quantity -= actual_qty
            if bin_obj.frozen_quantity >= expected_qty:
                bin_obj.frozen_quantity -= expected_qty
            else:
                bin_obj.frozen_quantity = 0
            bin_obj.pick_count += 1
            if order_item_id:
                item = db.query(OrderItem).filter(OrderItem.id == order_item_id).first()
                if item:
                    item.picked_quantity += actual_qty
                    if item.picked_quantity >= item.quantity:
                        item.status = "picked"
                    else:
                        item.status = "partial_picked"

    picker = db.query(Picker).filter(Picker.id == task.picker_id).first()
    if picker:
        picker.current_x = bin_obj.x
        picker.current_y = bin_obj.y

    task.current_step = step_idx + 1

    if task.current_step >= len(path):
        task.status = "completed"
        task.completed_at = datetime.utcnow()
        if picker:
            picker.status = "idle"
            picker.current_task_id = None
            picker.total_tasks += 1
            picker.total_distance += task.total_distance
            if task.started_at and task.completed_at:
                elapsed = (task.completed_at - task.started_at).total_seconds()
                picker.total_pick_time += elapsed
            picker.current_x = 0
            picker.current_y = 0

        if task.order_id:
            order = db.query(Order).filter(Order.id == task.order_id).first()
            if order:
                items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
                all_done = all(
                    it.status in ("picked", "exception", "partial_picked")
                    for it in items
                )
                any_exception = any(it.status == "exception" for it in items)
                if all_done:
                    order.status = "completed"
                    order.completed_at = datetime.utcnow()

    replenish_task = check_and_create_replenish_task(db, bin_obj)
    db.commit()
    return {
        "task_id": task_id,
        "step": task.current_step,
        "coordinate": coordinate,
        "task_status": task.status,
        "exception": req.exception,
        "replenishment_triggered": replenish_task is not None,
        "replenish_task_id": replenish_task.id if replenish_task else None,
    }


@router.post("/picking/{task_id}/complete")
def complete_picking(task_id: int, db: Session = Depends(get_db)):
    task = db.query(PickTask).filter(PickTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="拣货任务不存在")
    if task.status != "in_progress":
        raise HTTPException(status_code=400, detail="任务不在拣货中")

    task.status = "completed"
    task.completed_at = datetime.utcnow()

    picker = db.query(Picker).filter(Picker.id == task.picker_id).first()
    if picker:
        picker.status = "idle"
        picker.current_task_id = None
        picker.total_tasks += 1
        picker.total_distance += task.total_distance
        if task.started_at and task.completed_at:
            elapsed = (task.completed_at - task.started_at).total_seconds()
            picker.total_pick_time += elapsed
        picker.current_x = 0
        picker.current_y = 0

    if task.order_id:
        order = db.query(Order).filter(Order.id == task.order_id).first()
        if order:
            order.status = "completed"
            order.completed_at = datetime.utcnow()

    db.commit()
    return {"task_id": task_id, "status": "completed"}


@router.get("/picking/tasks", response_model=list[PickTaskResponse])
def list_pick_tasks(status: str = None, db: Session = Depends(get_db)):
    query = db.query(PickTask)
    if status:
        query = query.filter(PickTask.status == status)
    return query.order_by(PickTask.id.desc()).all()


@router.get("/picking/tasks/{task_id}", response_model=PickTaskResponse)
def get_pick_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(PickTask).filter(PickTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="拣货任务不存在")
    return task


@router.get("/picking/tasks/{task_id}/path")
def get_pick_task_path(task_id: int, db: Session = Depends(get_db)):
    task = db.query(PickTask).filter(PickTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="拣货任务不存在")
    path = json.loads(task.path) if task.path else []
    return {
        "task_id": task_id,
        "current_step": task.current_step,
        "total_steps": len(path),
        "total_distance": task.total_distance,
        "path": path,
    }


def _check_wave_eligibility(order_ids: list[int], db: Session):
    orders = []
    for oid in order_ids:
        order = db.query(Order).filter(Order.id == oid).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"订单{oid}不存在")
        if order.status != "pending":
            raise HTTPException(status_code=400, detail=f"订单{oid}状态为{order.status},无法加入波次")
        orders.append(order)

    if len(orders) > 5:
        raise HTTPException(status_code=400, detail="波次最多合并5个订单")

    all_bin_coords = set()
    for order in orders:
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        for item in items:
            bins = db.query(Bin).filter(
                Bin.sku_code == item.sku_code,
                Bin.quantity - Bin.frozen_quantity > 0,
            ).all()
            for b in bins:
                all_bin_coords.add((b.x, b.y, b.coordinate))

    config = _get_warehouse_config(db)
    aisle_counts: dict[int, int] = {}
    for bx, by, coord in all_bin_coords:
        aisle_idx = bx // config.aisle_spacing if config.aisle_spacing > 0 else bx
        aisle_counts[aisle_idx] = aisle_counts.get(aisle_idx, 0) + 1

    top3 = sorted(aisle_counts.items(), key=lambda x: -x[1])[:3]
    top3_count = sum(c for _, c in top3)
    total = len(all_bin_coords)

    if total == 0 or top3_count / total < 0.5:
        raise HTTPException(
            status_code=400,
            detail=f"订单库位不满足波次合并条件: 前3通道占比{top3_count/total*100:.1f}%<50%",
        )

    return orders, all_bin_coords


@router.post("/waves/create", response_model=WaveResponse)
def create_wave(req: WaveCreateRequest, db: Session = Depends(get_db)):
    orders, all_bin_coords = _check_wave_eligibility(req.order_ids, db)
    config = _get_warehouse_config(db)

    idle_pickers = db.query(Picker).filter(Picker.status == "idle").all()
    if not idle_pickers:
        raise HTTPException(status_code=400, detail="无空闲拣货员")

    picker = idle_pickers[0]

    wave = Wave(
        picker_id=picker.id,
        status="allocated",
        created_at=datetime.utcnow(),
    )
    db.add(wave)
    db.flush()

    bin_list = []
    for order in orders:
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        for item in items:
            candidate_bins = (
                db.query(Bin)
                .filter(
                    Bin.sku_code == item.sku_code,
                    Bin.quantity - Bin.frozen_quantity > 0,
                )
                .all()
            )
            remaining = item.quantity
            assigned = []
            sorted_candidates = sorted(
                candidate_bins,
                key=lambda b: abs(b.x - picker.current_x) + abs(b.y - picker.current_y),
            )
            for cbin in sorted_candidates:
                if remaining <= 0:
                    break
                available = cbin.quantity - cbin.frozen_quantity
                take = min(available, remaining)
                if take <= 0:
                    continue
                assigned.append({
                    "coordinate": cbin.coordinate,
                    "allocated": take,
                    "x": cbin.x,
                    "y": cbin.y,
                })
                bin_list.append({
                    "coordinate": cbin.coordinate,
                    "sku_code": item.sku_code,
                    "pick_quantity": take,
                    "order_item_id": item.id,
                    "x": cbin.x,
                    "y": cbin.y,
                })
                remaining -= take

            if remaining > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"SKU {item.sku_code} 库存不足以满足波次需求",
                )

            item.assigned_bins = json.dumps(assigned)
            item.allocated_quantity = sum(a["allocated"] for a in assigned)
            item.status = "allocated"
            for alloc in assigned:
                bin_obj = db.query(Bin).filter(Bin.coordinate == alloc["coordinate"]).first()
                if bin_obj:
                    bin_obj.frozen_quantity += alloc["allocated"]

    path, total_distance = plan_path_shortest(
        bin_list, config.aisle_spacing, config.aisle_length,
        picker.current_x, picker.current_y,
    )

    task = PickTask(
        wave_id=wave.id,
        picker_id=picker.id,
        status="allocated",
        path=json.dumps([{
            "coordinate": p["coordinate"],
            "sku_code": p["sku_code"],
            "pick_quantity": p["pick_quantity"],
            "order_item_id": p["order_item_id"],
            "x": p["x"],
            "y": p["y"],
        } for p in path]),
        path_details=json.dumps(path),
        total_distance=total_distance,
        current_step=0,
        created_at=datetime.utcnow(),
    )
    db.add(task)
    db.flush()

    for order in orders:
        order.status = "allocated"
        order.allocated_at = datetime.utcnow()
        order.wave_id = wave.id
        order.picker_id = picker.id

    picker.status = "busy"
    picker.current_task_id = task.id

    db.commit()
    db.refresh(wave)

    return WaveResponse(
        id=wave.id,
        picker_id=wave.picker_id,
        status=wave.status,
        order_ids=req.order_ids,
        created_at=wave.created_at,
    )


@router.get("/waves", response_model=list[WaveResponse])
def list_waves(db: Session = Depends(get_db)):
    waves = db.query(Wave).order_by(Wave.id.desc()).all()
    result = []
    for w in waves:
        orders = db.query(Order).filter(Order.wave_id == w.id).all()
        result.append(WaveResponse(
            id=w.id,
            picker_id=w.picker_id,
            status=w.status,
            order_ids=[o.id for o in orders],
            created_at=w.created_at,
        ))
    return result


@router.get("/waves/{wave_id}", response_model=WaveResponse)
def get_wave(wave_id: int, db: Session = Depends(get_db)):
    wave = db.query(Wave).filter(Wave.id == wave_id).first()
    if not wave:
        raise HTTPException(status_code=404, detail="波次不存在")
    orders = db.query(Order).filter(Order.wave_id == wave.id).all()
    return WaveResponse(
        id=wave.id,
        picker_id=wave.picker_id,
        status=wave.status,
        order_ids=[o.id for o in orders],
        created_at=wave.created_at,
    )
