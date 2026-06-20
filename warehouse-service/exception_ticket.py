from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import (
    ExceptionTicket, OrderItem, ReplenishTask, Bin,
    TICKET_TYPE_INVENTORY, TICKET_TYPE_REMINDER, TICKET_TYPE_CUSTOM,
    TICKET_STATUS_PENDING, TICKET_STATUS_IN_PROGRESS, TICKET_STATUS_CLOSED,
    TICKET_PRIORITY_LOW, TICKET_PRIORITY_MEDIUM, TICKET_PRIORITY_HIGH,
    TICKET_PRIORITY_ORDER,
    SOURCE_TYPE_PICK_TASK, SOURCE_TYPE_REPLENISH_TASK, SOURCE_TYPE_CUSTOM,
    TICKET_TYPES, TICKET_STATUSES, TICKET_PRIORITIES,
)
from schemas import (
    ExceptionTicketCreate,
    ExceptionTicketClaimRequest,
    ExceptionTicketCloseRequest,
    ExceptionTicketResponse,
    ExceptionTicketStatsResponse,
)

router = APIRouter(prefix="/api/exception-tickets", tags=["exception-tickets"])

REPLENISH_TIMEOUT_HOURS = 2
TICKET_ESCALATION_HOURS = 1


def _check_duplicate_ticket(db: Session, source_type: str, source_id: int) -> bool:
    existing = (
        db.query(ExceptionTicket)
        .filter(
            ExceptionTicket.source_type == source_type,
            ExceptionTicket.source_id == source_id,
            ExceptionTicket.status.in_([TICKET_STATUS_PENDING, TICKET_STATUS_IN_PROGRESS]),
        )
        .first()
    )
    return existing is not None


def _create_ticket(
    db: Session,
    ticket_type: str,
    source_type: str,
    source_id: int | None,
    target_bin: str,
    description: str,
    priority: str = TICKET_PRIORITY_LOW,
) -> ExceptionTicket | None:
    if source_type != SOURCE_TYPE_CUSTOM and source_id is not None:
        if _check_duplicate_ticket(db, source_type, source_id):
            return None

    ticket = ExceptionTicket(
        ticket_type=ticket_type,
        source_type=source_type,
        source_id=source_id,
        target_bin=target_bin,
        description=description,
        status=TICKET_STATUS_PENDING,
        priority=priority,
        is_urgent=False,
        created_at=datetime.utcnow(),
    )
    db.add(ticket)
    db.flush()
    return ticket


def create_inventory_ticket_for_exception(
    db: Session,
    pick_task_id: int,
    order_item_id: int,
    bin_coordinate: str,
    sku_code: str,
) -> ExceptionTicket | None:
    description = f"拣货异常：订单明细ID={order_item_id}, SKU={sku_code}, 库位={bin_coordinate}, 请核实实际库存"
    return _create_ticket(
        db=db,
        ticket_type=TICKET_TYPE_INVENTORY,
        source_type=SOURCE_TYPE_PICK_TASK,
        source_id=pick_task_id,
        target_bin=bin_coordinate,
        description=description,
        priority=TICKET_PRIORITY_MEDIUM,
    )


def create_reminder_ticket_for_replenish(
    db: Session,
    replenish_task_id: int,
    bin_coordinate: str,
    sku_code: str,
    created_at: datetime,
) -> ExceptionTicket | None:
    hours_passed = (datetime.utcnow() - created_at).total_seconds() / 3600
    description = (
        f"补货超时：补货任务ID={replenish_task_id}, SKU={sku_code}, "
        f"库位={bin_coordinate}, 已创建{hours_passed:.1f}小时仍未完成"
    )
    return _create_ticket(
        db=db,
        ticket_type=TICKET_TYPE_REMINDER,
        source_type=SOURCE_TYPE_REPLENISH_TASK,
        source_id=replenish_task_id,
        target_bin=bin_coordinate,
        description=description,
        priority=TICKET_PRIORITY_HIGH,
    )


