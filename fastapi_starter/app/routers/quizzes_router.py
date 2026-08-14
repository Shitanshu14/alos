"""
Week 14 — Quiz data model, submission + instant scoring, unlimited retries.

One quiz per chapter (5 questions, generated once and cached - see
quiz_generation_service.py). Learners can attempt it as many times as
they like; each attempt is scored instantly and saved for history.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .. import models, schemas
from ..database import get_db
from ..services.access_control_service import can_access_chapter
from ..services.quiz_generation_service import generate_quiz_questions
from .auth_router import get_current_user

logger = logging.getLogger("app.quizzes")

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])


def _get_accessible_chapter(chapter_id: int, current_user: models.User, db: Session) -> models.Chapter:
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")
    return chapter


@router.get(
    "/chapter/{chapter_id}",
    response_model=schemas.QuizOut,
    summary="Get (or generate) the quiz for a chapter",
)
def get_chapter_quiz(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = _get_accessible_chapter(chapter_id, current_user, db)

    quiz = db.query(models.Quiz).filter(models.Quiz.chapter_id == chapter_id).first()
    if not quiz:
        brief = db.query(models.ChapterBrief).filter(models.ChapterBrief.chapter_id == chapter_id).first()
        if not brief:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This chapter hasn't finished processing yet")

        key_concepts = json.loads(brief.key_concepts)
        generated_questions = generate_quiz_questions(db, chapter, key_concepts)
        if not generated_questions:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not enough concepts to build a quiz for this chapter")

        quiz = models.Quiz(chapter_id=chapter_id)
        db.add(quiz)
        try:
            db.flush()
            for i, q in enumerate(generated_questions):
                db.add(models.QuizQuestion(
                    quiz_id=quiz.id,
                    question_text=q["question_text"],
                    options=json.dumps(q["options"]),
                    correct_index=q["correct_index"],
                    order=i,
                ))
            db.commit()
            db.refresh(quiz)
        except IntegrityError:
            # Race condition: another concurrent request for the same
            # chapter's quiz won and already created it (Quiz.chapter_id
            # is unique) between our "does it exist" check and this
            # insert - roll back our half-done insert and use theirs
            # instead of crashing with a 500.
            db.rollback()
            quiz = db.query(models.Quiz).filter(models.Quiz.chapter_id == chapter_id).first()
            if not quiz:
                # Extremely unlikely (would mean the other transaction
                # rolled back too), but don't silently succeed with nothing.
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Could not create or find a quiz for this chapter - please retry",
                )

    questions = (
        db.query(models.QuizQuestion)
        .filter(models.QuizQuestion.quiz_id == quiz.id)
        .order_by(models.QuizQuestion.order.asc())
        .all()
    )

    return schemas.QuizOut(
        quiz_id=quiz.id,
        chapter_id=chapter.id,
        chapter_name=chapter.chapter_name,
        questions=[
            schemas.QuizQuestionPublic(question_id=q.id, question_text=q.question_text, options=json.loads(q.options))
            for q in questions
        ],
    )


@router.post(
    "/{quiz_id}/submit",
    response_model=schemas.QuizResultOut,
    summary="Submit answers and get an instant score (unlimited retries)",
)
def submit_quiz(
    quiz_id: int,
    payload: schemas.QuizSubmitRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    quiz = db.query(models.Quiz).filter(models.Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")

    chapter = db.query(models.Chapter).filter(models.Chapter.id == quiz.chapter_id).first()
    if not chapter or not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this quiz")

    questions = (
        db.query(models.QuizQuestion)
        .filter(models.QuizQuestion.quiz_id == quiz_id)
        .order_by(models.QuizQuestion.order.asc())
        .all()
    )
    if len(payload.answers) != len(questions):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Expected {len(questions)} answers, got {len(payload.answers)}",
        )

    # Each answer must be a valid index into its question's options list -
    # reject garbage/negative/out-of-range values with a clear error
    # instead of silently treating them as "wrong" (no scoring exploit
    # either way, since an invalid index can never equal correct_index,
    # but silently accepting malformed input is still worth rejecting).
    for i, (given, question) in enumerate(zip(payload.answers, questions)):
        option_count = len(json.loads(question.options))
        if not isinstance(given, int) or given < 0 or given >= option_count:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Answer at position {i} must be an integer between 0 and {option_count - 1}",
            )

    correct_indices = [q.correct_index for q in questions]
    score = sum(1 for given, correct in zip(payload.answers, correct_indices) if given == correct)

    previous_attempts = (
        db.query(models.QuizAttempt)
        .filter(models.QuizAttempt.quiz_id == quiz_id, models.QuizAttempt.user_id == current_user.id)
        .count()
    )
    attempt = models.QuizAttempt(
        quiz_id=quiz_id,
        user_id=current_user.id,
        attempt_number=previous_attempts + 1,
        score=score,
        total_questions=len(questions),
        answers=json.dumps(payload.answers),
    )
    db.add(attempt)
    db.commit()

    return schemas.QuizResultOut(
        attempt_number=attempt.attempt_number,
        score=score,
        total_questions=len(questions),
        correct_indices=correct_indices,
    )


@router.get(
    "/{quiz_id}/attempts",
    response_model=list[schemas.QuizAttemptOut],
    summary="My attempt history for a quiz (retry logic - shows every attempt, unlimited)",
)
def list_my_attempts(
    quiz_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attempts = (
        db.query(models.QuizAttempt)
        .filter(models.QuizAttempt.quiz_id == quiz_id, models.QuizAttempt.user_id == current_user.id)
        .order_by(models.QuizAttempt.attempt_number.asc())
        .all()
    )
    return [
        schemas.QuizAttemptOut(
            attempt_number=a.attempt_number, score=a.score,
            total_questions=a.total_questions, created_at=a.created_at,
        )
        for a in attempts
    ]
