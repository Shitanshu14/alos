"""
Week 12 — Classroom/session data model, homework assignment, and
automatic student access to assigned content.

A teacher creates a classroom, enrolls students into it, then assigns
homework (a chapter or a specific content item). The moment homework is
assigned, every enrolled student automatically has access to it - see
`get_classroom_content` below and `access_control_service.py`, which
chapters/games/quizzes all check against.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.notification_service import notify
from .auth_router import get_current_user

logger = logging.getLogger("app.classrooms")

router = APIRouter(prefix="/classrooms", tags=["Classrooms"])


def _require_teacher(user: models.User) -> None:
    if user.role not in ("teacher", "school"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only teacher/school accounts can manage classrooms")


def _get_owned_classroom(classroom_id: int, user: models.User, db: Session) -> models.Classroom:
    classroom = db.query(models.Classroom).filter(models.Classroom.id == classroom_id).first()
    if not classroom:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Classroom not found")
    if classroom.teacher_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This classroom doesn't belong to your account")
    return classroom


def _is_enrolled(classroom_id: int, student_id: int, db: Session) -> bool:
    return (
        db.query(models.ClassroomEnrollment)
        .filter(
            models.ClassroomEnrollment.classroom_id == classroom_id,
            models.ClassroomEnrollment.student_id == student_id,
        )
        .first()
        is not None
    )


# =========================================================
# Create / list classrooms
# =========================================================
@router.post("/", response_model=schemas.ClassroomOut, status_code=status.HTTP_201_CREATED, summary="Create a classroom")
def create_classroom(
    payload: schemas.ClassroomCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_teacher(current_user)
    classroom = models.Classroom(
        teacher_id=current_user.id,
        name=payload.name,
        subject=payload.subject,
        class_name=payload.class_name,
    )
    db.add(classroom)
    db.commit()
    db.refresh(classroom)
    return schemas.ClassroomOut(
        id=classroom.id, name=classroom.name, subject=classroom.subject,
        class_name=classroom.class_name, student_count=0, created_at=classroom.created_at,
    )


@router.get("/", response_model=list[schemas.ClassroomOut], summary="List my classrooms (teacher)")
def list_my_classrooms(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_teacher(current_user)
    classrooms = db.query(models.Classroom).filter(models.Classroom.teacher_id == current_user.id).all()
    results = []
    for c in classrooms:
        count = db.query(models.ClassroomEnrollment).filter(models.ClassroomEnrollment.classroom_id == c.id).count()
        results.append(schemas.ClassroomOut(
            id=c.id, name=c.name, subject=c.subject, class_name=c.class_name,
            student_count=count, created_at=c.created_at,
        ))
    return results


@router.get("/my-classrooms", response_model=list[schemas.ClassroomOut], summary="List classrooms I'm enrolled in (student)")
def list_enrolled_classrooms(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only student accounts have enrollments")
    enrollments = db.query(models.ClassroomEnrollment).filter(models.ClassroomEnrollment.student_id == current_user.id).all()
    results = []
    for e in enrollments:
        c = db.query(models.Classroom).filter(models.Classroom.id == e.classroom_id).first()
        if c:
            count = db.query(models.ClassroomEnrollment).filter(models.ClassroomEnrollment.classroom_id == c.id).count()
            results.append(schemas.ClassroomOut(
                id=c.id, name=c.name, subject=c.subject, class_name=c.class_name,
                student_count=count, created_at=c.created_at,
            ))
    return results


# =========================================================
# Enrollment
# =========================================================
@router.post("/{classroom_id}/enroll", response_model=schemas.MessageResponse, summary="Enroll a student in a classroom")
def enroll_student(
    classroom_id: int,
    payload: schemas.EnrollStudentRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_teacher(current_user)
    classroom = _get_owned_classroom(classroom_id, current_user, db)

    student = db.query(models.User).filter(models.User.id == payload.student_user_id).first()
    if not student or student.role != "student":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No student account found with that ID")

    if _is_enrolled(classroom_id, student.id, db):
        return {"message": f"{student.full_name} is already enrolled in this classroom"}

    db.add(models.ClassroomEnrollment(classroom_id=classroom_id, student_id=student.id))
    db.commit()
    notify(db, student, title="Added to a classroom", message=f'You were added to "{classroom.name}".')
    db.commit()
    return {"message": f"{student.full_name} enrolled successfully"}


@router.get("/{classroom_id}/students", response_model=list[schemas.StudentOut], summary="List students enrolled in a classroom")
def list_students(
    classroom_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_teacher(current_user)
    _get_owned_classroom(classroom_id, current_user, db)

    enrollments = db.query(models.ClassroomEnrollment).filter(models.ClassroomEnrollment.classroom_id == classroom_id).all()
    students = []
    for e in enrollments:
        s = db.query(models.User).filter(models.User.id == e.student_id).first()
        if s:
            students.append(schemas.StudentOut(id=s.id, full_name=s.full_name, email=s.email))
    return students


# =========================================================
# Homework
# =========================================================
@router.post(
    "/{classroom_id}/homework",
    response_model=schemas.HomeworkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assign homework to a classroom (auto-grants every enrolled student access)",
)
def assign_homework(
    classroom_id: int,
    payload: schemas.HomeworkCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_teacher(current_user)
    classroom = _get_owned_classroom(classroom_id, current_user, db)

    if not payload.chapter_id and not payload.content_item_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Provide chapter_id or content_item_id")

    if payload.chapter_id and not db.query(models.Chapter).filter(models.Chapter.id == payload.chapter_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if payload.content_item_id and not db.query(models.ContentItem).filter(models.ContentItem.id == payload.content_item_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    homework = models.Homework(
        classroom_id=classroom_id,
        assigned_by=current_user.id,
        chapter_id=payload.chapter_id,
        content_item_id=payload.content_item_id,
        title=payload.title,
        message=payload.message,
        due_date=payload.due_date,
    )
    db.add(homework)
    db.commit()
    db.refresh(homework)

    # Notify every enrolled student - they now have automatic access (no per-student share needed)
    enrollments = db.query(models.ClassroomEnrollment).filter(models.ClassroomEnrollment.classroom_id == classroom_id).all()
    for e in enrollments:
        student = db.query(models.User).filter(models.User.id == e.student_id).first()
        if student:
            notify(db, student, title="New homework assigned", message=f'"{homework.title}" was assigned in {classroom.name}.')
    db.commit()

    return homework


@router.get("/{classroom_id}/homework", response_model=list[schemas.HomeworkOut], summary="List a classroom's homework")
def list_homework(
    classroom_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_owner = db.query(models.Classroom).filter(
        models.Classroom.id == classroom_id, models.Classroom.teacher_id == current_user.id
    ).first() is not None
    is_student_member = current_user.role == "student" and _is_enrolled(classroom_id, current_user.id, db)

    if not (is_owner or is_student_member):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this classroom")

    return db.query(models.Homework).filter(models.Homework.classroom_id == classroom_id).order_by(models.Homework.created_at.desc()).all()


# =========================================================
# Auto student access to class content
# =========================================================
@router.get(
    "/{classroom_id}/content",
    response_model=list[schemas.ContentItemOut],
    summary="Content automatically available to an enrolled student (from all assigned homework)",
)
def get_classroom_content(
    classroom_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_owner = db.query(models.Classroom).filter(
        models.Classroom.id == classroom_id, models.Classroom.teacher_id == current_user.id
    ).first() is not None
    is_student_member = current_user.role == "student" and _is_enrolled(classroom_id, current_user.id, db)

    if not (is_owner or is_student_member):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this classroom")

    homework_rows = db.query(models.Homework).filter(models.Homework.classroom_id == classroom_id).all()

    content_item_ids = set()
    for hw in homework_rows:
        if hw.content_item_id:
            content_item_ids.add(hw.content_item_id)
        if hw.chapter_id:
            matches = db.query(models.ContentMatch).filter(
                models.ContentMatch.chapter_id == hw.chapter_id,
                models.ContentMatch.content_item_id.isnot(None),
            ).all()
            content_item_ids.update(m.content_item_id for m in matches)

    if not content_item_ids:
        return []

    items = db.query(models.ContentItem).filter(models.ContentItem.id.in_(content_item_ids)).all()
    return [
        schemas.ContentItemOut(
            id=i.id, title=i.title, content_type=i.content_type, subject=i.subject, class_name=i.class_name,
            description=i.description, body=i.body, media_url=i.media_url,
            is_seed_placeholder=i.is_seed_placeholder, is_generated=i.is_generated,
            tags=[t.name for t in i.tags], created_at=i.created_at, updated_at=i.updated_at,
        )
        for i in items
    ]
