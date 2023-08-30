import logging
import traceback
from typing import List, Union
import pickle
import os

import numpy as np
import pandas as pd

import yfinance as yf
import financedatabase as fd
from scripts.correlation_constants import FredSeries, Security
from scripts.clickhouse_functions import get_data_from_ch_stock_data
from config import STOCKS_DIR, FRED_DIR, DATA_DIR


# Configure the logger at the module level
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set to WARNING for production; DEBUG for development


def download_yfin_data(symbol):
    """Download historical data for a given symbol."""
    try:
        print(f"Downloading data for: {symbol}")
        df = yf.download(symbol)
        print("SAVING PARQUET to ", STOCKS_DIR / f'yahoo_daily/parquets/{symbol}.parquet')
        df.to_parquet(STOCKS_DIR / f'yahoo_daily/parquets/{symbol}.parquet')
        return df

    except Exception as e:
        print(f"EXCEPTION 1: {e}\nTraceback (most recent call last:\n{traceback.format_exc()}")
        return pd.Series()


def read_series_data(symbol, source):
    """Read data based on the source and data format."""
    if source == 'yahoo':
        file_path = STOCKS_DIR / f'yahoo_daily/parquets/{symbol}.parquet'
        if not os.path.exists(file_path):
            logger.warning(f'{file_path} does not exist')
            return None
        series = pd.read_parquet(file_path)
        if 'Date' in series.columns:
            series['Date'] = pd.to_datetime(series['Date'])
            series = series.set_index('Date')['Adj Close']
        else:
            series.index = pd.to_datetime(series.index)

        if 'Adj Close' in series.columns and not series_is_empty(series, symbol, file_path):
            series = series['Adj Close']
        else:
            return None

    elif source == 'alpaca':
        file_path = STOCKS_DIR / f'Alpaca_15m/parquets/{symbol}.parquet'
        if not os.path.exists(file_path):
            logger.warning(f'{file_path} does not exist')
            return None
        series = pd.read_parquet(file_path)
        series['timestamp'] = pd.to_datetime(series['timestamp'])
        series = series.set_index('timestamp')['close']
    else:
        raise ValueError(f"Unknown source: {source}")

    return series


def get_validated_security_data(symbol: str, start_date: str, end_date: str, source: str, dl_data: bool, use_ch: bool):
    """Get security data from file, make sure its within range and continuous"""
    if dl_data:
        security_data = download_yfin_data(symbol)
    elif use_ch:
        security_data = get_data_from_ch_stock_data(symbol, start_date)
    else:
        security_data = read_series_data(symbol, source)

    if security_data is None:
        return None

    # if not is_series_within_date_range(security_data, start_date, end_date):
    #     # print(f"{symbol:<6} hasn't been on the market for the required duration. Skipping...")
    #     return None

    if not is_series_continuous(security_data, symbol):
        return None

    if not is_series_non_repeating(security_data, symbol):
        return None

    # Detrend
    security_data = security_data.diff().dropna()

    return security_data


def series_is_empty(series, symbol, file_path, dl_data=True) -> bool:
    # Check for duplicate column names and NaN only columns
    duplicate_columns = series.columns[series.columns.duplicated()].tolist()
    nan_only_columns = series.columns[series.isna().all()].tolist()
    if duplicate_columns or nan_only_columns:
        logger.warning(f"Duplicate columns/NaN only columns for {symbol}. Skipping...")
        with open(DATA_DIR / 'files_to_delete.txt', 'a') as f:
            f.write(f'{symbol}\n')
        return True

    # Check if the stock has data
    if series is None or series.empty or series.shape[0] == 0 or series.isna().all().all() or \
            series.dropna().nunique().nunique() == 1:
        logger.warning(f"No data available for {symbol}. Skipping...")
        with open(DATA_DIR / 'files_to_delete.txt', 'a') as f:
            f.write(f'{symbol}\n')
        return True

    return False


def is_series_within_date_range(series, start_date: str, end_date: str) -> bool:
    """Check if series is within date range"""
    start_year, start_month, _ = map(int, start_date.split('-'))
    end_year, end_month, _ = map(int, end_date.split('-'))

    # Check if the stock has data since 'start year' and past 'end year'
    start_condition = series.index.min().year > start_year or (series.index.min().year == start_year and
                                                               series.index.min().month > start_month)
    end_condition = series.index.max().year < end_year or (series.index.max().year == end_year and
                                                           series.index.max().month < end_month)

    if start_condition or end_condition:
        # print(f"{symbol:<6} hasn't been on the market for the required duration. Skipping...")
        return False
    return True


def is_series_non_repeating(series, symbol):
    window_size = 10
    if series.rolling(window_size).apply(lambda x: np.all(x == x.iloc[0])).any():
        logger.warning(f"{symbol} has sections with {window_size} or more consecutive repeated values. Skipping...")
        return False
    return True


def is_series_continuous(series, symbol: str) -> bool:
    """Check if series is continuous, allowing for up to window_size consecutive missing datapoints"""
    window_size = 10  # Define the size of the rolling window
    if series.rolling(window_size).apply(lambda x: all(np.isnan(x))).any():
        logger.warning(f"{symbol} has sections with {window_size} or more consecutive NaN values. Skipping...")
        return False
    return True


def pickle_securities_objects(security: Union[Security, FredSeries]):
    """Pickles a security object to re-use the calculations"""
    file_path = DATA_DIR / f'Graphs/pickled_securities_objects/{security.symbol}.pkl'
    # Save dict of base security id's, and symbols that correlate with them for later use
    with open(file_path, 'wb') as pickle_file:
        pickle.dump(security, pickle_file)


def load_saved_securities(symbol: str, start_date: str) -> Union[Security, FredSeries]:
    """Loads and returns saved security objects from pickle files."""
    file_path = DATA_DIR / f'Graphs/pickled_securities_objects/{symbol}.pkl'

    if file_path.exists():
        with open(file_path, 'rb') as pickle_file:
            security = pickle.load(pickle_file)
        return security
    else:
        print(f"No saved data found for symbol: {symbol}")


def get_fred_md_series_list() -> List[FredSeries]:
    """Create list of FredSeries objects from fred_md_metadata csv"""
    fred_md_metadata = pd.read_csv(FRED_DIR / 'fred_md_metadata.csv')

    fred_series_list = [
        FredSeries(
            row['fred_md_id'], row['api_id'], row['title'], row['tcode'],
            row['frequency'], row['source_title'], row['source_link'],
            row['release_title'], row['release_link']
        )
        for _, row in fred_md_metadata.iterrows() if pd.notnull(row['fred_md_id']) and row['fred_md_id'] != ''
    ]

    return fred_series_list


def get_fredmd_series_data(series_id):
    """For getting a series from the FRED-MD dataset"""
    md_data = pd.read_csv(FRED_DIR / 'FRED_MD/MD_2023-08-02.csv')
    md_data = md_data.rename(columns={'sasdate': 'Date'})
    md_data['Date'] = pd.to_datetime(md_data['Date'])

    # Extract the 'series_id' column for correlation
    md_data = md_data.set_index('Date')[series_id]

    return md_data


def fit_data_to_time_range(series_data, start_date):
    start_datetime = pd.to_datetime(start_date)
    start_datetime = max(start_datetime, series_data.index.min())
    series_data = series_data.loc[start_datetime:]
    return series_data


def initialize_fin_db_stock_metadata():
    equities = fd.Equities()
    df = equities.select()
    df.to_csv(STOCKS_DIR / 'FinDB/fin_db_stock_data.csv')
