import sys
import os

# Add app to path
sys.path.insert(0, os.path.abspath("."))

from app.providers.mzdata_provider import MZDataProvider
from app.core.config import get_settings

try:
    provider = MZDataProvider()
    print("Provider instantiated successfully.")
    print("Base URL:", provider.base_url)
    print("API Key length:", len(provider.api_key) if provider.api_key else 0)
except Exception as e:
    print("Error:", e)
