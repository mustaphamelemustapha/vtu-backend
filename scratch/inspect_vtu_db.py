import os
from sqlalchemy import create_engine, MetaData, Table
from sqlalchemy.orm import sessionmaker

def inspect_db_file(db_url, name):
    print(f"\n=================== INSPECTING {name} ({db_url}) ===================")
    if not os.path.exists(db_url.replace("sqlite:///", "")):
        print("Database file does not exist.")
        return
        
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    metadata = MetaData()
    metadata.reflect(bind=engine)
    
    if "reward_campaigns" not in metadata.tables:
        print("No reward_campaigns table found.")
        session.close()
        return
        
    campaigns = Table("reward_campaigns", metadata, autoload_with=engine)
    transactions = Table("transactions", metadata, autoload_with=engine)
    users = Table("users", metadata, autoload_with=engine)
    
    print("\n--- Users (First 10) ---")
    for row in session.execute(users.select().limit(10)).mappings():
        print(f"ID: {row['id']}, Email: {row['email']}, Role: {row['role']}")
        
    print("\n--- Campaigns ---")
    for row in session.execute(campaigns.select()).mappings():
        print(f"ID: {row['id']}, Title: {row['title']}, Type: {row['campaign_type']}, Metric: {row['target_metric']}, Val: {row['target_value']}, Active: {row['is_active']}, Activated At: {row['activated_at']}, Created At: {row['created_at']}")
        
    print("\n--- Transactions (First 10) ---")
    for row in session.execute(transactions.select().limit(10)).mappings():
        print(f"ID: {row['id']}, User ID: {row['user_id']}, Type: {row['tx_type']}, Status: {row['status']}, Plan: {row['data_plan_code']}, Amount: {row['amount']}, Created At: {row['created_at']}")
        
    session.close()

if __name__ == "__main__":
    inspect_db_file("sqlite:///./test.db", "TEST DB")
    inspect_db_file("sqlite:///./vtu.db", "VTU DB")
