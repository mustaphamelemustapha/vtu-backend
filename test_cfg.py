from app.core.config import get_settings
try:
    s = get_settings()
    print("Settings created successfully.")
    print("Has database_url:", hasattr(s, 'database_url'))
    print("Dict:", s.dict().keys())
except Exception as e:
    import traceback
    traceback.print_exc()
