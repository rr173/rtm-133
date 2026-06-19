from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class WarehouseConfigCreate(BaseModel):
    name: str = "default"
    num_aisles: int
    aisle_length: int
    aisle_spacing: int = 2


class WarehouseConfigResponse(BaseModel):
    id: int
    name: str
    num_aisles: int
    aisle_length: int
    aisle_spacing: int
    is_default: bool

    class Config:
        from_attributes = True


class BinResponse(BaseModel):
    id: int
    coordinate: str
    row: int
    level: int
    x: int
    y: int
    sku_code: Optional[str] = None
    quantity: int = 0
    frozen_quantity: int = 0
    pick_count: int = 0

    class Config:
        from_attributes = True


class StockInRequest(BaseModel):
    coordinate: str
    sku_code: str
    quantity: int


class SKUInventoryResponse(BaseModel):
    sku_code: str
    bins: list[dict]


class FreezeRequest(BaseModel):
    coordinate: str
    quantity: int


class OrderItemCreate(BaseModel):
    sku_code: str
    quantity: int


class OrderCreate(BaseModel):
    items: list[OrderItemCreate]


class OrderItemResponse(BaseModel):
    id: int
    sku_code: str
    quantity: int
    allocated_quantity: int
    picked_quantity: int
    status: str
    assigned_bins: Optional[str] = None

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    status: str
    wave_id: Optional[int] = None
    picker_id: Optional[int] = None
    created_at: Optional[datetime] = None
    allocated_at: Optional[datetime] = None
    picking_started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    items: list[OrderItemResponse] = []

    class Config:
        from_attributes = True


class PickerCreate(BaseModel):
    name: str


class PickerUpdate(BaseModel):
    status: Optional[str] = None


class PickerResponse(BaseModel):
    id: int
    name: str
    status: str
    current_x: int
    current_y: int
    current_task_id: Optional[int] = None
    total_tasks: int = 0
    total_distance: float = 0.0
    total_pick_time: float = 0.0

    class Config:
        from_attributes = True


class PathStep(BaseModel):
    coordinate: str
    sku_code: Optional[str] = None
    pick_quantity: int = 0
    x: int
    y: int


class PathPlanResponse(BaseModel):
    steps: list[PathStep]
    total_distance: int
    strategy: str


class PickConfirmRequest(BaseModel):
    actual_quantity: int = 0
    exception: bool = False


class PickTaskResponse(BaseModel):
    id: int
    order_id: Optional[int] = None
    wave_id: Optional[int] = None
    picker_id: int
    status: str
    path: Optional[str] = None
    path_details: Optional[str] = None
    total_distance: int = 0
    current_step: int = 0
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WaveCreateRequest(BaseModel):
    order_ids: list[int]


class WaveResponse(BaseModel):
    id: int
    picker_id: Optional[int] = None
    status: str
    order_ids: list[int] = []
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PickerStatsResponse(BaseModel):
    picker_id: int
    name: str
    today_tasks: int
    today_distance: float
    avg_time_per_order: float


class BinHeatResponse(BaseModel):
    coordinate: str
    sku_code: Optional[str]
    pick_count: int
    suggestion: Optional[str] = None


class OrderStatsResponse(BaseModel):
    total_orders: int
    avg_fulfillment_seconds: float
    exception_rate: float
