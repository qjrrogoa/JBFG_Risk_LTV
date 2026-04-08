
import os
import sys
sys.path.append(os.path.join(os.getcwd(), "backend"))

from database import SessionLocal, LtvStandard
from sqlalchemy import func

def check_mixing():
    db = SessionLocal()
    try:
        # Check bank_name mixing
        banks = db.query(LtvStandard.bank_name, func.count(LtvStandard.id)).group_by(LtvStandard.bank_name).all()
        print(f"LtvStandard records per bank: {banks}")
        
        # Check some samples
        samples = db.query(LtvStandard).limit(5).all()
        print("Sample data:")
        for s in samples:
            print(f"Bank: {s.bank_name}, Category: {s.category}, Usage: {s.usage_type}, Region: {s.region}, Value: {s.ltv_value}")
            
    finally:
        db.close()

if __name__ == "__main__":
    check_mixing()
