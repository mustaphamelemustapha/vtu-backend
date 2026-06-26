import sqlite3

conn = sqlite3.connect("vtu.db")
cursor = conn.cursor()

try:
    # Check if transaction exists
    cursor.execute("SELECT id, reference, external_reference, amount, status FROM transactions WHERE external_reference = 'R-XOLNQDQJKP'")
    row = cursor.fetchone()
    print("TX WITH EXT REF R-XOLNQDQJKP:", row)

    # Check user 8 balance
    cursor.execute("SELECT balance FROM wallets WHERE user_id = 8")
    row_wallet = cursor.fetchone()
    print("USER 8 WALLET:", row_wallet)
except Exception as e:
    print("ERROR:", e)
finally:
    conn.close()
