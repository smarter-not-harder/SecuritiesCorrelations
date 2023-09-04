from pathlib import Path
import pandas as pd

ALPACA_BASE_URL = 'https://paper-api.alpaca.markets'

PROJECT_ROOT = Path("C:/Users/afshin/IdeaProjects/apca_api_trader/")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
STOCKS_DIR = DATA_DIR / 'Stock_data'
FRED_DIR = DATA_DIR / 'FRED'

etf_metadata = pd.read_csv(STOCKS_DIR / 'FinDB/updated_fin_db_etf_data.csv', index_col='symbol')
stock_metadata = pd.read_csv(STOCKS_DIR / 'FinDB/updated_fin_db_stock_data.csv', index_col='symbol')
index_metadata = pd.read_csv(STOCKS_DIR / 'FinDB/updated_fin_db_indices_data.csv', index_col='symbol')

securities_metadata = (etf_metadata, stock_metadata, index_metadata)