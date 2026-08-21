"""SQLAlchemy models for indexed blockchain data."""

from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.common.db import Base


class User(Base):
    """User authentication model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class IndexerState(Base):
    """Track the state of each indexer."""

    __tablename__ = "indexer_state"

    indexer_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_indexed_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running, stopped, error


class Trade(Base):
    """Individual trade records from the Bittensor network."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    is_sell: Mapped[bool] = mapped_column(Boolean, nullable=False)
    netuid: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # up to 1024
    tao: Mapped[int] = mapped_column(BigInteger, nullable=False)  # up to 21 * 10^15
    alpha: Mapped[int] = mapped_column(BigInteger, nullable=False)  # up to 21 * 10^15
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)  # up to 10^9
    address: Mapped[str] = mapped_column(String(48), nullable=False)  # SS58 address
    
    # Block information
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    # Extrinsic information
    extrinsic_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Transfer/Move information
    is_transferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_moved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_burned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_coldkey_swapped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_pure_move: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_trades_is_sell", "is_sell"),
        Index("ix_trades_netuid", "netuid"),
        Index("ix_trades_address", "address"),
        Index("ix_trades_block_number", "block_number"),
        Index("ix_trades_block_timestamp", "block_timestamp"),
        Index("ix_trades_is_transferred", "is_transferred"),
        Index("ix_trades_is_moved", "is_moved"),
        Index("ix_trades_is_burned", "is_burned"),
        Index("ix_trades_is_coldkey_swapped", "is_coldkey_swapped"),
        Index("ix_trades_is_pure_move", "is_pure_move"),
    )


class OHLCInterval(str, Enum):
    """OHLC candle intervals."""

    MIN_1 = "1m"
    MIN_5 = "5m"
    MIN_15 = "15m"
    HOUR_1 = "1H"
    HOUR_4 = "4H"
    DAY_1 = "1D"
    DAY_7 = "7D"


class OHLCBase:
    """Base mixin for OHLC candle data."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    netuid: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume_tao: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    volume_alpha: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OHLC1m(OHLCBase, Base):
    """1-minute OHLC candles."""

    __tablename__ = "ohlc_1m"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_1m_netuid_open_time"),
        Index("ix_ohlc_1m_netuid_open_time", "netuid", "open_time"),
    )


class OHLC5m(OHLCBase, Base):
    """5-minute OHLC candles."""

    __tablename__ = "ohlc_5m"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_5m_netuid_open_time"),
        Index("ix_ohlc_5m_netuid_open_time", "netuid", "open_time"),
    )


class OHLC15m(OHLCBase, Base):
    """15-minute OHLC candles."""

    __tablename__ = "ohlc_15m"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_15m_netuid_open_time"),
        Index("ix_ohlc_15m_netuid_open_time", "netuid", "open_time"),
    )


class OHLC1H(OHLCBase, Base):
    """1-hour OHLC candles."""

    __tablename__ = "ohlc_1h"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_1h_netuid_open_time"),
        Index("ix_ohlc_1h_netuid_open_time", "netuid", "open_time"),
    )


class OHLC4H(OHLCBase, Base):
    """4-hour OHLC candles."""

    __tablename__ = "ohlc_4h"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_4h_netuid_open_time"),
        Index("ix_ohlc_4h_netuid_open_time", "netuid", "open_time"),
    )


class OHLC1D(OHLCBase, Base):
    """1-day OHLC candles."""

    __tablename__ = "ohlc_1d"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_1d_netuid_open_time"),
        Index("ix_ohlc_1d_netuid_open_time", "netuid", "open_time"),
    )


class OHLC7D(OHLCBase, Base):
    """7-day OHLC candles."""

    __tablename__ = "ohlc_7d"
    __table_args__ = (
        UniqueConstraint("netuid", "open_time", name="uq_ohlc_7d_netuid_open_time"),
        Index("ix_ohlc_7d_netuid_open_time", "netuid", "open_time"),
    )


# Mapping from interval enum to model class
OHLC_MODELS = {
    OHLCInterval.MIN_1: OHLC1m,
    OHLCInterval.MIN_5: OHLC5m,
    OHLCInterval.MIN_15: OHLC15m,
    OHLCInterval.HOUR_1: OHLC1H,
    OHLCInterval.HOUR_4: OHLC4H,
    OHLCInterval.DAY_1: OHLC1D,
    OHLCInterval.DAY_7: OHLC7D,
}


class Transfer(Base):
    """Transfers of tokens between addresses."""

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Block information
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    # Extrinsic information
    extrinsic_index: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Transfer details
    netuid: Mapped[int] = mapped_column(Integer, nullable=False, index=True) # -1 for native TAO
    from_address: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    to_address: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    tao_amount: Mapped[int] = mapped_column(BigInteger, nullable=False) # in tao

    # Coldkey Swap flag
    is_coldkey_swap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class Proxy(Base):
    """Proxies for the Bittensor network."""

    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Block information
    delegator: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    delegatee: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    proxy_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)


class FailedTrade(Base):
    """Failed trade records from the Bittensor network."""

    __tablename__ = "failed_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Block/Extrinsic information
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    block_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    extrinsic_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    error_message: Mapped[str] = mapped_column(String(255), nullable=False)

    # Address information
    address: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    proxy_address: Mapped[str] = mapped_column(String(48), nullable=False, index=True)

    # Call information
    call_module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    call_function: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    call_args: Mapped[dict] = mapped_column(JSONB, nullable=False)
