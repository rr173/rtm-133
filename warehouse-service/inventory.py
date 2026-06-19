from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Bin
from schemas import StockInRequest, SKUInventoryResponse, FreezeRequest

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.post("/stock-in")
def stock_in(req: StockInRequest, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == req.coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")
    bin_obj.sku_code = req.sku_code
    bin_obj.quantity += req.quantity
    db.commit()
    db.refresh(bin_obj)
    return {
        "coordinate": bin_obj.coordinate,
        "sku_code": bin_obj.sku_code,
        "quantity": bin_obj.quantity,
        "frozen_quantity": bin_obj.frozen_quantity,
    }


@router.get("/sku/{sku_code}")
def query_sku_inventory(sku_code: str, db: Session = Depends(get_db)):
    bins = db.query(Bin).filter(Bin.sku_code == sku_code).all()
    if not bins:
        raise HTTPException(status_code=404, detail=f"SKU {sku_code} 无库存记录")
    return {
        "sku_code": sku_code,
        "bins": [
            {
                "coordinate": b.coordinate,
                "quantity": b.quantity,
                "frozen_quantity": b.frozen_quantity,
                "available": b.quantity - b.frozen_quantity,
            }
            for b in bins
        ],
        "total_quantity": sum(b.quantity for b in bins),
        "total_frozen": sum(b.frozen_quantity for b in bins),
        "total_available": sum(b.quantity - b.frozen_quantity for b in bins),
    }


@router.get("/bin/{coordinate}")
def query_bin_inventory(coordinate: str, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")
    return {
        "coordinate": bin_obj.coordinate,
        "sku_code": bin_obj.sku_code,
        "quantity": bin_obj.quantity,
        "frozen_quantity": bin_obj.frozen_quantity,
        "available": bin_obj.quantity - bin_obj.frozen_quantity,
    }


@router.post("/freeze")
def freeze_inventory(req: FreezeRequest, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == req.coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")
    available = bin_obj.quantity - bin_obj.frozen_quantity
    if available < req.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"可用库存不足: 可用{available}, 需要冻结{req.quantity}",
        )
    bin_obj.frozen_quantity += req.quantity
    db.commit()
    return {
        "coordinate": bin_obj.coordinate,
        "quantity": bin_obj.quantity,
        "frozen_quantity": bin_obj.frozen_quantity,
        "available": bin_obj.quantity - bin_obj.frozen_quantity,
    }


@router.post("/unfreeze")
def unfreeze_inventory(req: FreezeRequest, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == req.coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")
    if bin_obj.frozen_quantity < req.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"冻结库存不足: 冻结{bin_obj.frozen_quantity}, 需要解冻{req.quantity}",
        )
    bin_obj.frozen_quantity -= req.quantity
    db.commit()
    return {
        "coordinate": bin_obj.coordinate,
        "quantity": bin_obj.quantity,
        "frozen_quantity": bin_obj.frozen_quantity,
        "available": bin_obj.quantity - bin_obj.frozen_quantity,
    }


@router.get("/all")
def list_all_inventory(db: Session = Depends(get_db)):
    bins = db.query(Bin).filter(Bin.sku_code.isnot(None), Bin.quantity > 0).all()
    return {
        "total_bins_with_stock": len(bins),
        "items": [
            {
                "coordinate": b.coordinate,
                "sku_code": b.sku_code,
                "quantity": b.quantity,
                "frozen_quantity": b.frozen_quantity,
                "available": b.quantity - b.frozen_quantity,
            }
            for b in bins
        ],
    }
