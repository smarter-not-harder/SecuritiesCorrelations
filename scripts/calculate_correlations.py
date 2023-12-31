import logging
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool, cpu_count
from typing import List, Union, Set
import warnings

import pandas as pd

from scripts.correlation_constants import Security, FredapiSeries, FredmdSeries, start_years
from scripts.file_reading_funcs import original_get_validated_security_data

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set to WARNING for production; DEBUG for development


def compute_correlation(series_data1: pd.Series, series_data2: pd.Series) -> float:
    return series_data1.corr(series_data2)


def get_correlation_for_series(main_security_data_detrended: pd.DataFrame,
                               security_data_detrended: pd.DataFrame) -> float:
    aligned_main, aligned_symbol = main_security_data_detrended.align(security_data_detrended, join='inner', axis=0)

    # After aligning, ensure column names are retained
    aligned_main.columns = main_security_data_detrended.columns
    aligned_symbol.columns = security_data_detrended.columns

    correlation = compute_correlation(aligned_main['main'], aligned_symbol['symbol'])

    return correlation


def define_top_correlations(all_main_securities: List[Security | FredmdSeries | FredapiSeries]) \
        -> List[Security | FredmdSeries | FredapiSeries]:
    num_symbols = 100

    # Define the correlation attributes and their corresponding positive and negative correlation attributes

    # Loop through each securities_main security
    for main_security in all_main_securities:
        # Loop through each correlation start_date
        for start_date in start_years:
            correlation_dict = main_security.all_correlations.get(start_date, {})

            if len(correlation_dict) == 0:
                continue

            # Sort the symbols by their correlation values in descending and ascending order
            sorted_symbols_desc = sorted(correlation_dict.keys(), key=correlation_dict.get, reverse=True)
            sorted_symbols_asc = sorted(correlation_dict.keys(), key=correlation_dict.get, reverse=False)

            # Add the top num_symbols positively correlated securities to the positive_correlations attribute
            for symbol in sorted_symbols_desc[:num_symbols]:
                correlated_security = Security(symbol)
                correlated_security.set_correlation(correlation_dict[symbol])
                main_security.positive_correlations[start_date].append(correlated_security)

            # Add the top num_symbols negatively correlated securities to the negative_correlations attribute
            for symbol in sorted_symbols_asc[:num_symbols]:
                correlated_security = Security(symbol)
                correlated_security.set_correlation(correlation_dict[symbol])
                main_security.negative_correlations[start_date].append(correlated_security)

            # Set the correlation_dict for the start_date to None
            main_security.all_correlations[start_date] = None

    return all_main_securities


def process_symbol(args):
    self, cache, symbol, start_date, end_date, source, dl_data, use_ch, all_main_securities = args
    security_data = self.get_validated_security_data(cache, symbol, start_date, end_date, source, dl_data, use_ch)
    if security_data is None:
        return

    result_list = []
    for single_main_security in all_main_securities:
        if isinstance(single_main_security, Security) and symbol == single_main_security.symbol:
            continue

        main_security_data_detrended = single_main_security.series_data_detrended[start_date]

        corr_float = get_correlation_for_series(main_security_data_detrended, security_data)

        if corr_float is not None:
            result_list.append((single_main_security, symbol, corr_float))

    return result_list


