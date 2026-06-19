from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Picker, Bin, Order, OrderItem, PickTask
from schemas import PickerStatsResponse, BinHeatResponse, OrderStatsResponse

router = APIRouter(prefix="/api/stats", tags=["statistics"])


@router.get("/pickers", response_model=list[PickerStatsResponse])
def all_picker_stats(db: Session = Depends(get_db)):
    pickers = db.query(Picker).all()
    result = []
    for p in pickers:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_tasks = (
            db.query(PickTask)
            .filter(
                PickTask.picker_id == p.id,
                PickTask.completed_at >= today_start,
            )
            .all()
        )
        today_count = len(today_tasks)
        today_distance = sum(t.total_distance for t in today_tasks)
        avg_time = 0.0
        if today_tasks:
            times = []
            for t in today_tasks:
                if t.started_at and t.completed_at:
                    times.append((t.completed_at - t.started_at).total_seconds())
            if times:
                avg_time = sum(times) / len(times)

        result.append(PickerStatsResponse(
            picker_id=p.id,
            name=p.name,
            today_tasks=today_count,
            today_distance=today_distance,
            avg_time_per_order=round(avg_time, 2),
        ))
    return result


@router.get("/pickers/{picker_id}", response_model=PickerStatsResponse)
def picker_stats(picker_id: int, db: Session = Depends(get_db)):
    picker = db.query(Picker).filter(Picker.id == picker_id).first()
    if not picker:
        raise HTTPException(status_code=404, detail="拣货员不存在")

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_tasks = (
        db.query(PickTask)
        .filter(
            PickTask.picker_id == picker.id,
            PickTask.completed_at >= today_start,
        )
        .all()
    )
    today_count = len(today_tasks)
    today_distance = sum(t.total_distance for t in today_tasks)
    avg_time = 0.0
    if today_tasks:
        times = []
        for t in today_tasks:
            if t.started_at and t.completed_at:
                times.append((t.completed_at - t.started_at).total_seconds())
        if times:
            avg_time = sum(times) / len(times)

    return PickerStatsResponse(
        picker_id=picker.id,
        name=picker.name,
        today_tasks=today_count,
        today_distance=today_distance,
        avg_time_per_order=round(avg_time, 2),
    )


@router.get("/bin-heat", response_model=list[BinHeatResponse])
def bin_heat_stats(top: int = 20, db: Session = Depends(get_db)):
    bins = (
        db.query(Bin)
        .filter(Bin.pick_count > 0)
        .order_by(Bin.pick_count.desc())
        .limit(top)
        .all()
    )
    max_row_ref = db.query(Bin).order_by(Bin.row.desc()).first()
    max_row = max_row_ref.row if max_row_ref else 20

    result = []
    for b in bins:
        suggestion = None
        if b.row > max_row // 2 and b.pick_count > 5:
            suggestion = f"建议前置: 该库位拣取{b.pick_count}次, 当前位于第{b.row}排, 建议移至靠近通道入口的库位"
        result.append(BinHeatResponse(
            coordinate=b.coordinate,
            sku_code=b.sku_code,
            pick_count=b.pick_count,
            suggestion=suggestion,
        ))
    return result


@router.get("/orders", response_model=OrderStatsResponse)
def order_stats(db: Session = Depends(get_db)):
    orders = db.query(Order).all()
    total = len(orders)

    fulfillment_times = []
    exception_count = 0

    for order in orders:
        if order.created_at and order.completed_at:
            elapsed = (order.completed_at - order.created_at).total_seconds()
            fulfillment_times.append(elapsed)

        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        for item in items:
            if item.status == "exception":
                exception_count += 1

    avg_fulfillment = sum(fulfillment_times) / len(fulfillment_times) if fulfillment_times else 0.0

    total_items = db.query(OrderItem).count()
    exception_rate = exception_count / total_items if total_items > 0 else 0.0

    return OrderStatsResponse(
        total_orders=total,
        avg_fulfillment_seconds=round(avg_fulfillment, 2),
        exception_rate=round(exception_rate, 4),
    )
