from datetime import date, timedelta
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Date, Integer, String, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, joinedload
from models import Base, Book, Member, Borrowing
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine, checkfirst=True)


app = FastAPI()

# CORS Headers (Changes Made)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


# Books

@app.get("/books")
def get_books(db: Session = Depends(get_db)):
    books = db.query(Book).all()
    for book in books:
        book.available_to_borrow = book.stock - book.borrowed
    return JSONResponse(status_code=200, content={"books": jsonable_encoder(books)})

@app.post("/books")
async def create_book(code: str, title: str, author: str, stock: int, db: Session = Depends(get_db)):
    book = Book(code=code, title=title, author=author, stock=stock, borrowed=0, image="")
    db.add(book)
    db.commit()

    # Fetch cover image from OpenLibrary
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://openlibrary.org/search.json?q={title}&_spellcheck_count=0&limit=10&fields=key,cover_i,title,subtitle,author_name,name&mode=everything") as response:
            response.raise_for_status()
            data = await response.json()

            # Check if there are any results
            if data["numFound"] > 0:
                # Iterate through documents to find one with cover_i
                for doc in data["docs"]:
                    if "cover_i" in doc:
                        cover_i = doc["cover_i"]
                        cover_image_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
                        book.image = cover_image_url
                        db.commit()
                        break

                # If cover_i not found, print a message
                if not book.image:
                    print(f"No cover image found for book: {title}")

            else:
                print("No results found for this book.")

    return JSONResponse(
        status_code=200, content={"status_code": 200, "message": "Book created", "image": book.image}
    )

@app.put("/books/{code}")
def update_book(code: str, title: str, author: str, stock: int, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.code == code).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    book.title = title
    book.author = author
    book.stock = stock
    db.commit()
    return JSONResponse(status_code=200, content={"status_code": 200, "message": "Book updated"})


@app.delete("/books/{code}")
def delete_book(code: str, db: Session = Depends(get_db)):
    book = db.query(Book).filter(Book.code == code).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    db.delete(book)
    db.commit()
    return JSONResponse(status_code=200, content={"status_code": 200, "message": "Book deleted"})


# Members

@app.get("/members")
def get_members(db: Session = Depends(get_db)):
    members = []
    for member in db.query(Member).all():
        member_data = jsonable_encoder(member)
        member_data["total_borrowed"] = len(member.borrowings)
        members.append(member_data)
    return JSONResponse(status_code=200, content={"members": members})


@app.post("/members")
def create_member(name: str, db: Session = Depends(get_db)):
    member_code = f"M{len(db.query(Member).all()) + 1:03}"
    member = Member(code=member_code, name=name, penalty_end_date=None)
    db.add(member)
    db.commit()
    return JSONResponse(
        status_code=200, content={"status_code": 200, "message": "Member created", "member_code": member_code}
    )


# Borrowing

@app.post("/borrowings")
def borrow_book(member_code: str, book_code: str, db: Session = Depends(get_db)):
    member = db.query(Member).filter(Member.code == member_code).first()
    book = db.query(Book).filter(Book.code == book_code).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if book.borrowed >= book.stock:
        raise HTTPException(status_code=400, detail="Book is currently out of stock")
    if len(member.borrowings) >= 2:
        raise HTTPException(status_code=400, detail="Member cannot borrow more than 2 books")
    if member.penalty_end_date and member.penalty_end_date > date.today():
        raise HTTPException(status_code=400, detail="Member is currently penalized")

    # Check for duplicate borrowing
    existing_borrowing = db.query(Borrowing).filter(Borrowing.member_code == member_code, Borrowing.book_code == book_code).first()
    if existing_borrowing:
        raise HTTPException(status_code=400, detail="Member has already borrowed this book")

    borrowing_id = f"B{len(db.query(Borrowing).all()) + 1:03}"
    borrowing = Borrowing(id=borrowing_id, member_code=member.code, book_code=book.code, borrowed_at=date.today())
    db.add(borrowing)
    book.borrowed += 1
    db.commit()
    return JSONResponse(
        status_code=200, content={"status_code": 200, "message": "Book borrowed", "borrowing_id": borrowing_id}
    )

@app.post("/returns")
def return_book(member_code: str, book_code: str, db: Session = Depends(get_db)):
    member = db.query(Member).filter(Member.code == member_code).first()
    book = db.query(Book).filter(Book.code == book_code).first()
    borrowing = db.query(Borrowing).filter(Borrowing.member_code == member.code, Borrowing.book_code == book.code).first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    if not borrowing:
        raise HTTPException(status_code=400, detail="Book is not borrowed by this member")

    days_overdue = (date.today() - borrowing.borrowed_at).days - 7
    if days_overdue > 0:
        penalty_days = days_overdue * 3
        member.penalty_end_date = date.today() + timedelta(days=penalty_days)

    db.delete(borrowing)
    book.borrowed -= 1
    db.commit()
    return JSONResponse(
        status_code=200, content={"status_code": 200, "message": "Book returned"}
    )

# Get All Borrowings
@app.get("/borrowings")
def get_borrowings(db: Session = Depends(get_db)):
    borrowings = (
        db.query(Borrowing)
        .options(joinedload(Borrowing.member), joinedload(Borrowing.book))
        .all()
    )

    data = [
        {
            "id": str(borrow.id),
            "member_name": borrow.member.name if borrow.member else None,
            "member_code": borrow.member_code,
            "book_title": borrow.book.title if borrow.book else None,
            "book_code": borrow.book_code,
            "borrowed_at": borrow.borrowed_at.isoformat() if borrow.borrowed_at else None
        }
        for borrow in borrowings
    ]

    return JSONResponse(status_code=200, content={"borrowings": jsonable_encoder(data)})

# Error Handling

@app.exception_handler(Exception)
def exception_handler(request, exc):
    json_resp = get_default_error_response()
    return json_resp

def get_default_error_response(status_code=500, message="Internal Server Error"):
    return JSONResponse(
        status_code=status_code,
        content={"status_code": status_code, "message": message},
    )