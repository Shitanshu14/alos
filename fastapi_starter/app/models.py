from sqlalchemy import Column, Integer, String
from .database import Base


# Ye ek table define karta hai users ke liye
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)


# Sample table - isko apne app ke hisaab se badal dena
# Jaise agar todo app hai to "Todo", agar blog hai to "Post" waghera
class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    owner_id = Column(Integer, index=True)
