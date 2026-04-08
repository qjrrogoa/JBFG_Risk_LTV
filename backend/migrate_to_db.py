import os
import pandas as pd
from database import engine, SessionLocal, LtvStandard, init_db
from sqlalchemy.orm import Session
from datetime import datetime

# 데이터 경로
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

def migrate():
    print("--- STEP 1: Initializing Tables ---")
    init_db()
    db: Session = SessionLocal()
    
    # 은행 이름 수동 지정 (인코딩 안전하게)
    banks = [
        {"id": "kjb", "name": "광주은행", "path": os.path.join(DATA_DIR, "LTV_기준(광주은행).csv")},
        {"id": "jbb", "name": "전북은행", "path": os.path.join(DATA_DIR, "LTV_기준(전북은행).csv")}
    ]
    
    try:
        print("--- STEP 2: Cleaning old records ---")
        db.query(LtvStandard).delete()
        db.commit() # Commit delete first
        
        for bank in banks:
            path = bank["path"]
            name = bank["name"]
            
            if not os.path.exists(path):
                print(f"File not found: {path}")
                continue
                
            print(f"Moving data for: {name} (Unicode: {name.encode('unicode_escape')})...")
            # 한국어 CSV이므로 cp949 또는 utf-8-sig 시도
            try:
                df = pd.read_csv(path, encoding="utf-8-sig")
            except:
                df = pd.read_csv(path, encoding="cp949")
            
            if "적용시작일" not in df.columns:
                df.insert(0, "적용시작일", "1900-01-01")
            
            # Wide -> Long
            id_vars = ["적용시작일", "구분", "담보종류"]
            melted = df.melt(id_vars=id_vars, var_name="region", value_name="ltv_value")
            
            records = []
            for _, row in melted.iterrows():
                try:
                    eff_date = pd.to_datetime(row["적용시작일"])
                except:
                    eff_date = datetime(1900, 1, 1)
                
                records.append(LtvStandard(
                    bank_name=name,
                    category=str(row["구분"]),
                    usage_type=str(row["담보종류"]),
                    region=str(row["region"]),
                    ltv_value=float(row["ltv_value"]) if pd.notnull(row["ltv_value"]) else 80.0,
                    effective_date=eff_date
                ))
            
            db.bulk_save_objects(records)
            db.commit() # Commit per bank
            print(f"Successfully migrated {name}: {len(records)} rows")
            
        print("\nCOMPLETED: All data fixed and migrated successfully!")
        
    except Exception as e:
        print(f"Migration failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
