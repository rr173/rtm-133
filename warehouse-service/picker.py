from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Picker, SHIFT_TEMPLATES
from schemas import PickerCreate, PickerUpdate, PickerResponse

router = APIRouter(prefix="/api/pickers", tags=["pickers"])


@router.post("", response_model=PickerResponse)
def create_picker(req: PickerCreate, db: Session = Depends(get_db)):
    if req.shift_code not in SHIFT_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"班次代码无效，可选值: {', '.join(SHIFT_TEMPLATES.keys())}"
        )
    picker = Picker(
        name=req.name,
        status="idle",
        shift_code=req.shift_code,
        current_x=0,
        current_y=0,
    )
    db.add(picker)
    db.commit()
    db.refresh(picker)
    return picker


@router.get("", response_model=list[PickerResponse])
def list_pickers(db: Session = Depends(get_db)):
    return db.query(Picker).all()


@router.get("/{picker_id}", response_model=PickerResponse)
def get_picker(picker_id: int, db: Session = Depends(get_db)):
    picker = db.query(Picker).filter(Picker.id == picker_id).first()
    if not picker:
        raise HTTPException(status_code=404, detail="拣货员不存在")
    return picker


@router.patch("/{picker_id}/status", response_model=PickerResponse)
def update_picker_status(picker_id: int, req: PickerUpdate, db: Session = Depends(get_db)):
    picker = db.query(Picker).filter(Picker.id == picker_id).first()
    if not picker:
        raise HTTPException(status_code=404, detail="拣货员不存在")
    if req.status and req.status not in ("idle", "busy", "offline"):
        raise HTTPException(status_code=400, detail="状态必须为idle/busy/offline")
    if req.shift_code and req.shift_code not in SHIFT_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"班次代码无效，可选值: {', '.join(SHIFT_TEMPLATES.keys())}"
        )
    if req.status:
        picker.status = req.status
    if req.shift_code:
        picker.shift_code = req.shift_code
    db.commit()
    db.refresh(picker)
    return picker
