from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import WarehouseConfig, Aisle, Bin
from schemas import WarehouseConfigCreate, WarehouseConfigResponse, BinResponse

router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])


def generate_warehouse_layout(db: Session, config: WarehouseConfig):
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


@router.post("/config", response_model=WarehouseConfigResponse)
def create_warehouse_config(req: WarehouseConfigCreate, db: Session = Depends(get_db)):
    if req.num_aisles < 2 or req.num_aisles > 20:
        raise HTTPException(status_code=400, detail="通道数量必须在2到20之间")
    if req.aisle_length < 10 or req.aisle_length > 100:
        raise HTTPException(status_code=400, detail="每条通道长度必须在10到100之间")

    existing = db.query(WarehouseConfig).filter(WarehouseConfig.name == req.name).first()
    if existing:
        db.query(Bin).filter(Bin.warehouse_id == existing.id).delete()
        db.query(Aisle).filter(Aisle.warehouse_id == existing.id).delete()
        db.delete(existing)
        db.flush()

    config = WarehouseConfig(
        name=req.name,
        num_aisles=req.num_aisles,
        aisle_length=req.aisle_length,
        aisle_spacing=req.aisle_spacing,
        is_default=(req.name == "default"),
    )
    db.add(config)
    db.flush()

    generate_warehouse_layout(db, config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/config", response_model=WarehouseConfigResponse)
def get_warehouse_config(name: str = "default", db: Session = Depends(get_db)):
    config = db.query(WarehouseConfig).filter(WarehouseConfig.name == name).first()
    if not config:
        raise HTTPException(status_code=404, detail="仓库配置不存在")
    return config


@router.get("/configs", response_model=list[WarehouseConfigResponse])
def list_warehouse_configs(db: Session = Depends(get_db)):
    return db.query(WarehouseConfig).all()


@router.get("/bins", response_model=list[BinResponse])
def list_bins(warehouse_name: str = "default", db: Session = Depends(get_db)):
    config = db.query(WarehouseConfig).filter(WarehouseConfig.name == warehouse_name).first()
    if not config:
        raise HTTPException(status_code=404, detail="仓库配置不存在")
    return db.query(Bin).filter(Bin.warehouse_id == config.id).all()


@router.get("/bins/{coordinate}", response_model=BinResponse)
def get_bin(coordinate: str, db: Session = Depends(get_db)):
    bin_obj = db.query(Bin).filter(Bin.coordinate == coordinate).first()
    if not bin_obj:
        raise HTTPException(status_code=404, detail="库位不存在")
    return bin_obj


@router.get("/layout")
def get_warehouse_layout(warehouse_name: str = "default", db: Session = Depends(get_db)):
    config = db.query(WarehouseConfig).filter(WarehouseConfig.name == warehouse_name).first()
    if not config:
        raise HTTPException(status_code=404, detail="仓库配置不存在")

    aisles = db.query(Aisle).filter(Aisle.warehouse_id == config.id).order_by(Aisle.index).all()
    result = []
    for aisle in aisles:
        bins = db.query(Bin).filter(Bin.aisle_id == aisle.id).order_by(Bin.row, Bin.level).all()
        result.append({
            "aisle_name": aisle.name,
            "aisle_index": aisle.index,
            "bins": [
                {
                    "coordinate": b.coordinate,
                    "x": b.x,
                    "y": b.y,
                    "row": b.row,
                    "level": b.level,
                    "sku_code": b.sku_code,
                    "quantity": b.quantity,
                    "frozen_quantity": b.frozen_quantity,
                }
                for b in bins
            ],
        })
    return {
        "config": {
            "id": config.id,
            "name": config.name,
            "num_aisles": config.num_aisles,
            "aisle_length": config.aisle_length,
            "aisle_spacing": config.aisle_spacing,
        },
        "aisles": result,
    }
