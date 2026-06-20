import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base, get_db
from models import (
    WarehouseConfig, Aisle, Bin, Order, OrderItem, Picker, PickTask, Wave,
    ReplenishConfig, ReplenishTask, RelocationSuggestion, RelocationStats,
    PRIORITY_NORMAL, PRIORITY_URGENT, PRIORITY_SUPER_URGENT,
)
from warehouse import router as warehouse_router
from inventory import router as inventory_router
from order import router as order_router
from picker import router as picker_router
from allocation import router as allocation_router, trigger_allocation
from stats import router as stats_router
from replenishment import router as replenishment_router
from relocation import router as relocation_router

Base.metadata.create_all(bind=engine)


def preset_default_warehouse(db: Session):
    existing = db.query(WarehouseConfig).filter(WarehouseConfig.name == "default").first()
    if existing:
        return existing

    config = WarehouseConfig(
        name="default",
        num_aisles=6,
        aisle_length=20,
        aisle_spacing=2,
        is_default=True,
    )
    db.add(config)
    db.flush()

    for aisle_idx in range(config.num_aisles):
        aisle_name = chr(ord("A") + aisle_idx)
        aisle = Aisle(
            warehouse_id=config.id,
            name=aisle_name,
            index=aisle_idx,
        )
        db.add(aisle)
        db.flush()

        for row in range(1, config.aisle_length + 1):
            for level in (1, 2):
                coordinate = f"{aisle_name}-{row:02d}-{level:02d}"
                x = aisle_idx * config.aisle_spacing
                y = row
                bin_obj = Bin(
                    aisle_id=aisle.id,
                    warehouse_id=config.id,
                    row=row,
                    level=level,
                    coordinate=coordinate,
                    x=x,
                    y=y,
                    quantity=0,
                    frozen_quantity=0,
                )
                db.add(bin_obj)

    db.flush()
    return config


def preset_skus(db: Session, config: WarehouseConfig):
    all_bins = db.query(Bin).filter(Bin.warehouse_id == config.id).all()
    if not all_bins:
        return

    sku_codes = [f"SKU{i:03d}" for i in range(1, 51)]

    bin_index = 0
    shuffled_bins = list(all_bins)
    random.shuffle(shuffled_bins)

    for sku in sku_codes:
        num_locations = random.randint(2, 4)
        for _ in range(num_locations):
            if bin_index >= len(shuffled_bins):
                bin_index = 0
                random.shuffle(shuffled_bins)
            target_bin = shuffled_bins[bin_index]
            target_bin.sku_code = sku
            target_bin.quantity = random.randint(10, 50)
            bin_index += 1

    db.flush()


def preset_pickers(db: Session):
    existing = db.query(Picker).first()
    if existing:
        return
    for i in range(1, 4):
        picker = Picker(
            name=f"拣货员{i}",
            status="idle",
            current_x=0,
            current_y=0,
        )
        db.add(picker)
    db.flush()


def preset_test_orders(db: Session, config: WarehouseConfig):
    existing = db.query(Order).first()
    if existing:
        return

    all_bins = db.query(Bin).filter(
        Bin.warehouse_id == config.id,
        Bin.sku_code.isnot(None),
        Bin.quantity > 0,
    ).all()

    if not all_bins:
        return

    sku_bins: dict[str, list] = {}
    for b in all_bins:
        sku_bins.setdefault(b.sku_code, []).append(b)

    available_skus = list(sku_bins.keys())
    if len(available_skus) < 5:
        return

    now = datetime.utcnow()
    order_specs = [
        (PRIORITY_NORMAL, now),
        (PRIORITY_NORMAL, now),
        (PRIORITY_URGENT, now - timedelta(minutes=31)),
        (PRIORITY_URGENT, now),
        (PRIORITY_SUPER_URGENT, now),
    ]

    created_count = 0
    for priority, created_time in order_specs:
        if created_count >= 5:
            break

        num_items = random.randint(2, 5)
        selected_skus = random.sample(available_skus, min(num_items, len(available_skus)))

        items_data = []
        valid = True
        for sku in selected_skus:
            bins_with_sku = sku_bins[sku]
            total_available = sum(b.quantity - b.frozen_quantity for b in bins_with_sku)
            if total_available <= 0:
                valid = False
                break
            qty = random.randint(1, min(10, total_available))
            items_data.append((sku, qty))

        if not valid:
            continue

        order = Order(
            status="pending",
            priority=priority,
            is_overdue=False,
            is_critically_overdue=False,
            escalation_count=0,
            created_at=created_time,
        )
        db.add(order)
        db.flush()

        for sku, qty in items_data:
            order_item = OrderItem(
                order_id=order.id,
                sku_code=sku,
                quantity=qty,
                allocated_quantity=0,
                picked_quantity=0,
                status="pending",
            )
            db.add(order_item)

        created_count += 1

    db.flush()


