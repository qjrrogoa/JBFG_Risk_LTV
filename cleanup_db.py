
import os
import sys
sys.path.append(os.path.join(os.getcwd(), "backend"))

from database import SessionLocal, LtvStandard
from sqlalchemy import text
from datetime import datetime

def cleanup_db():
    db = SessionLocal()
    try:
        # Check current dates
        dates = db.query(LtvStandard.effective_date).distinct().all()
        print(f"Distinct effective dates in DB: {[d[0] for d in dates]}")
        
        # Count records from 2026-04-01
        target_date = datetime(2026, 4, 1)
        count = db.query(LtvStandard).filter(LtvStandard.effective_date == target_date).count()
        print(f"Records on 2026-04-01: {count}")
        
        if count > 0:
            print(f"Deleting {count} records from 2026-04-01...")
            db.query(LtvStandard).filter(LtvStandard.effective_date == target_date).delete()
            db.commit()
            print("Successfully deleted.")
        else:
            print("No records found for that date.")
            
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_db()
