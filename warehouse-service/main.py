import random
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base, get_db
from models import (
    WarehouseConfig, Aisle, Bin, Order, OrderItem, Picker, PickTask, Wave,
)
from warehouse import router as warehouse_router
from inventory import router as inventory_router
from order import router as order_router
from picker import router as picker_router
from allocation import router as allocation_router, trigger_allocation
from stats import router as stats_router

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

    for order_idx in range(5):
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

        order = Order(status="pending", created_at=datetime.utcnow())
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

    db.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        config = preset_default_warehouse(db)
        preset_skus(db, config)
        preset_pickers(db)
        preset_test_orders(db, config)
        db.commit()

        results = trigger_allocation(db)
        print(f"========== 启动时自动分配结果 ==========")
        for r in results:
            print(f"  订单 {r['order_id']} -> 拣货员 {r['picker_id']}")
        print(f"  共分配 {len(results)} 个订单")
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
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