class CorrelationCalculator:
    """Calculates correlations between different series. symbols attribute is generally thousands long,
all_main_securities is generally only a few securities long"""

    def __init__(self, symbols, cache, debug=False):
        self.DEBUG = debug
        self.symbols = ['AAPL', 'MSFT', 'GME', 'UNH', 'SHEL', 'FNV', 'DIS', 'NFLX', 'VZ', 'TMUS', 'INTC'] \
            if self.DEBUG else symbols
        self.cache = cache

    def define_correlation_for_each_year(self, securities_list, end_date,
                                         source, dl_data, use_ch, use_multiprocessing):

        all_start_dates = ['2023', '2022', '2021']

        if use_multiprocessing:
            for start_date in all_start_dates:
                self.define_correlations_for_series_list_multiprocessing(securities_list, start_date, end_date, source,
                                                                         dl_data, use_ch)
        else:
            for start_date in all_start_dates:
                self.define_correlations_for_series_list(securities_list, start_date, end_date, source,
                                                         dl_data, use_ch)

        return securities_list

    def define_correlations_for_series_list_multiprocessing(self, all_main_securities, start_date, end_date, source,
                                                            dl_data, use_ch):

        args = [(self, self.cache, symbol, start_date, end_date, source, dl_data, use_ch, all_main_securities)
                for symbol in self.symbols]

        # Use multiprocessing to speed up the computation
        with Pool(cpu_count() // 4) as pool:
            all_results = pool.map(process_symbol, args)

        # Update the correlations in main_security objects
        for results in all_results:
            if results:
                for main_security, symbol, correlation_float in results:
                    if start_date not in main_security.all_correlations:
                        main_security.all_correlations[start_date] = {}
                    main_security.all_correlations[start_date][symbol] = correlation_float

        return all_main_securities

    @staticmethod
    def get_validated_security_data(cache, symbol, start_date, end_date, source, dl_data, use_ch):
        # Check if data exists in cache
        data = cache.get(symbol)
        if data is not None:
            # print(f"Cache hit for {symbol}. Total hits: {cache.get_hits()}, Total misses: {cache.get_misses()}")
            return data

        # If not in cache, compute the data and store it in the cache
        data = original_get_validated_security_data(symbol, start_date, end_date, source, dl_data, use_ch)
        if data is not None:
            cache.set(symbol, data)
        # print(f"Cache miss for {symbol}. Total hits: {cache.get_hits()}, Total misses: {cache.get_misses()}")
        return data

    def define_correlations_for_series_list(self, all_main_securities: Union[Set['Security'], Set['FredapiSeries']],
                                            start_date: str, end_date: str, source: str, dl_data: bool, use_ch: bool) \
            -> Set['Security']:
        """Main function for calculating the correlations for each Security against a list of other securities"""

        symbols = set(self.symbols)  # This changes the order of symbols
        all_main_securities_set = set(all_main_securities)

        for symbol in symbols:
            try:
                security_data_detrended = original_get_validated_security_data(symbol, start_date, end_date, source,
                                                                               dl_data, use_ch)
            except AttributeError:  # Better than checking if its None every time
                continue

            for main_security in all_main_securities_set:

                if isinstance(main_security, Security) and symbol == main_security.symbol:
                    continue  # Skips comparison if being compared to itself

                main_security_data_detrended = main_security.series_data_detrended[start_date]

                try:
                    correlation_float = get_correlation_for_series(main_security_data_detrended,
                                                                   security_data_detrended)
                except TypeError:
                    logger.warning(f'Skipping correlation calculation for {symbol} due to missing data.')
                    continue

                if start_date not in main_security.all_correlations:
                    main_security.all_correlations[start_date] = {}

                main_security.all_correlations[start_date][symbol] = correlation_float

        return all_main_securities

    def worker(self, symbol, all_main_securities_set, start_date, end_date, source, dl_data, use_ch):
        try:
            security_data = original_get_validated_security_data(symbol, start_date, end_date, source, dl_data, use_ch)
        except AttributeError:
            return

        for main_security in all_main_securities_set:
            if isinstance(main_security, Security) and symbol == main_security.symbol:
                continue

            main_security_data_detrended = main_security.series_data_detrended[start_date]

            try:
                correlation_float = get_correlation_for_series(main_security_data_detrended, security_data)
            except TypeError:
                logger.warning(f'Skipping correlation calculation for {symbol} due to missing data.')
                continue

            if start_date not in main_security.all_correlations:
                main_security.all_correlations[start_date] = {}

            main_security.all_correlations[start_date][symbol] = correlation_float

    def define_correlations_for_series_list_multithread(self, all_main_securities: Set[Security | FredmdSeries
                                                                                       | FredapiSeries],
                                                        start_date: str, end_date: str, source: str, dl_data: bool,
                                                        use_ch: bool) -> Set['Security']:

        symbols = set(self.symbols)
        all_main_securities_set = set(all_main_securities)

        # Define the number of threads; can be adjusted based on performance
        num_threads = cpu_count() // 4  # For instance, use 8 threads or the number of symbols, whichever is smaller

        # Using ThreadPoolExecutor to run worker functions in parallel
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            executor.map(self.worker, symbols, [all_main_securities_set] * len(symbols), [start_date] * len(symbols),
                         [end_date] * len(symbols), [source] * len(symbols), [dl_data] * len(symbols),
                         [use_ch] * len(symbols))

        return all_main_securities


if __name__ == '__main__':
    pass
    # Call the desired methods on the manager instance
    # manager.define_correlations_for_series_list(...)
