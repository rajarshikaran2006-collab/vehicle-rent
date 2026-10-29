"""Create an administrator without exposing a public privileged-registration endpoint."""
from getpass import getpass
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
from .db import create_database, default_database_url, local_now
from .models import User
import os
import re


def main():
    load_dotenv(ROOT/'.env')
    name=input('Administrator name: ').strip()
    email=input('Administrator email: ').strip().lower()
    password=getpass('Password (at least 8 characters): ')
    confirm=getpass('Confirm password: ')
    if len(name)<2 or len(name)>100 or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise SystemExit('Enter a valid name and email.')
    if len(password)<8 or len(password)>128 or password!=confirm:
        raise SystemExit('Passwords must match and be 8–128 characters long.')
    engine,Session=create_database(os.environ.get('DATABASE_URL',default_database_url()))
    with Session() as db:
        if db.scalar(select(User.id).where(User.email==email)):
            raise SystemExit('An account with that email already exists.')
        db.add(User(name=name,email=email,password_hash=generate_password_hash(password),role='admin',active=True,created_at=local_now()))
        db.commit()
    engine.dispose()
    print('Administrator created. You may now log in.')


if __name__=='__main__': main()
