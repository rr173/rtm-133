from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Picker, WorkHourRecord, PickTask,
    SHIFT_TEMPLATES, SHIFT_MORNING, SHIFT_AFTERNOON, SHIFT_NIGHT,
    FATIGUE_THRESHOLD_HOURS,
)
from schemas import (
    PickerUpdate, PickerResponse, ShiftTemplateResponse,
    OnDutyPickerResponse, WorkHourRecordResponse, WorkHourStatsResponse,
)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def is_picker_on_duty(picker: Picker, check_time: datetime = None) -> bool:
    if check_time is None:
        check_time = datetime.utcnow()

    shift = SHIFT_TEMPLATES.get(picker.shift_code)
    if not shift:
        return False

    start_hour = shift["start_hour"]
    end_hour = shift["end_hour"]
    current_hour = check_time.hour

    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    else:
        return current_hour >= start_hour or current_hour < end_hour


def get_shift_name(shift_code: str) -> str:
    shift = SHIFT_TEMPLATES.get(shift_code)
    return shift["name"] if shift else shift_code


def get_available_pickers_for_allocation(db: Session) -> list[Picker]:
    all_idle = db.query(Picker).filter(Picker.status == "idle").all()
    on_duty_idle = [p for p in all_idle if is_picker_on_duty(p)]

    today = date.today()
    non_fatigued = []
    fatigued = []
    for p in on_duty_idle:
        rec = (
            db.query(WorkHourRecord)
            .filter(
                WorkHourRecord.picker_id == p.id,
                WorkHourRecord.work_date == today,
            )
            .first()
        )
        if rec and rec.is_fatigued:
            fatigued.append(p)
        else:
            non_fatigued.append(p)

    return non_fatigued + fatigued


def _get_or_create_work_hour_record(db: Session, picker_id: int, work_date: date) -> WorkHourRecord:
    rec = (
        db.query(WorkHourRecord)
        .filter(
            WorkHourRecord.picker_id == picker_id,
            WorkHourRecord.work_date == work_date,
        )
        .first()
    )
    if not rec:
        rec = WorkHourRecord(
            picker_id=picker_id,
            work_date=work_date,
            actual_work_seconds=0.0,
            is_fatigued=False,
        )
        db.add(rec)
        db.flush()
    return rec


def update_work_hours_on_task_start(db: Session, picker_id: int, started_at: datetime):
    work_date = started_at.date()
    rec = _get_or_create_work_hour_record(db, picker_id, work_date)
    if rec.first_task_started_at is None or started_at < rec.first_task_started_at:
        rec.first_task_started_at = started_at


def _recalculate_actual_work_seconds(db: Session, picker_id: int, work_date: date) -> float:
    day_start = datetime.combine(work_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    tasks = (
        db.query(PickTask)
        .filter(
            PickTask.picker_id == picker_id,
            PickTask.started_at.isnot(None),
            PickTask.completed_at.isnot(None),
            PickTask.started_at >= day_start,
            PickTask.started_at < day_end,
        )
        .order_by(PickTask.started_at)
        .all()
    )

    if not tasks:
        return 0.0

    intervals = []
    for t in tasks:
        t_start = max(t.started_at, day_start)
        t_end = min(t.completed_at, day_end) if t.completed_at < day_end else day_end
        if t_start < t_end:
            intervals.append((t_start, t_end))

    if not intervals:
        return 0.0

    intervals.sort()
    merged = [intervals[0]]
    for cur_start, cur_end in intervals[1:]:
        last_start, last_end = merged[-1]
        if cur_start <= last_end:
            merged[-1] = (last_start, max(last_end, cur_end))
        else:
            merged.append((cur_start, cur_end))

    total_seconds = 0.0
    for s, e in merged:
        total_seconds += (e - s).total_seconds()
    return total_seconds


def update_work_hours_on_task_complete(db: Session, picker_id: int, completed_at: datetime):
    work_date = completed_at.date()
    rec = _get_or_create_work_hour_record(db, picker_id, work_date)

    if rec.last_task_completed_at is None or completed_at > rec.last_task_completed_at:
        rec.last_task_completed_at = completed_at

    actual_seconds = _recalculate_actual_work_seconds(db, picker_id, work_date)
    rec.actual_work_seconds = actual_seconds
    rec.is_fatigued = (actual_seconds / 3600.0) > FATIGUE_THRESHOLD_HOURS


def is_picker_fatigued_today(db: Session, picker_id: int) -> bool:
    today = date.today()
    rec = (
        db.query(WorkHourRecord)
        .filter(
            WorkHourRecord.picker_id == picker_id,
            WorkHourRecord.work_date == today,
        )
        .first()
    )
    return rec.is_fatigued if rec else False


@router.get("/shifts", response_model=list[ShiftTemplateResponse])
def list_shift_templates():
    return [
        ShiftTemplateResponse(
            code=code,
            name=info["name"],
            start_hour=info["start_hour"],
            end_hour=info["end_hour"],
        )
        for code, info in SHIFT_TEMPLATES.items()
    ]


@router.get("/pickers/on-duty", response_model=list[OnDutyPickerResponse])
def list_on_duty_pickers(db: Session = Depends(get_db)):
    pickers = db.query(Picker).all()
    today = date.today()
    result = []
    for p in pickers:
        if not is_picker_on_duty(p):
            continue
        rec = (
            db.query(WorkHourRecord)
            .filter(
                WorkHourRecord.picker_id == p.id,
                WorkHourRecord.work_date == today,
            )
            .first()
        )
        result.append(OnDutyPickerResponse(
            picker_id=p.id,
            name=p.name,
            status=p.status,
            shift_code=p.shift_code,
            shift_name=get_shift_name(p.shift_code),
            is_fatigued=rec.is_fatigued if rec else False,
        ))
    return result


@router.get("/pickers/on-duty-at", response_model=list[OnDutyPickerResponse])
def list_on_duty_pickers_at_time(
    check_time: datetime = Query(..., description="检查时间, 格式: YYYY-MM-DDTHH:MM:SS"),
    db: Session = Depends(get_db),
):
    pickers = db.query(Picker).all()
    result = []
    for p in pickers:
        if not is_picker_on_duty(p, check_time):
            continue
        work_date = check_time.date()
        rec = (
            db.query(WorkHourRecord)
            .filter(
                WorkHourRecord.picker_id == p.id,
                WorkHourRecord.work_date == work_date,
            )
            .first()
        )
        result.append(OnDutyPickerResponse(
            picker_id=p.id,
            name=p.name,
            status=p.status,
            shift_code=p.shift_code,
            shift_name=get_shift_name(p.shift_code),
            is_fatigued=rec.is_fatigued if rec else False,
        ))
    return result


@router.patch("/pickers/{picker_id}/shift", response_model=PickerResponse)
def update_picker_shift(picker_id: int, req: PickerUpdate, db: Session = Depends(get_db)):
    picker = db.query(Picker).filter(Picker.id == picker_id).first()
    if not picker:
        raise HTTPException(status_code=404, detail="拣货员不存在")
    if req.shift_code and req.shift_code not in SHIFT_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"班次代码无效，可选值: {', '.join(SHIFT_TEMPLATES.keys())}"
        )
    if req.shift_code:
        picker.shift_code = req.shift_code
    db.commit()
    db.refresh(picker)
    return picker


