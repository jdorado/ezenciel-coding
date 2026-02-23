from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from src.config import settings

# Parse connection string logic
os.makedirs("data", exist_ok=True)

engine = create_engine(
    settings.db_path, 
    connect_args={"check_same_thread": False} if "sqlite" in settings.db_path else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
