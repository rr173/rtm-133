from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Order, OrderItem, Bin
from schemas import OrderCreate, OrderResponse, OrderItemResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("", response_model=OrderResponse)
def create_order(req: OrderCreate, db: Session = Depends(get_db)):
    for item in req.items:
        bins = db.query(Bin).filter(Bin.sku_code == item.sku_code).all()
        total_available = sum(b.quantity - b.frozen_quantity for b in bins)
        if total_available < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"SKU {item.sku_code} 库存不足: 需要{item.quantity}, 可用{total_available}",
            )

    order = Order(status="pending", created_at=datetime.utcnow())
    db.add(order)
    db.flush()

    for item in req.items:
        order_item = OrderItem(
            order_id=order.id,
            sku_code=item.sku_code,
            quantity=item.quantity,
            allocated_quantity=0,
            picked_quantity=0,
            status="pending",
        )
        db.add(order_item)

    db.commit()
    db.refresh(order)
    return _build_order_response(order, db)


@router.get("", response_model=list[OrderResponse])
def list_orders(status: str = None, db: Session = Depends(get_db)):
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.id.desc()).all()
    return [_build_order_response(o, db) for o in orders]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return _build_order_response(order, db)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status not in ("pending", "allocated"):
        raise HTTPException(status_code=400, detail=f"订单状态为{order.status},无法取消")

    if order.status == "allocated":
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        for item in items:
            if item.assigned_bins:
                import json
                assigned = json.loads(item.assigned_bins)
                for alloc in assigned:
                    bin_obj = db.query(Bin).filter(Bin.coordinate == alloc["coordinate"]).first()
                    if bin_obj:
                        bin_obj.frozen_quantity -= alloc["allocated"]

    order.status = "cancelled"
    db.commit()
    return {"message": "订单已取消", "order_id": order_id}


def _build_order_response(order: Order, db: Session):
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    return OrderResponse(
        id=order.id,
        status=order.status,
        wave_id=order.wave_id,
        picker_id=order.picker_id,
        created_at=order.created_at,
        allocated_at=order.allocated_at,
        picking_started_at=order.picking_started_at,
        completed_at=order.completed_at,
        items=[
            OrderItemResponse(
                id=oi.id,
                sku_code=oi.sku_code,
                quantity=oi.quantity,
                allocated_quantity=oi.allocated_quantity,
                picked_quantity=oi.picked_quantity,
                status=oi.status,
                assigned_bins=oi.assigned_bins,
            )
            for oi in items
        ],
    )