def scan_exception_pick_items(db: Session) -> list[ExceptionTicket]:
    exception_items = (
        db.query(OrderItem)
        .filter(OrderItem.status == "exception")
        .all()
    )

    created_tickets = []
    for item in exception_items:
        if not item.assigned_bins:
            continue

        import json
        try:
            assigned = json.loads(item.assigned_bins)
        except (json.JSONDecodeError, TypeError):
            continue

        for alloc in assigned:
            bin_coord = alloc.get("coordinate")
            if not bin_coord:
                continue

            pick_tasks = (
                db.query(ExceptionTicket)
                .filter(
                    ExceptionTicket.source_type == SOURCE_TYPE_PICK_TASK,
                    ExceptionTicket.target_bin == bin_coord,
                    ExceptionTicket.ticket_type == TICKET_TYPE_INVENTORY,
                    ExceptionTicket.status.in_([TICKET_STATUS_PENDING, TICKET_STATUS_IN_PROGRESS]),
                )
                .first()
            )
            if pick_tasks:
                continue

            bin_obj = db.query(Bin).filter(Bin.coordinate == bin_coord).first()
            sku_code = bin_obj.sku_code if bin_obj else item.sku_code

            ticket = create_inventory_ticket_for_exception(
                db=db,
                pick_task_id=item.order_id,
                order_item_id=item.id,
                bin_coordinate=bin_coord,
                sku_code=sku_code,
            )
            if ticket:
                created_tickets.append(ticket)

    return created_tickets


def scan_timeout_replenish_tasks(db: Session) -> list[ExceptionTicket]:
    cutoff_time = datetime.utcnow() - timedelta(hours=REPLENISH_TIMEOUT_HOURS)

    timeout_tasks = (
        db.query(ReplenishTask)
        .filter(
            ReplenishTask.status.in_(["pending", "in_progress"]),
            ReplenishTask.created_at <= cutoff_time,
        )
        .all()
    )

    created_tickets = []
    for task in timeout_tasks:
        ticket = create_reminder_ticket_for_replenish(
            db=db,
            replenish_task_id=task.id,
            bin_coordinate=task.bin_coordinate,
            sku_code=task.sku_code,
            created_at=task.created_at,
        )
        if ticket:
            created_tickets.append(ticket)

    return created_tickets


def escalate_ticket_priority(db: Session) -> int:
    escalation_cutoff = datetime.utcnow() - timedelta(hours=TICKET_ESCALATION_HOURS)

    pending_tickets = (
        db.query(ExceptionTicket)
        .filter(
            ExceptionTicket.status == TICKET_STATUS_PENDING,
            ExceptionTicket.created_at <= escalation_cutoff,
        )
        .all()
    )

    escalated_count = 0
    for ticket in pending_tickets:
        last_escalation = ticket.last_escalated_at or ticket.created_at
        if (datetime.utcnow() - last_escalation).total_seconds() < TICKET_ESCALATION_HOURS * 3600:
            continue

        current_level = TICKET_PRIORITY_ORDER.get(ticket.priority, 0)
        if current_level == 0:
            ticket.priority = TICKET_PRIORITY_MEDIUM
            ticket.last_escalated_at = datetime.utcnow()
            escalated_count += 1
        elif current_level == 1:
            ticket.priority = TICKET_PRIORITY_HIGH
            ticket.last_escalated_at = datetime.utcnow()
            escalated_count += 1
        elif current_level == 2:
            if not ticket.is_urgent:
                ticket.is_urgent = True
                ticket.last_escalated_at = datetime.utcnow()
                escalated_count += 1

    return escalated_count


