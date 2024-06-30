from sqlalchemy import Column, Date, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class Book(Base):
    __tablename__ = "books"
    code = Column(String, primary_key=True)
    title = Column(String)
    author = Column(String)
    stock = Column(Integer)
    borrowed = Column(Integer)
    image = Column(String)

class Member(Base):
    __tablename__ = "members"
    code = Column(String, primary_key=True)
    name = Column(String)
    penalty_end_date = Column(Date)
    borrowings = relationship("Borrowing", back_populates="member")

class Borrowing(Base):
    __tablename__ = "borrowings"
    id = Column(String, primary_key=True)
    member_code = Column(String, ForeignKey("members.code"))
    book_code = Column(String, ForeignKey("books.code"))
    borrowed_at = Column(Date)

    member = relationship("Member", back_populates="borrowings")
    book = relationship("Book")