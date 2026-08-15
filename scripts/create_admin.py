"""
One-time setup script to create the first admin account.
Run from the project root:

    python scripts/create_admin.py

Never hardcode an admin password in source code — this is the only
supported way to create the first login.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Admin  # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing_count = db.query(Admin).count()
        if existing_count > 0:
            print(f"There {'is' if existing_count == 1 else 'are'} already {existing_count} admin account(s).")
            proceed = input("Create another one anyway? [y/N]: ").strip().lower()
            if proceed != "y":
                print("Cancelled.")
                return

        name = input("Admin name: ").strip()
        email = input("Admin email (used to log in): ").strip().lower()
        if not name or not email:
            print("Name and email are required.")
            return
        if db.query(Admin).filter(Admin.email == email).first():
            print("An admin with that email already exists.")
            return

        password = getpass.getpass("Admin password (min 8 characters): ")
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords didn't match.")
            return

        admin = Admin(name=name, email=email, password_hash=hash_password(password), role="super_admin")
        db.add(admin)
        db.commit()
        print(f"Admin account created for {email}. You can now log in at /admin/login.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
