"""
Security-critical: admin accounts can NOT be created through the public
signup API (see schemas.py's ALLOWED_SIGNUP_ROLES) - that would let
anyone grant themselves admin access. This script is the intended way
to create one, and should only be run by a trusted operator with direct
server/database access (e.g. during initial deployment setup).

Usage:
    python -m app.create_admin --email admin@example.com --password "StrongPass@123" --name "Admin"

Or interactively (prompts for each value, hides the password):
    python -m app.create_admin
"""
import argparse
import getpass
import sys

from .database import SessionLocal, engine, Base
from . import models, auth
from .schemas import validate_password_strength


def create_admin(email: str, password: str, full_name: str, phone: str) -> None:
    try:
        validate_password_strength(password)
    except ValueError as exc:
        print(f"Password doesn't meet requirements: {exc}")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.email == email).first()
        if existing:
            print(f"A user with email '{email}' already exists (role={existing.role}).")
            sys.exit(1)

        admin = models.User(
            full_name=full_name,
            email=email,
            phone=phone,
            hashed_password=auth.hash_password(password),
            role="admin",
            is_verified=True,  # trusted creation path - skip the email-verification step
        )
        db.add(admin)
        db.commit()
        print(f"Admin account created: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an ALOS admin account (trusted operator use only)")
    parser.add_argument("--email", help="Admin's email")
    parser.add_argument("--password", help="Admin's password (omit to be prompted securely instead)")
    parser.add_argument("--name", default="Admin", help="Admin's full name")
    parser.add_argument("--phone", default="0000000000", help="Admin's phone number")
    args = parser.parse_args()

    email = args.email or input("Admin email: ").strip()
    password = args.password or getpass.getpass("Admin password: ")

    create_admin(email=email, password=password, full_name=args.name, phone=args.phone)
