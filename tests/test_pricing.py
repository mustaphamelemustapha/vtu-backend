from decimal import Decimal
from app.services.pricing import get_price_for_user
from app.models import PricingRule, PricingRole, DataPlan, UserRole


class DummyDB:
    def __init__(self, rule=None):
        self.rule = rule

    def query(self, model):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.rule



def test_pricing_user_no_rule():
    plan = DataPlan(network="mtn", base_price=Decimal("100"), plan_code="X", plan_name="X", data_size="1GB", validity="1d")
    db = DummyDB(None)
    price = get_price_for_user(db, plan, UserRole.USER)
    assert price == Decimal("100")


def test_pricing_reseller_rule():
    plan = DataPlan(network="mtn", base_price=Decimal("100"), plan_code="X", plan_name="X", data_size="1GB", validity="1d")
    rule = PricingRule(network="mtn", role=PricingRole.RESELLER, margin=Decimal("-10"))
    db = DummyDB(rule)
    price = get_price_for_user(db, plan, UserRole.RESELLER)
    assert price == Decimal("90")
