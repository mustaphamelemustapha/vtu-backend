import pytest
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models import DataPlan, PricingRule, PricingRole, User, UserRole
from app.api.v1.endpoints.data import list_data_plans

def test_data_plan_promo_mapping():
    # Use SQLite in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    
    # Register the standard PostgreSQL-style now() function in SQLite
    from sqlalchemy.event import listens_for
    import sqlite3
    import datetime
    
    @listens_for(engine, "connect")
    def register_sqlite_now(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            dbapi_connection.create_function("now", 0, lambda: datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Create user
        user = User(
            email="test_user@example.com",
            full_name="Test User",
            referral_code="REF123",
            hashed_password="...",
            role=UserRole.USER,
            is_active=True
        )
        db.add(user)
        db.commit()

        # Add pricing rule for MTN
        rule = PricingRule(
            network="mtn",
            role=PricingRole.USER,
            margin=Decimal("50"),
            margin_type="fixed"
        )
        db.add(rule)

        # Create a normal plan without promo
        plan1 = DataPlan(
            network="mtn",
            plan_code="mtn-500mb",
            plan_name="MTN 500MB",
            data_size="500MB",
            validity="30 days",
            base_price=Decimal("200"),
            is_active=True
        )
        # Create a plan with database-set promo details
        plan2 = DataPlan(
            network="mtn",
            plan_code="mtn-1gb-promo",
            plan_name="MTN 1GB",
            data_size="1GB",
            validity="30 days",
            base_price=Decimal("400"),
            display_price=Decimal("350"), # discounted price
            promo_active=True,
            promo_old_price=Decimal("450"), # original price override
            promo_label="Special Deal",
            cashback_amount=Decimal("15"),
            cashback_label="₦15 cashback",
            is_active=True
        )
        # Create a plan with dynamic percent-off calculation
        plan3 = DataPlan(
            network="mtn",
            plan_code="mtn-2gb-promo",
            plan_name="MTN 2GB",
            data_size="2GB",
            validity="30 days",
            base_price=Decimal("800"),
            display_price=Decimal("700"), # discounted price
            promo_active=True,
            is_active=True
        )

        db.add_all([plan1, plan2, plan3])
        db.commit()

        # Call the list_data_plans endpoint function
        results = list_data_plans(user=user, db=db)
        
        # Verify results
        assert len(results) >= 3
        
        # Sort results by code for assertions
        p_map = {p.plan_code: p for p in results}
        
        # Check plan1 (No promo)
        p1 = p_map["mtn-500mb"]
        assert p1.price == Decimal("250") # base_price (200) + margin (50)
        assert not p1.promo_active
        assert p1.promo_old_price is None
        assert p1.promo_label is None
        assert p1.cashback_label is None

        # Check plan2 (Explicit promo details)
        p2 = p_map["mtn-1gb-promo"]
        assert p2.price == Decimal("350") # display_price
        assert p2.promo_active
        assert p2.promo_old_price == Decimal("450")
        assert p2.promo_label == "Special Deal"
        assert p2.cashback_amount == Decimal("15")
        assert p2.cashback_label == "₦15 cashback"

        # Check plan3 (Dynamic percent-off calculation)
        p3 = p_map["mtn-2gb-promo"]
        assert p3.price == Decimal("700") # display_price
        assert p3.promo_active
        # old price defaults to standard retail: base_price (800) + margin (50) = 850
        assert p3.promo_old_price == Decimal("850") 
        # percent off: (850 - 700) / 850 = 17.6% -> 18% off
        assert p3.promo_label == "18% off"
        assert p3.cashback_label is None
        
    finally:
        db.close()
