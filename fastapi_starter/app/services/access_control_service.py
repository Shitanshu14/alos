"""
Shared access-control helpers.

Pulled out into its own module (rather than duplicated across
chapters_router.py, games_router.py, quizzes_router.py) so "who can see
this chapter's stuff" is defined in exactly one place.
"""
from .. import models


def can_access_chapter(db, chapter: models.Chapter, user: models.User) -> bool:
    """A user can access a chapter if either:
    - they uploaded it (the original owner-only rule from Week 3), or
    - they're a student enrolled in a classroom that has homework
      pointing at this chapter (Week 12's "auto student access").
    """
    if chapter.uploaded_by == user.id:
        return True

    if user.role == "student":
        enrolled_classroom_ids = [
            row.classroom_id for row in
            db.query(models.ClassroomEnrollment)
            .filter(models.ClassroomEnrollment.student_id == user.id)
            .all()
        ]
        if enrolled_classroom_ids:
            has_homework = (
                db.query(models.Homework)
                .filter(
                    models.Homework.classroom_id.in_(enrolled_classroom_ids),
                    models.Homework.chapter_id == chapter.id,
                )
                .first()
            )
            if has_homework:
                return True

    return False
