import enum
from sqlalchemy import create_engine, Column, Integer, Enum, String
from sqlalchemy.orm import declarative_base

class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    AGENT = "agent"

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.CUSTOMER)

engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
print(UserRole.__members__)
