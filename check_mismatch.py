
import os
import sys
sys.path.append(os.path.join(os.getcwd(), "backend"))

from database import SessionLocal, LtvStandard

def check_region_mismatch():
    db = SessionLocal()
    try:
        # 광주은행인데 전남/광주/수도권/전북 외의 지역(전주, 군산 등 전북은행용)이 있는지 확인
        jb_regions = ["전주", "군산", "익산", "시지역", "군이하", "광역시"]
        mismatched = db.query(LtvStandard).filter(
            LtvStandard.bank_name == "광주은행",
            LtvStandard.region.in_(jb_regions)
        ).all()
        
        if mismatched:
            print(f"Found {len(mismatched)} mismatched records for 광주은행!")
            for m in mismatched[:5]:
                print(f"Bank: {m.bank_name}, Region: {m.region}, Usage: {m.usage_type}")
        else:
            print("No mismatched regions for 광주은행.")
            
        # 역으로 전북은행인데 전국 17개 도가 있는지 확인
        kj_regions = ["세종", "충북", "충남", "경북", "경남", "제주", "강원"]
        mismatched_jb = db.query(LtvStandard).filter(
            LtvStandard.bank_name == "전북은행",
            LtvStandard.region.in_(kj_regions)
        ).all()
        
        if mismatched_jb:
            print(f"Found {len(mismatched_jb)} mismatched records for 전북은행!")
            for m in mismatched_jb[:5]:
                print(f"Bank: {m.bank_name}, Region: {m.region}, Usage: {m.usage_type}")
    finally:
        db.close()

if __name__ == "__main__":
    check_region_mismatch()
