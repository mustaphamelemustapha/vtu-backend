#!/usr/bin/env python3
import sys
import os
import argparse
from decimal import Decimal

# Add the project directory to sys.path to resolve imports correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import DataPlan
from app.core.database import SessionLocal

def main():
    parser = argparse.ArgumentParser(description="MELE DATA Admin Promotion and Cashback Script Control")
    parser.add_argument("--code", required=True, help="Plan code to modify (e.g. mtn-1gb, glo_500mb)")
    parser.add_argument("--active", action="store_true", help="Set promo_active to True")
    parser.add_argument("--inactive", action="store_true", help="Set promo_active to False")
    parser.add_argument("--price", type=float, help="Discounted price to set as active customer display_price")
    parser.add_argument("--agent-price", type=float, help="Discounted price to set as active agent_price")
    parser.add_argument("--old-price", type=float, help="Previous/original price (for strikethrough)")
    parser.add_argument("--label", help="Promo label (e.g. '20%% off'). If omitted and old-price/price are set, percent off is calculated.")
    parser.add_argument("--cashback-amount", type=float, help="Cashback amount (e.g. 10.0)")
    parser.add_argument("--cashback-label", help="Cashback label text (e.g. '₦10 cashback')")
    parser.add_argument("--clear", action="store_true", help="Clear all marketing and promo columns for this plan")

    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Search by code (case-insensitive and trimmed)
        search_code = str(args.code).strip().lower()
        plan = db.query(DataPlan).filter(DataPlan.plan_code.ilike(search_code)).first()
        
        if not plan:
            # Fallback search matching suffix or contains
            plan = db.query(DataPlan).filter(
                (DataPlan.plan_code.ilike(f"%:{search_code}")) | 
                (DataPlan.plan_code.ilike(f"%{search_code}%"))
            ).first()
            
        if not plan:
            print(f"Error: No data plan found with code or matching pattern: '{args.code}'")
            # List some available codes
            existing = db.query(DataPlan.plan_code, DataPlan.plan_name).limit(10).all()
            print("Here are some existing plan codes:")
            for p_code, p_name in existing:
                print(f"  - {p_code} ({p_name})")
            sys.exit(1)

        print(f"Found plan: {plan.plan_name} [{plan.plan_code}]")
        print(f"  Current base_price: ₦{plan.base_price}")
        print(f"  Current display_price: ₦{plan.display_price}")
        print(f"  Current promo_active: {getattr(plan, 'promo_active', False)}")
        
        if args.clear:
            plan.promo_active = False
            plan.promo_old_price = None
            plan.promo_label = None
            plan.cashback_amount = None
            plan.cashback_label = None
            plan.display_price = None
            db.commit()
            print("Successfully cleared all promotions and pricing overrides for the plan.")
            sys.exit(0)

        # Apply changes
        updated = False
        
        if args.active:
            plan.promo_active = True
            updated = True
            print("-> Set promo_active = True")
        elif args.inactive:
            plan.promo_active = False
            updated = True
            print("-> Set promo_active = False")

        if args.price is not None:
            plan.display_price = Decimal(str(args.price))
            updated = True
            print(f"-> Set display_price = ₦{args.price}")
            
        if args.agent_price is not None:
            plan.agent_price = Decimal(str(args.agent_price))
            updated = True
            print(f"-> Set agent_price = ₦{args.agent_price}")

        if args.old_price is not None:
            plan.promo_old_price = Decimal(str(args.old_price))
            updated = True
            print(f"-> Set promo_old_price = ₦{args.old_price}")

        # Label resolution
        label_to_set = args.label
        if label_to_set is None and getattr(plan, "promo_active", False):
            # Try to auto-calculate label if we have both prices
            target_price = plan.display_price or plan.base_price
            old_p = plan.promo_old_price
            if old_p and target_price and old_p > target_price:
                discount_pct = int(round((old_p - target_price) / old_p * Decimal("100")))
                if discount_pct > 0:
                    label_to_set = f"{discount_pct}% off"
                    print(f"-> Auto-calculated discount percent: {label_to_set}")

        if label_to_set is not None:
            plan.promo_label = label_to_set
            updated = True
            print(f"-> Set promo_label = '{label_to_set}'")

        if args.cashback_amount is not None:
            plan.cashback_amount = Decimal(str(args.cashback_amount))
            updated = True
            print(f"-> Set cashback_amount = ₦{args.cashback_amount}")
            
            # Default cashback label if not set
            if args.cashback_label is None and (plan.cashback_label is None or "cashback" in plan.cashback_label.lower()):
                args.cashback_label = f"₦{int(args.cashback_amount)} cashback"

        if args.cashback_label is not None:
            plan.cashback_label = args.cashback_label
            updated = True
            print(f"-> Set cashback_label = '{args.cashback_label}'")

        if updated:
            db.commit()
            db.refresh(plan)
            print("Successfully updated database record!")
            print(f"  New active price: ₦{plan.display_price or plan.base_price}")
            print(f"  New previous price: ₦{plan.promo_old_price}")
            print(f"  New promo_label: {plan.promo_label}")
            print(f"  New cashback_label: {plan.cashback_label}")
        else:
            print("No updates requested. Run with --help to see all arguments.")

    finally:
        db.close()

if __name__ == "__main__":
    main()