def run_global_scan(db: Session) -> dict:
    escalate_ticket_priority(db)

    pick_tickets = scan_exception_pick_items(db)
    replenish_tickets = scan_timeout_replenish_tasks(db)

    all_tickets = pick_tickets + replenish_tickets
    db.commit()

    return {
        "scanned_exception_items": len(pick_tickets) > 0,
        "scanned_timeout_replenish": len(replenish_tickets) > 0,
        "created_inventory_tickets": len(pick_tickets),
        "created_reminder_tickets": len(replenish_tickets),
        "total_created": len(all_tickets),
        "tickets": [
            {
                "id": t.id,
                "type": TICKET_TYPES.get(t.ticket_type, t.ticket_type),
                "target_bin": t.target_bin,
                "priority": TICKET_PRIORITIES.get(t.priority, t.priority),
            }
            for t in all_tickets
        ],
    }


@router.get("", response_model=list[ExceptionTicketResponse])
def list_tickets(
    status: str = None,
    ticket_type: str = None,
    priority: str = None,
    db: Session = Depends(get_db),
):
    query = db.query(ExceptionTicket)
    if status:
        query = query.filter(ExceptionTicket.status == status)
    if ticket_type:
        query = query.filter(ExceptionTicket.ticket_type == ticket_type)
    if priority:
        query = query.filter(ExceptionTicket.priority == priority)
    return query.order_by(ExceptionTicket.id.desc()).all()


@router.get("/{ticket_id}", response_model=ExceptionTicketResponse)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(ExceptionTicket).filter(ExceptionTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


@router.post("", response_model=ExceptionTicketResponse)
def create_custom_ticket(req: ExceptionTicketCreate, db: Session = Depends(get_db)):
    if not req.target_bin:
        raise HTTPException(status_code=400, detail="目标库位不能为空")
    if not req.description:
        raise HTTPException(status_code=400, detail="描述不能为空")
    if req.priority not in TICKET_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"优先级无效，可选值: {', '.join(TICKET_PRIORITIES.keys())}")

    bin_obj = db.query(Bin).filter(Bin.coordinate == req.target_bin).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail=f"库位{req.target_bin}不存在")

    ticket = _create_ticket(
        db=db,
        ticket_type=TICKET_TYPE_CUSTOM,
        source_type=SOURCE_TYPE_CUSTOM,
        source_id=None,
        target_bin=req.target_bin,
        description=req.description,
        priority=req.priority,
    )
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/claim", response_model=ExceptionTicketResponse)
def claim_ticket(ticket_id: int, req: ExceptionTicketClaimRequest, db: Session = Depends(get_db)):
    ticket = db.query(ExceptionTicket).filter(ExceptionTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    if ticket.status != TICKET_STATUS_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"工单状态为{TICKET_STATUSES.get(ticket.status, ticket.status)}，无法认领",
        )
    if not req.handler:
        raise HTTPException(status_code=400, detail="处理人不能为空")

    ticket.status = TICKET_STATUS_IN_PROGRESS
    ticket.claimed_at = datetime.utcnow()
    ticket.handler = req.handler
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/close", response_model=ExceptionTicketResponse)
def close_ticket(ticket_id: int, req: ExceptionTicketCloseRequest, db: Session = Depends(get_db)):
    ticket = db.query(ExceptionTicket).filter(ExceptionTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    if ticket.status != TICKET_STATUS_IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail=f"工单状态为{TICKET_STATUSES.get(ticket.status, ticket.status)}，无法关闭",
        )
    if not req.handler_note:
        raise HTTPException(status_code=400, detail="处理备注不能为空")

    ticket.status = TICKET_STATUS_CLOSED
    ticket.closed_at = datetime.utcnow()
    ticket.handler_note = req.handler_note
    db.commit()
    db.refresh(ticket)
    return ticket


@router.post("/scan")
def trigger_global_scan(db: Session = Depends(get_db)):
    result = run_global_scan(db)
    return result


@router.post("/escalate")
def trigger_priority_escalation(db: Session = Depends(get_db)):
    count = escalate_ticket_priority(db)
    db.commit()
    return {"escalated_count": count, "message": f"已升级{count}条工单的优先级"}


