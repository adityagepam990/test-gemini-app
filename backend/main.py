import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Define database name
DB_NAME = "expenses.db"

# Helper to get database connection
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# Lifespan manager for FastAPI (to initialize database on startup)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database and tables
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    yield

# Create the FastAPI app
app = FastAPI(
    title="Personal Expense & Budget Tracker API",
    description="Backend API for managing expenses and budgets",
    version="1.0.0",
    lifespan=lifespan
)

# Pydantic Model for Input Validation
class ExpenseCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Title of the expense")
    amount: float = Field(..., gt=0, description="Amount spent (must be greater than 0)")
    category: str = Field(..., min_length=1, description="Category of the expense")
    date: str = Field(..., description="Date of expense in YYYY-MM-DD format")

# Endpoint: Serve SPA frontend index.html
@app.get("/")
def serve_index():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontend_path = os.path.join(base_dir, "frontend", "index.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Frontend index.html is not found. Please ensure frontend/index.html is created."}
    )

# Endpoint: List all expenses (with optional category filtering)
@app.get("/api/expenses")
def get_expenses(category: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if category and category != "All":
        cursor.execute(
            "SELECT id, title, amount, category, date FROM expenses WHERE category = ? ORDER BY date DESC, id DESC",
            (category,)
        )
    else:
        cursor.execute("SELECT id, title, amount, category, date FROM expenses ORDER BY date DESC, id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Endpoint: Add a new expense
@app.post("/api/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(expense: ExpenseCreate):
    # Validate date format (YYYY-MM-DD)
    try:
        datetime.strptime(expense.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD"
        )

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO expenses (title, amount, category, date) VALUES (?, ?, ?, ?)",
        (expense.title, expense.amount, expense.category, expense.date)
    )
    conn.commit()
    expense_id = cursor.lastrowid
    cursor.execute("SELECT id, title, amount, category, date FROM expenses WHERE id = ?", (expense_id,))
    new_row = cursor.fetchone()
    conn.close()
    return dict(new_row)

# Endpoint: Delete an expense
@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM expenses WHERE id = ?", (expense_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense with ID {expense_id} not found"
        )
    cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Expense deleted successfully"}

# Endpoint: Compute Analytics
@app.get("/api/analytics")
def get_analytics():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Total spent across all records
    cursor.execute("SELECT COALESCE(SUM(amount), 0.0) FROM expenses")
    total_spent = cursor.fetchone()[0]

    # 2. Total spent in the current calendar month (YYYY-MM)
    current_month_str = datetime.now().strftime("%Y-%m")
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0.0) FROM expenses WHERE date LIKE ?",
        (f"{current_month_str}%",)
    )
    monthly_spent = cursor.fetchone()[0]

    # 3. Top spending category overall (most total spending)
    cursor.execute("""
        SELECT category, SUM(amount) as cat_spent
        FROM expenses
        GROUP BY category
        ORDER BY cat_spent DESC
        LIMIT 1
    """)
    top_cat_row = cursor.fetchone()
    if top_cat_row:
        top_category = {
            "name": top_cat_row[0],
            "spent": top_cat_row[1]
        }
    else:
        top_category = None

    conn.close()

    return {
        "total_spent": total_spent,
        "monthly_spent": monthly_spent,
        "top_category": top_category
    }
