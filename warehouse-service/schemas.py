from datetime import datetime, date
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
    priority: str = "normal"


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
    priority: str
    is_overdue: bool
    is_critically_overdue: bool
    escalation_count: int
    fulfillment_deadline_minutes: int
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
    shift_code: str = "morning"


class PickerUpdate(BaseModel):
    status: Optional[str] = None
    shift_code: Optional[str] = None


class PickerResponse(BaseModel):
    id: int
    name: str
    status: str
    shift_code: str = "morning"
    current_x: int
    current_y: int
    current_task_id: Optional[int] = None
    total_tasks: int = 0
    total_distance: float = 0.0
    total_pick_time: float = 0.0

    class Config:
        from_attributes = True


class ShiftTemplateResponse(BaseModel):
    code: str
    name: str
    start_hour: int
    end_hour: int


class OnDutyPickerResponse(BaseModel):
    picker_id: int
    name: str
    status: str
    shift_code: str
    shift_name: str
    is_fatigued: bool = False


class WorkHourRecordResponse(BaseModel):
    id: int
    picker_id: int
    work_date: date
    first_task_started_at: Optional[datetime] = None
    last_task_completed_at: Optional[datetime] = None
    actual_work_seconds: float = 0.0
    actual_work_hours: float = 0.0
    is_fatigued: bool = False

    class Config:
        from_attributes = True


class WorkHourStatsResponse(BaseModel):
    picker_id: int
    name: str
    shift_code: str
    total_work_hours: float = 0.0
    avg_daily_hours: float = 0.0
    max_consecutive_hours: float = 0.0
    work_days: int = 0
    fatigue_days: int = 0


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


class ReplenishConfigCreate(BaseModel):
    sku_code: str
    threshold: int
    target_quantity: int


class ReplenishConfigUpdate(BaseModel):
    threshold: Optional[int] = None
    target_quantity: Optional[int] = None


class ReplenishConfigResponse(BaseModel):
    id: int
    sku_code: str
    threshold: int
    target_quantity: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReplenishTaskResponse(BaseModel):
    id: int
    bin_coordinate: str
    sku_code: str
    required_quantity: int
    status: str
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    actual_quantity: Optional[int] = None

    class Config:
        from_attributes = True


class ReplenishTaskStartRequest(BaseModel):
    pass


class ReplenishTaskCompleteRequest(BaseModel):
    actual_quantity: int


class ReplenishStatsResponse(BaseModel):
    today_created: int
    today_completed: int
    today_pending: int
    today_in_progress: int
    today_cancelled: int
    total_pending: int
    total_in_progress: int


class RelocationSuggestionResponse(BaseModel):
    id: int
    source_bin: str
    target_bin: str
    sku_code: str
    quantity: int
    estimated_saving: float
    status: str
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RelocationDecisionRequest(BaseModel):
    decision: str


class RelocationStatsResponse(BaseModel):
    total_executed: int
    total_estimated_saving: float
    last_full_optimization_at: Optional[datetime] = None


class PriorityPendingCount(BaseModel):
    priority: str
    pending_count: int


class OrderPriorityStatsResponse(BaseModel):
    pending_by_priority: list[PriorityPendingCount]
    today_overdue_count: int
    avg_fulfillment_seconds_by_priority: list[dict]


class OverdueCheckResult(BaseModel):
    scanned_count: int
    escalated_count: int
    newly_overdue_count: int
    critically_overdue_count: int
    escalated_orders: list[dict]


class ExceptionTicketCreate(BaseModel):
    target_bin: str
    description: str
    priority: str = "low"


class ExceptionTicketClaimRequest(BaseModel):
    handler: str


class ExceptionTicketCloseRequest(BaseModel):
    handler_note: str


class ExceptionTicketResponse(BaseModel):
    id: int
    ticket_type: str
    source_type: str
    source_id: Optional[int] = None
    target_bin: str
    description: str
    status: str
    priority: str
    is_urgent: bool
    created_at: datetime
    claimed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    handler: Optional[str] = None
    handler_note: Optional[str] = None

    class Config:
        from_attributes = True


class ExceptionTicketStatsResponse(BaseModel):
    today_by_type: dict
    today_by_status: dict
    avg_processing_seconds: float