def preset_replenish_configs(db: Session):
    existing = db.query(ReplenishConfig).first()
    if existing:
        return

    configs_data = [
        {"sku_code": "SKU001", "threshold": 10, "target_quantity": 50},
        {"sku_code": "SKU002", "threshold": 8, "target_quantity": 40},
        {"sku_code": "SKU003", "threshold": 15, "target_quantity": 60},
        {"sku_code": "SKU004", "threshold": 5, "target_quantity": 25},
        {"sku_code": "SKU005", "threshold": 20, "target_quantity": 80},
        {"sku_code": "SKU006", "threshold": 12, "target_quantity": 45},
        {"sku_code": "SKU007", "threshold": 6, "target_quantity": 30},
        {"sku_code": "SKU008", "threshold": 18, "target_quantity": 70},
        {"sku_code": "SKU009", "threshold": 9, "target_quantity": 35},
        {"sku_code": "SKU010", "threshold": 7, "target_quantity": 38},
    ]

    for cfg in configs_data:
        config = ReplenishConfig(
            sku_code=cfg["sku_code"],
            threshold=cfg["threshold"],
            target_quantity=cfg["target_quantity"],
        )
        db.add(config)

    db.flush()


def preset_low_stock_bins(db: Session, config: WarehouseConfig):
    low_stock_skus = ["SKU001", "SKU002", "SKU003", "SKU004", "SKU005"]
    thresholds = {
        "SKU001": 10,
        "SKU002": 8,
        "SKU003": 15,
        "SKU004": 5,
        "SKU005": 20,
    }

    count = 0
    for sku in low_stock_skus:
        if count >= 3:
            break
        bins_with_sku = (
            db.query(Bin)
            .filter(
                Bin.warehouse_id == config.id,
                Bin.sku_code == sku,
            )
            .all()
        )
        if not bins_with_sku:
            continue
        target_bin = bins_with_sku[0]
        target_bin.quantity = max(0, thresholds[sku] - random.randint(1, 5))
        count += 1

    if count < 3:
        all_sku_bins = (
            db.query(Bin)
            .filter(
                Bin.warehouse_id == config.id,
                Bin.sku_code.isnot(None),
                Bin.quantity > 5,
            )
            .all()
        )
        random.shuffle(all_sku_bins)
        for b in all_sku_bins:
            if count >= 3:
                break
            b.quantity = random.randint(0, 3)
            count += 1

    db.flush()


def preset_bin_heat_data(db: Session, config: WarehouseConfig):
    all_bins = db.query(Bin).filter(Bin.warehouse_id == config.id).all()
    mid_row = config.aisle_length // 2

    bins_with_sku = [b for b in all_bins if b.sku_code and b.quantity > 0]
    random.shuffle(bins_with_sku)

    back_bins = [b for b in bins_with_sku if b.row > mid_row]
    front_bins = [b for b in bins_with_sku if b.row <= mid_row]

    hot_count = 0
    for bin_obj in back_bins:
        if hot_count >= 5:
            break
        bin_obj.pick_count = random.randint(15, 50)
        hot_count += 1

    if hot_count < 5:
        other_bins = [b for b in bins_with_sku if b.row > mid_row and b not in back_bins[:hot_count]]
        for bin_obj in other_bins:
            if hot_count >= 5:
                break
            bin_obj.pick_count = random.randint(15, 50)
            hot_count += 1

    cold_count = 0
    for bin_obj in front_bins:
        if cold_count >= 5:
            break
        bin_obj.pick_count = 0
        cold_count += 1

    if cold_count < 5:
        other_front_bins = [b for b in bins_with_sku if b.row <= mid_row and b not in front_bins[:cold_count]]
        for bin_obj in other_front_bins:
            if cold_count >= 5:
                break
            bin_obj.pick_count = 0
            cold_count += 1

    for bin_obj in bins_with_sku:
        if bin_obj.pick_count == 0 and not (
            (bin_obj in back_bins[:hot_count] if hot_count > 0 else False) or
            (bin_obj in front_bins[:cold_count] if cold_count > 0 else False)
        ):
            bin_obj.pick_count = random.randint(1, 10)

    db.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        config = preset_default_warehouse(db)
        preset_skus(db, config)
        preset_replenish_configs(db)
        preset_low_stock_bins(db, config)
        preset_pickers(db)
        preset_test_orders(db, config)
        preset_bin_heat_data(db, config)
        db.commit()

        results = trigger_allocation(db)
        print(f"========== 启动时自动分配结果 ==========")
        for r in results:
            print(f"  订单 {r['order_id']} -> 拣货员 {r['picker_id']}")
        print(f"  共分配 {len(results)} 个订单")
        print(f"========================================")

        low_stock_bins = (
            db.query(Bin)
            .filter(Bin.sku_code.isnot(None))
            .all()
        )
        low_count = 0
        from replenishment import get_replenish_config
        for b in low_stock_bins:
            available = b.quantity - b.frozen_quantity
            cfg = get_replenish_config(db, b.sku_code)
            if available < cfg.threshold:
                low_count += 1
        print(f"========== 补货预置信息 ==========")
        print(f"  预置10个SKU补货配置完成")
        print(f"  当前低于阈值的库位数: {low_count}")
        print(f"  提示: 调用 POST /api/replenishment/trigger-full-scan 可触发全仓补货检测")
        print(f"========================================")

        hot_back_count = (
            db.query(Bin)
            .filter(
                Bin.warehouse_id == config.id,
                Bin.sku_code.isnot(None),
                Bin.pick_count > 10,
                Bin.row > config.aisle_length // 2,
            )
            .count()
        )
        cold_front_count = (
            db.query(Bin)
            .filter(
                Bin.warehouse_id == config.id,
                Bin.sku_code.isnot(None),
                Bin.pick_count == 0,
                Bin.row <= config.aisle_length // 2,
            )
            .count()
        )
        print(f"========== 库位热度预置信息 ==========")
        print(f"  后半段热门库位(拣取>10次): {hot_back_count}个")
        print(f"  前半段冷门库位(拣取=0次): {cold_front_count}个")
        print(f"  提示: 调用 POST /api/relocation/generate-full 可生成全仓搬迁建议")
        print(f"========================================")
    finally:
        db.close()

    yield


