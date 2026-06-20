from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text, ForeignKey, UniqueConstraint, Date
from sqlalchemy.orm import relationship
from database import Base


SHIFT_MORNING = "morning"
SHIFT_AFTERNOON = "afternoon"
SHIFT_NIGHT = "night"

SHIFT_TEMPLATES = {
    SHIFT_MORNING: {"name": "早班", "start_hour": 8, "end_hour": 16},
    SHIFT_AFTERNOON: {"name": "中班", "start_hour": 16, "end_hour": 0},
    SHIFT_NIGHT: {"name": "晚班", "start_hour": 0, "end_hour": 8},
}

FATIGUE_THRESHOLD_HOURS = 7


class WarehouseConfig(Base):
    __tablename__ = "warehouse_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    num_aisles = Column(Integer, nullable=False)
    aisle_length = Column(Integer, nullable=False)
    aisle_spacing = Column(Integer, nullable=False, default=2)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    aisles = relationship("Aisle", back_populates="warehouse", cascade="all, delete-orphan")


class Aisle(Base):
    __tablename__ = "aisles"

    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouse_configs.id"), nullable=False)
    name = Column(String(10), nullable=False)
    index = Column(Integer, nullable=False)

    warehouse = relationship("WarehouseConfig", back_populates="aisles")
    bins = relationship("Bin", back_populates="aisle", cascade="all, delete-orphan")


class Bin(Base):
    __tablename__ = "bins"
    __table_args__ = (UniqueConstraint("warehouse_id", "coordinate", name="uq_bin_warehouse_coordinate"),)

    id = Column(Integer, primary_key=True, index=True)
    aisle_id = Column(Integer, ForeignKey("aisles.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouse_configs.id"), nullable=False)
    row = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)
    coordinate = Column(String(20), nullable=False)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    sku_code = Column(String(50), nullable=True)
    quantity = Column(Integer, default=0)
    frozen_quantity = Column(Integer, default=0)
    pick_count = Column(Integer, default=0)

    aisle = relationship("Aisle", back_populates="bins")


PRIORITY_NORMAL = "normal"
PRIORITY_URGENT = "urgent"
PRIORITY_SUPER_URGENT = "super_urgent"

PRIORITY_LEVEL = {
    PRIORITY_NORMAL: 0,
    PRIORITY_URGENT: 1,
    PRIORITY_SUPER_URGENT: 2,
}

PRIORITY_FULFILLMENT_MINUTES = {
    PRIORITY_NORMAL: 60,
    PRIORITY_URGENT: 30,
    PRIORITY_SUPER_URGENT: 15,
}

PRIORITY_ESCALATION = {
    PRIORITY_NORMAL: PRIORITY_URGENT,
    PRIORITY_URGENT: PRIORITY_SUPER_URGENT,
}


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), default="pending", nullable=False)
    priority = Column(String(20), default=PRIORITY_NORMAL, nullable=False)
    is_overdue = Column(Boolean, default=False, nullable=False)
    is_critically_overdue = Column(Boolean, default=False, nullable=False)
    escalation_count = Column(Integer, default=0, nullable=False)
    wave_id = Column(Integer, ForeignKey("waves.id"), nullable=True)
    picker_id = Column(Integer, ForeignKey("pickers.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    allocated_at = Column(DateTime, nullable=True)
    picking_started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    pick_tasks = relationship("PickTask", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    sku_code = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    allocated_quantity = Column(Integer, default=0)
    picked_quantity = Column(Integer, default=0)
    status = Column(String(20), default="pending", nullable=False)
    assigned_bins = Column(Text, nullable=True)

    order = relationship("Order", back_populates="items")


class Picker(Base):
    __tablename__ = "pickers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    status = Column(String(20), default="idle", nullable=False)
    shift_code = Column(String(20), default=SHIFT_MORNING, nullable=False)
    current_x = Column(Integer, default=0)
    current_y = Column(Integer, default=0)
    current_task_id = Column(Integer, nullable=True)
    total_tasks = Column(Integer, default=0)
    total_distance = Column(Float, default=0.0)
    total_pick_time = Column(Float, default=0.0)

    pick_tasks = relationship("PickTask", back_populates="picker")
    work_hour_records = relationship("WorkHourRecord", back_populates="picker", cascade="all, delete-orphan")


class WorkHourRecord(Base):
    __tablename__ = "work_hour_records"
    __table_args__ = (UniqueConstraint("picker_id", "work_date", name="uq_work_hour_picker_date"),)

    id = Column(Integer, primary_key=True, index=True)
    picker_id = Column(Integer, ForeignKey("pickers.id"), nullable=False)
    work_date = Column(Date, nullable=False)
    first_task_started_at = Column(DateTime, nullable=True)
    last_task_completed_at = Column(DateTime, nullable=True)
    actual_work_seconds = Column(Float, default=0.0)
    is_fatigued = Column(Boolean, default=False, nullable=False)

    picker = relationship("Picker", back_populates="work_hour_records")


class PickTask(Base):
    __tablename__ = "pick_tasks"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    wave_id = Column(Integer, ForeignKey("waves.id"), nullable=True)
    picker_id = Column(Integer, ForeignKey("pickers.id"), nullable=False)
    status = Column(String(20), default="allocated", nullable=False)
    path = Column(Text, nullable=True)
    path_details = Column(Text, nullable=True)
    total_distance = Column(Integer, default=0)
    current_step = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    order = relationship("Order", back_populates="pick_tasks")
    picker = relationship("Picker", back_populates="pick_tasks")
    wave = relationship("Wave", back_populates="pick_tasks")


class Wave(Base):
    __tablename__ = "waves"

    id = Column(Integer, primary_key=True, index=True)
    picker_id = Column(Integer, ForeignKey("pickers.id"), nullable=True)
    status = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    pick_tasks = relationship("PickTask", back_populates="wave")


class ReplenishConfig(Base):
    __tablename__ = "replenish_configs"
    __table_args__ = (UniqueConstraint("sku_code", name="uq_replenish_config_sku"),)

    id = Column(Integer, primary_key=True, index=True)
    sku_code = Column(String(50), nullable=False)
    threshold = Column(Integer, nullable=False, default=5)
    target_quantity = Column(Integer, nullable=False, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ReplenishTask(Base):
    __tablename__ = "replenish_tasks"

    id = Column(Integer, primary_key=True, index=True)
    bin_coordinate = Column(String(20), nullable=False)
    sku_code = Column(String(50), nullable=False)
    required_quantity = Column(Integer, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    actual_quantity = Column(Integer, nullable=True)


class RelocationSuggestion(Base):
    __tablename__ = "relocation_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    source_bin = Column(String(20), nullable=False)
    target_bin = Column(String(20), nullable=False)
    sku_code = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    estimated_saving = Column(Float, default=0.0)
    status = Column(String(20), default="pending", nullable=False)
    reason = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)


class RelocationStats(Base):
    __tablename__ = "relocation_stats"

    id = Column(Integer, primary_key=True, index=True)
    total_executed = Column(Integer, default=0)
    total_estimated_saving = Column(Float, default=0.0)
    last_full_optimization_at = Column(DateTime, nullable=True)