@router.get("/work-hours/{picker_id}", response_model=list[WorkHourRecordResponse])
def get_picker_work_hours(
    picker_id: int,
    start_date: date = Query(..., description="开始日期, 格式: YYYY-MM-DD"),
    end_date: date = Query(..., description="结束日期, 格式: YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    picker = db.query(Picker).filter(Picker.id == picker_id).first()
    if not picker:
        raise HTTPException(status_code=404, detail="拣货员不存在")

    records = (
        db.query(WorkHourRecord)
        .filter(
            WorkHourRecord.picker_id == picker_id,
            WorkHourRecord.work_date >= start_date,
            WorkHourRecord.work_date <= end_date,
        )
        .order_by(WorkHourRecord.work_date)
        .all()
    )
    result = []
    for r in records:
        result.append(WorkHourRecordResponse(
            id=r.id,
            picker_id=r.picker_id,
            work_date=r.work_date,
            first_task_started_at=r.first_task_started_at,
            last_task_completed_at=r.last_task_completed_at,
            actual_work_seconds=r.actual_work_seconds,
            actual_work_hours=round(r.actual_work_seconds / 3600.0, 2),
            is_fatigued=r.is_fatigued,
        ))
    return result


@router.get("/work-hours/stats", response_model=list[WorkHourStatsResponse])
def get_work_hour_stats(
    start_date: date = Query(..., description="开始日期, 格式: YYYY-MM-DD"),
    end_date: date = Query(..., description="结束日期, 格式: YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    pickers = db.query(Picker).all()
    result = []
    for p in pickers:
        records = (
            db.query(WorkHourRecord)
            .filter(
                WorkHourRecord.picker_id == p.id,
                WorkHourRecord.work_date >= start_date,
                WorkHourRecord.work_date <= end_date,
                WorkHourRecord.actual_work_seconds > 0,
            )
            .order_by(WorkHourRecord.work_date)
            .all()
        )
        if not records:
            continue

        daily_hours = [r.actual_work_seconds / 3600.0 for r in records]
        total_hours = sum(daily_hours)
        avg_hours = total_hours / len(daily_hours) if daily_hours else 0.0
        max_consecutive = max(daily_hours) if daily_hours else 0.0
        fatigue_days = sum(1 for r in records if r.is_fatigued)

        result.append(WorkHourStatsResponse(
            picker_id=p.id,
            name=p.name,
            shift_code=p.shift_code,
            total_work_hours=round(total_hours, 2),
            avg_daily_hours=round(avg_hours, 2),
            max_consecutive_hours=round(max_consecutive, 2),
            work_days=len(records),
            fatigue_days=fatigue_days,
        ))
    return result
