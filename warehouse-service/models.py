from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


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

    id = Column(Integer, primary_key=True, index=True)
    aisle_id = Column(Integer, ForeignKey("aisles.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouse_configs.id"), nullable=False)
    row = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)
    coordinate = Column(String(20), unique=True, nullable=False)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    sku_code = Column(String(50), nullable=True)
    quantity = Column(Integer, default=0)
    frozen_quantity = Column(Integer, default=0)
    pick_count = Column(Integer, default=0)

    aisle = relationship("Aisle", back_populates="bins")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), default="pending", nullable=False)
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
    current_x = Column(Integer, default=0)
    current_y = Column(Integer, default=0)
    current_task_id = Column(Integer, nullable=True)
    total_tasks = Column(Integer, default=0)
    total_distance = Column(Float, default=0.0)
    total_pick_time = Column(Float, default=0.0)

    pick_tasks = relationship("PickTask", back_populates="picker")


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
