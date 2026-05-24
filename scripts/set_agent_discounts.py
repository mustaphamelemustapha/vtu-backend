import os
import sys
from decimal import Decimal

# Add the project root to python path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import SessionLocal
from app.models.pricing_rule import PricingRule, PricingRole, MarginType

def set_agent_margins():
    """
    Easily configure the exact agent discounts in bulk.
    
    The backend pricing engine adds `margin` to the base price of a service.
    For normal users, this margin is typically higher.
    For agents/resellers, you can set the margin lower (e.g., to 0 or a very small markup).
    """
    db = SessionLocal()
    try:
        # Define the exact margins you want for Agents (Resellers)
        # Margin type can be "fixed" (Naira) or "percentage" (%)
        agent_rules = [
            # Example: 0 Naira markup for MTN Data (Base price only)
            {"network": "data:mtn:glad", "margin": 0, "margin_type": MarginType.FIXED},
            {"network": "data:airtel:glad", "margin": 0, "margin_type": MarginType.FIXED},
            {"network": "data:glo:glad", "margin": 0, "margin_type": MarginType.FIXED},
            {"network": "data:9mobile:glad", "margin": 0, "margin_type": MarginType.FIXED},
            
            # Example: 1% margin on Airtime for agents
            {"network": "airtime:mtn:glad", "margin": 1, "margin_type": MarginType.PERCENTAGE},
            {"network": "airtime:airtel:glad", "margin": 1, "margin_type": MarginType.PERCENTAGE},
            {"network": "airtime:glo:glad", "margin": 1, "margin_type": MarginType.PERCENTAGE},
            {"network": "airtime:9mobile:glad", "margin": 1, "margin_type": MarginType.PERCENTAGE},
            
            # Add electricity or cable below if needed
            # {"network": "electricity:ikeja:glad", "margin": 10, "margin_type": MarginType.FIXED},
        ]

        print("Updating Agent (Reseller) pricing rules...")
        
        for rule_data in agent_rules:
            network = rule_data["network"]
            margin = Decimal(str(rule_data["margin"]))
            margin_type = rule_data["margin_type"].value if hasattr(rule_data["margin_type"], 'value') else rule_data["margin_type"]
            
            # Check if rule exists
            existing = db.query(PricingRule).filter(
                PricingRule.network == network,
                PricingRule.role == PricingRole.RESELLER
            ).first()
            
            if existing:
                existing.margin = margin
                existing.margin_type = margin_type
                print(f"Updated: {network} -> margin: {margin} ({margin_type})")
            else:
                new_rule = PricingRule(
                    network=network,
                    role=PricingRole.RESELLER,
                    margin=margin,
                    margin_type=margin_type
                )
                db.add(new_rule)
                print(f"Created: {network} -> margin: {margin} ({margin_type})")
                
        db.commit()
        print("\nSuccessfully updated all agent pricing rules!")
        
    except Exception as e:
        print(f"Error updating agent margins: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    set_agent_margins()
