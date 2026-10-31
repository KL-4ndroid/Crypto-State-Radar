"""Source metadata is a contract, not evidence of an installed collector."""
from dataclasses import dataclass, asdict
from datetime import timedelta
from .causal import epoch_time
from .contracts import stamp
from ..retry_policy import utc

OFFICIAL_MARKET_DOC='https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
PUBLIC_DATA_DOC='https://github.com/binance/binance-public-data'


@dataclass(frozen=True)
class SourceContract:
    source: str
    endpoint: str
    auth_requirement: str
    retention: str
    expected_update_interval: str
    timestamp_semantics: str
    timestamp_unit: str
    documentation: str = OFFICIAL_MARKET_DOC
    missing_behavior: str = 'UNAVAILABLE_WITH_REASON; never zero-fill'
    contract_version: str = 'binance-source-contract-2'
    collector_status: str = 'NOT_ENABLED_IN_PRODUCTION'


SOURCES = {c.source:c for c in [
    SourceContract('oi','/futures/data/openInterestHist','NONE_DOCUMENTED','LATEST_MONTH','5m','PERIOD_END','ms'),
    SourceContract('taker','/futures/data/takerlongshortRatio','NONE_DOCUMENTED','LATEST_30D','5m','PERIOD_START','ms'),
    SourceContract('top_account','/futures/data/topLongShortAccountRatio','X-MBX-APIKEY','LATEST_30D','5m','PERIOD_END','ms'),
    SourceContract('top_position','/futures/data/topLongShortPositionRatio','X-MBX-APIKEY','LATEST_30D','5m','PERIOD_END','ms'),
    SourceContract('global_accounts','/futures/data/globalLongShortAccountRatio','NONE_DOCUMENTED','LATEST_30D','5m','PERIOD_END','ms'),
    SourceContract('funding_settlement','/fapi/v1/fundingRate','NONE_DOCUMENTED','NO_30D_LIMIT_DOCUMENTED','CONTRACT_SPECIFIC_SETTLEMENT','SETTLEMENT_TIME','ms'),
    SourceContract('premium_mark_index','/fapi/v1/premiumIndex','NONE_DOCUMENTED','CURRENT_SNAPSHOT','SNAPSHOT_POLL_5m','RESPONSE_TIME','ms'),
    SourceContract('premium_history','/fapi/v1/premiumIndexKlines','NONE_DOCUMENTED','VERIFY_BACKFILL_PER_SYMBOL','5m','CANDLE_CLOSE','ms'),
    SourceContract('spot_aggtrades','https://api.binance.com/api/v3/aggTrades','NONE_DOCUMENTED','BOUNDED_REST_PLUS_DAILY_ARCHIVE','EVENT','TRADE_TIME','ms',PUBLIC_DATA_DOC),
    SourceContract('perp_aggtrades','/fapi/v1/aggTrades','NONE_DOCUMENTED','LATEST_48H_REST_PLUS_DAILY_ARCHIVE','EVENT','TRADE_TIME','ms',PUBLIC_DATA_DOC),
    SourceContract('spot_archive_2025_plus','https://data.binance.vision/data/spot','NONE','DAILY_MONTHLY_FILES_REVISIONED','NEXT_DAY_EXACT_RELEASE_UNKNOWN','TRADE_TIME','us',PUBLIC_DATA_DOC),
    SourceContract('perp_archive','https://data.binance.vision/data/futures/um','NONE','DAILY_MONTHLY_FILES_REVISIONED','NEXT_DAY_EXACT_RELEASE_UNKNOWN','TRADE_TIME','ms',PUBLIC_DATA_DOC),
    SourceContract('spot_aggtrades_5m','DERIVED_FROM_SPOT_AGGTRADES','NONE','OWNED_ARCHIVE','5m_WHEN_COMPLETE','WINDOW_END','ms',PUBLIC_DATA_DOC,collector_status='LOCAL_PILOT_DERIVED'),
    SourceContract('perp_aggtrades_5m','DERIVED_FROM_PERP_AGGTRADES','NONE','OWNED_ARCHIVE','5m_WHEN_COMPLETE','WINDOW_END','ms',PUBLIC_DATA_DOC,collector_status='LOCAL_PILOT_DERIVED'),
    SourceContract('smart_signal','UNCONFIRMED_PUBLIC_DEVELOPER_ENDPOINT','UNCONFIRMED','UNCONFIRMED','UNCONFIRMED','UNKNOWN','ms',
        'https://www.binance.com/en/support/faq/detail/197ed63fbca34124bae53d8e6d4c9d29',collector_status='OPTIONAL_UNAVAILABLE')
]}


def metadata():
    return [asdict(contract) for contract in SOURCES.values()]


def observed_times(source,raw_timestamp,observed_at,period_seconds=None):
    """Availability is when this collector actually saw a complete observation.

    Historical downloads never acquire fictional historical publication times.
    """
    contract=SOURCES[source]
    if contract.timestamp_semantics=='UNKNOWN':raise ValueError('unknown source timestamp semantics')
    effective=epoch_time(raw_timestamp,contract.timestamp_unit)
    if contract.timestamp_semantics=='PERIOD_START':
        if not period_seconds or period_seconds<=0:raise ValueError('interval length required')
        effective=stamp(utc(effective)+timedelta(seconds=period_seconds))
    if effective>stamp(observed_at):raise ValueError('partial interval or future source time')
    return {'effective_time':effective,'availability_time':stamp(observed_at),'availability_basis':'OBSERVED'}