app = FastAPI(
    title="仓储物流调度与路径优化服务",
    description="仓库拣货调度、路径规划、库存管理与订单履行系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(warehouse_router)
app.include_router(inventory_router)
app.include_router(order_router)
app.include_router(picker_router)
app.include_router(allocation_router)
app.include_router(stats_router)
app.include_router(replenishment_router)
app.include_router(relocation_router)


@app.get("/")
def root():
    return {
        "service": "仓储物流调度与路径优化服务",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    total_orders = db.query(Order).count()
    pending_orders = db.query(Order).filter(Order.status == "pending").count()
    allocated_orders = db.query(Order).filter(Order.status == "allocated").count()
    picking_orders = db.query(Order).filter(Order.status == "picking").count()
    completed_orders = db.query(Order).filter(Order.status == "completed").count()
    shipped_orders = db.query(Order).filter(Order.status == "shipped").count()

    total_pickers = db.query(Picker).count()
    idle_pickers = db.query(Picker).filter(Picker.status == "idle").count()
    busy_pickers = db.query(Picker).filter(Picker.status == "busy").count()

    total_tasks = db.query(PickTask).count()
    active_tasks = db.query(PickTask).filter(PickTask.status == "in_progress").count()

    total_bins = db.query(Bin).count()
    bins_with_stock = db.query(Bin).filter(Bin.sku_code.isnot(None), Bin.quantity > 0).count()

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    replenish_today_created = db.query(ReplenishTask).filter(ReplenishTask.created_at >= today_start).count()
    replenish_today_completed = db.query(ReplenishTask).filter(
        ReplenishTask.completed_at >= today_start,
        ReplenishTask.status == "completed",
    ).count()
    replenish_pending = db.query(ReplenishTask).filter(ReplenishTask.status == "pending").count()

    from relocation import get_relocation_stats
    relocation_stats = get_relocation_stats(db)
    relocation_pending = db.query(RelocationSuggestion).filter(RelocationSuggestion.status == "pending").count()

    return {
        "orders": {
            "total": total_orders,
            "pending": pending_orders,
            "allocated": allocated_orders,
            "picking": picking_orders,
            "completed": completed_orders,
            "shipped": shipped_orders,
        },
        "pickers": {
            "total": total_pickers,
            "idle": idle_pickers,
            "busy": busy_pickers,
        },
        "tasks": {
            "total": total_tasks,
            "active": active_tasks,
        },
        "inventory": {
            "total_bins": total_bins,
            "bins_with_stock": bins_with_stock,
        },
        "replenishment": {
            "today_created": replenish_today_created,
            "today_completed": replenish_today_completed,
            "pending": replenish_pending,
        },
        "relocation": {
            "total_executed": relocation_stats.total_executed,
            "total_estimated_saving": relocation_stats.total_estimated_saving,
            "pending_suggestions": relocation_pending,
            "last_full_optimization_at": relocation_stats.last_full_optimization_at,
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