@router.get("/stats/today", response_model=ExceptionTicketStatsResponse)
def get_today_stats(db: Session = Depends(get_db)):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    today_tickets = db.query(ExceptionTicket).filter(ExceptionTicket.created_at >= today_start).all()

    by_type = {}
    by_status = {}
    for t in today_tickets:
        type_name = TICKET_TYPES.get(t.ticket_type, t.ticket_type)
        by_type[type_name] = by_type.get(type_name, 0) + 1
        status_name = TICKET_STATUSES.get(t.status, t.status)
        by_status[status_name] = by_status.get(status_name, 0) + 1

    closed_tickets = (
        db.query(ExceptionTicket)
        .filter(
            ExceptionTicket.status == TICKET_STATUS_CLOSED,
            ExceptionTicket.closed_at >= today_start,
        )
        .all()
    )

    total_seconds = 0.0
    closed_count = 0
    for t in closed_tickets:
        if t.created_at and t.closed_at:
            duration = (t.closed_at - t.created_at).total_seconds()
            total_seconds += duration
            closed_count += 1

    avg_seconds = total_seconds / closed_count if closed_count > 0 else 0.0

    return ExceptionTicketStatsResponse(
        today_by_type=by_type,
        today_by_status=by_status,
        avg_processing_seconds=avg_seconds,
    )


def preset_test_exception_data(db: Session):
    import json
    from models import Order, ReplenishTask as RT

    existing_exception_items = db.query(OrderItem).filter(OrderItem.status == "exception").all()
    existing_coords = set()
    for item in existing_exception_items:
        if item.assigned_bins:
            try:
                assigned = json.loads(item.assigned_bins)
                for alloc in assigned:
                    existing_coords.add(alloc.get("coordinate"))
            except (json.JSONDecodeError, TypeError):
                pass

    needed_exceptions = max(0, 2 - len(existing_exception_items))

    if needed_exceptions > 0:
        all_bins = db.query(Bin).filter(Bin.sku_code.isnot(None)).all()
        used_coords = set(existing_coords)
        order_id_counter = 10000

        for i in range(needed_exceptions):
            available_bins = [b for b in all_bins if b.coordinate not in used_coords]
            if not available_bins:
                break

            bin_obj = available_bins[0]
            used_coords.add(bin_obj.coordinate)

            mock_order = Order(
                status="completed",
                priority="normal",
                created_at=datetime.utcnow() - timedelta(hours=1),
            )
            db.add(mock_order)
            db.flush()

            mock_order_item = OrderItem(
                order_id=mock_order.id,
                sku_code=bin_obj.sku_code,
                quantity=5,
                allocated_quantity=5,
                picked_quantity=0,
                status="exception",
                assigned_bins=json.dumps([{
                    "coordinate": bin_obj.coordinate,
                    "allocated": 5,
                    "x": bin_obj.x,
                    "y": bin_obj.y,
                }]),
            )
            db.add(mock_order_item)
            db.flush()
            order_id_counter += 1

    cutoff = datetime.utcnow() - timedelta(hours=2)
    existing_timeout = (
        db.query(ReplenishTask)
        .filter(
            ReplenishTask.status.in_(["pending", "in_progress"]),
            ReplenishTask.created_at <= cutoff,
        )
        .count()
    )

    if existing_timeout == 0:
        all_bins = db.query(Bin).filter(Bin.sku_code.isnot(None)).all()
        used_bin_coords = {
            t.bin_coordinate for t in db.query(ReplenishTask).all()
        }
        available_bins = [b for b in all_bins if b.coordinate not in used_bin_coords]
        if available_bins:
            bin_obj = available_bins[0]
            old_task = RT(
                bin_coordinate=bin_obj.coordinate,
                sku_code=bin_obj.sku_code,
                required_quantity=20,
                status="pending",
                created_at=datetime.utcnow() - timedelta(hours=3),
            )
            db.add(old_task)
            db.flush()

    db.commit()
