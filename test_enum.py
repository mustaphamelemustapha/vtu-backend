import enum
from sqlalchemy import Enum

class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    AMBASSADOR = "ambassador"
    ADMIN = "admin"

print(Enum(UserRole).enums)
