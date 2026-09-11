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

# Lifespan manager for FastAPI (to initialize database and migrate schema on startup)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database and tables
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create expenses table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            is_recurring INTEGER DEFAULT 0,
            frequency TEXT
        )
    """)
    
    # Migration helper: ensure is_recurring and frequency columns exist on expenses table
    cursor.execute("PRAGMA table_info(expenses)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "is_recurring" not in columns:
        cursor.execute("ALTER TABLE expenses ADD COLUMN is_recurring INTEGER DEFAULT 0")
    if "frequency" not in columns:
        cursor.execute("ALTER TABLE expenses ADD COLUMN frequency TEXT")
        
    # Create settings table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    # Store default Monthly Budget Limit to 2000.0 if not present
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('monthly_budget_limit', '2000.0')")
    
    conn.commit()
    conn.close()
    yield

# Create the FastAPI app
app = FastAPI(
    title="Personal Expense & Budget Tracker API",
    description="Backend API for managing expenses, dynamic budgets, and subscriptions",
    version="1.1.0",
    lifespan=lifespan
)

# Pydantic Model for Input Validation
class ExpenseCreate(BaseModel):
    title: str = Field(..., min_length=1, description="Title of the expense")
    amount: float = Field(..., gt=0, description="Amount spent (must be greater than 0)")
    category: str = Field(..., min_length=1, description="Category of the expense")
    date: str = Field(..., description="Date of expense in YYYY-MM-DD format")
    is_recurring: bool = Field(False, description="Is this a recurring subscription?")
    frequency: str = Field(None, description="Frequency of the subscription (Weekly, Monthly, Yearly)")

# Pydantic Model for Budget Limit Update
class BudgetUpdate(BaseModel):
    budget_limit: float = Field(..., gt=0, description="New monthly budget limit")

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

# Endpoint: List all expenses (with optional category, search, start_date, and end_date filtering)
@app.get("/api/expenses")
def get_expenses(
    category: str = None,
    search: str = None,
    start_date: str = None,
    end_date: str = None
):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Dynamic SQL construction
    query = "SELECT id, title, amount, category, date, is_recurring, frequency FROM expenses"
    conditions = []
    params = []
    
    if category and category != "All":
        conditions.append("category = ?")
        params.append(category)
        
    if search:
        conditions.append("title LIKE ?")
        params.append(f"%{search}%")
        
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
        
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY date DESC, id DESC"
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Endpoint: Add a new expense (with optional subscription support)
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

    # Validate subscription frequency
    if expense.is_recurring:
        if expense.frequency not in ["Weekly", "Monthly", "Yearly"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Frequency must be one of: Weekly, Monthly, Yearly"
            )
    else:
        expense.frequency = None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO expenses (title, amount, category, date, is_recurring, frequency) VALUES (?, ?, ?, ?, ?, ?)",
        (
            expense.title,
            expense.amount,
            expense.category,
            expense.date,
            1 if expense.is_recurring else 0,
            expense.frequency
        )
    )
    conn.commit()
    expense_id = cursor.lastrowid
    cursor.execute("SELECT id, title, amount, category, date, is_recurring, frequency FROM expenses WHERE id = ?", (expense_id,))
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

# Endpoint: List recurring expenses & compute projected monthly subscription cost
@app.get("/api/subscriptions")
def get_subscriptions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, amount, category, date, is_recurring, frequency FROM expenses WHERE is_recurring = 1")
    rows = cursor.fetchall()
    conn.close()
    
    subscriptions = [dict(row) for row in rows]
    
    # Calculate projected monthly cost
    projected_monthly_cost = 0.0
    for sub in subscriptions:
        amount = sub["amount"]
        freq = sub["frequency"]
        if freq == "Weekly":
            projected_monthly_cost += amount * (52.0 / 12.0) # 4.33 weeks per month
        elif freq == "Monthly":
            projected_monthly_cost += amount
        elif freq == "Yearly":
            projected_monthly_cost += amount / 12.0
            
    return {
        "subscriptions": subscriptions,
        "projected_monthly_cost": projected_monthly_cost
    }

# Endpoint: Retrieve stored Monthly Budget Limit
@app.get("/api/settings/budget")
def get_budget_limit():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'monthly_budget_limit'")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return {"budget_limit": 2000.0}
    return {"budget_limit": float(row[0])}

# Endpoint: Update stored Monthly Budget Limit
@app.post("/api/settings/budget")
def update_budget_limit(update: BudgetUpdate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO settings (key, value) VALUES ('monthly_budget_limit', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (str(update.budget_limit), str(update.budget_limit))
    )
    conn.commit()
    conn.close()
    return {
        "success": True, 
        "budget_limit": update.budget_limit, 
        "message": "Budget limit updated successfully"
    }

# Endpoint: Compute Analytics (with DB-backed budget limit)
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

    # 4. Fetch stored Monthly Budget Limit from Settings
    cursor.execute("SELECT value FROM settings WHERE key = 'monthly_budget_limit'")
    budget_row = cursor.fetchone()
    budget_limit = float(budget_row[0]) if budget_row else 2000.0

    conn.close()

    return {
        "total_spent": total_spent,
        "monthly_spent": monthly_spent,
        "top_category": top_category,
        "budget_limit": budget_limit
    }
