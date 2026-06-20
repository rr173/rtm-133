from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Bin, RelocationSuggestion, RelocationStats, WarehouseConfig, Aisle
from schemas import RelocationSuggestionResponse, RelocationDecisionRequest, RelocationStatsResponse

router = APIRouter(prefix="/api/relocation", tags=["relocation"])


def get_relocation_stats(db: Session) -> RelocationStats:
    stats = db.query(RelocationStats).first()
    if not stats:
        stats = RelocationStats(
            total_executed=0,
            total_estimated_saving=0.0,
        )
        db.add(stats)
        db.flush()
    return stats


def calculate_distance_saving(source_bin: Bin, target_bin: Bin) -> float:
    source_dist = abs(source_bin.x) + abs(source_bin.y)
    target_dist = abs(target_bin.x) + abs(target_bin.y)
    return max(0.0, float(source_dist - target_dist))


def get_aisle_mid_row(db: Session, warehouse_id: int) -> int:
    config = db.query(WarehouseConfig).filter(WarehouseConfig.id == warehouse_id).first()
    if config:
        return config.aisle_length // 2
    return 10


def generate_relocation_suggestions(db: Session, warehouse_name: str = "default") -> list[RelocationSuggestion]:
    config = db.query(WarehouseConfig).filter(WarehouseConfig.name == warehouse_name).first()
    if not config:
        raise HTTPException(status_code=404, detail="仓库配置不存在")

    all_bins = db.query(Bin).filter(Bin.warehouse_id == config.id).all()
    if not all_bins:
        return []

    mid_row = config.aisle_length // 2

    bins_with_sku = [b for b in all_bins if b.sku_code and b.quantity > 0]
    bins_with_sku_sorted = sorted(bins_with_sku, key=lambda b: b.pick_count, reverse=True)

    total_sku_bins = len(bins_with_sku_sorted)
    if total_sku_bins < 5:
        return []

    top_20_count = max(1, int(total_sku_bins * 0.2))
    bottom_20_count = max(1, int(total_sku_bins * 0.2))

    top_bins = bins_with_sku_sorted[:top_20_count]
    bottom_bins = bins_with_sku_sorted[-bottom_20_count:]

    empty_front_bins = [
        b for b in all_bins
        if not b.sku_code and b.quantity == 0 and b.row <= mid_row
    ]
    empty_back_bins = [
        b for b in all_bins
        if not b.sku_code and b.quantity == 0 and b.row > mid_row
    ]

    suggestions = []

    hot_back_bins = [b for b in top_bins if b.row > mid_row]
    for source_bin in hot_back_bins:
        if not empty_front_bins:
            break
        target_bin = empty_front_bins.pop(0)
        saving = calculate_distance_saving(source_bin, target_bin)
        suggestion = RelocationSuggestion(
            source_bin=source_bin.coordinate,
            target_bin=target_bin.coordinate,
            sku_code=source_bin.sku_code,
            quantity=source_bin.quantity,
            estimated_saving=saving,
            status="pending",
            reason=f"热门SKU({source_bin.pick_count}次拣取)位于通道后半段(第{source_bin.row}排),建议前置",
            created_at=datetime.utcnow(),
        )
        suggestions.append(suggestion)

    cold_front_bins = [b for b in bottom_bins if b.row <= mid_row]
    for source_bin in cold_front_bins:
        if not empty_back_bins:
            break
        target_bin = empty_back_bins.pop(0)
        saving = calculate_distance_saving(target_bin, source_bin)
        suggestion = RelocationSuggestion(
            source_bin=source_bin.coordinate,
            target_bin=target_bin.coordinate,
            sku_code=source_bin.sku_code,
            quantity=source_bin.quantity,
            estimated_saving=saving,
            status="pending",
            reason=f"冷门SKU({source_bin.pick_count}次拣取)占用通道前半段(第{source_bin.row}排),建议后置让出库位",
            created_at=datetime.utcnow(),
        )
        suggestions.append(suggestion)

    return suggestions


