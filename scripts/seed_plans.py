from decimal import Decimal
from app.core.database import SessionLocal
from app.models import DataPlan


SAMPLE_PLANS = [
    {
        "network": "mtn",
        "plan_code": "MTN_1GB_30D",
        "plan_name": "MTN 1GB",
        "data_size": "1GB",
        "validity": "30d",
        "base_price": Decimal("350"),
    },
    {
        "network": "glo",
        "plan_code": "GLO_2GB_30D",
        "plan_name": "GLO 2GB",
        "data_size": "2GB",
        "validity": "30d",
        "base_price": Decimal("500"),
    },
]


def main():
    db = SessionLocal()
    try:
        for plan in SAMPLE_PLANS:
            existing = db.query(DataPlan).filter(DataPlan.plan_code == plan["plan_code"]).first()
            if not existing:
                db.add(DataPlan(**plan))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