@router.post("/generate-full")
def generate_full_suggestions(db: Session = Depends(get_db)):
    db.query(RelocationSuggestion).filter(RelocationSuggestion.status == "pending").delete()

    suggestions = generate_relocation_suggestions(db)
    for s in suggestions:
        db.add(s)

    stats = get_relocation_stats(db)
    stats.last_full_optimization_at = datetime.utcnow()

    db.commit()

    for s in suggestions:
        db.refresh(s)

    return {
        "message": f"全仓库位优化建议生成完成,共生成{len(suggestions)}条建议",
        "total_count": len(suggestions),
        "suggestions": [
            {
                "id": s.id,
                "source_bin": s.source_bin,
                "target_bin": s.target_bin,
                "sku_code": s.sku_code,
                "quantity": s.quantity,
                "estimated_saving": s.estimated_saving,
                "reason": s.reason,
            }
            for s in suggestions
        ],
    }


@router.get("/suggestions", response_model=list[RelocationSuggestionResponse])
def list_suggestions(status: str = None, db: Session = Depends(get_db)):
    query = db.query(RelocationSuggestion)
    if status:
        query = query.filter(RelocationSuggestion.status == status)
    return query.order_by(RelocationSuggestion.id.desc()).all()


@router.get("/suggestions/{suggestion_id}", response_model=RelocationSuggestionResponse)
def get_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    suggestion = db.query(RelocationSuggestion).filter(RelocationSuggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="搬迁建议不存在")
    return suggestion


@router.post("/suggestions/{suggestion_id}/decision", response_model=RelocationSuggestionResponse)
def decide_suggestion(suggestion_id: int, req: RelocationDecisionRequest, db: Session = Depends(get_db)):
    suggestion = db.query(RelocationSuggestion).filter(RelocationSuggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="搬迁建议不存在")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"建议状态为{suggestion.status},无法操作")

    decision = req.decision.lower()
    if decision == "reject":
        suggestion.status = "rejected"
        suggestion.confirmed_at = datetime.utcnow()
        db.commit()
        db.refresh(suggestion)
        return suggestion

    if decision != "confirm":
        raise HTTPException(status_code=400, detail="无效的决策,请使用 confirm 或 reject")

    source_bin = db.query(Bin).filter(Bin.coordinate == suggestion.source_bin).first()
    if not source_bin:
        raise HTTPException(status_code=404, detail=f"源库位{suggestion.source_bin}不存在")

    target_bin = db.query(Bin).filter(Bin.coordinate == suggestion.target_bin).first()
    if not target_bin:
        raise HTTPException(status_code=404, detail=f"目标库位{suggestion.target_bin}不存在")

    if target_bin.sku_code or target_bin.quantity > 0:
        raise HTTPException(
            status_code=400,
            detail=f"目标库位{suggestion.target_bin}已有库存(SKU:{target_bin.sku_code}),无法执行搬迁",
        )

    if source_bin.frozen_quantity > 0:
        raise HTTPException(
            status_code=400,
            detail=f"源库位{suggestion.source_bin}有{source_bin.frozen_quantity}件冻结库存,请等待冻结释放后再执行",
        )

    target_bin.sku_code = source_bin.sku_code
    target_bin.quantity = source_bin.quantity
    target_bin.frozen_quantity = 0
    target_bin.pick_count = source_bin.pick_count

    source_bin.sku_code = None
    source_bin.quantity = 0
    source_bin.frozen_quantity = 0
    source_bin.pick_count = 0

    suggestion.status = "executed"
    suggestion.confirmed_at = datetime.utcnow()
    suggestion.executed_at = datetime.utcnow()

    stats = get_relocation_stats(db)
    stats.total_executed += 1
    stats.total_estimated_saving += suggestion.estimated_saving

    db.commit()
    db.refresh(suggestion)
    return suggestion


@router.get("/stats", response_model=RelocationStatsResponse)
def get_stats(db: Session = Depends(get_db)):
    stats = get_relocation_stats(db)
    return stats
