"""
chart_service.py — yt-video

Generates financial charts using yfinance + mplfinance (both free).
Also provides live price snapshots for ticker bar and market data for script.

Identical to v1 pipeline; imports updated for standalone yt-video package.
All data sourced from Yahoo Finance — free, no API key required.
"""

import os
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from logger import logger

_CHART_DIR = "output/charts"
_W, _H     = 1920, 1080
_DPI       = 100   # figsize (19.2, 10.8) → exactly 1920×1080


def _to_yf_ticker(symbol: str) -> str:
    if symbol == "NIFTY":
        return "^NSEI"
    return f"{symbol}.NS"


def _fetch_ohlcv(symbol: str, days: int = 90) -> pd.DataFrame:
    ticker = _to_yf_ticker(symbol)
    end    = datetime.today()
    start  = end - timedelta(days=days)
    df = yf.download(
        ticker, start=start, end=end,
        interval="1d", progress=False, auto_adjust=True,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    if df.empty:
        raise ValueError(f"No data returned for {symbol} ({ticker})")
    df.index = df.index.tz_localize(None)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _dark_style():
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#00FF78", down="#FF5050",
            edge="inherit", wick="inherit", volume="inherit",
        ),
        facecolor="#0D0D0D",
        edgecolor="#0D0D0D",
        figcolor="#0D0D0D",
        gridcolor="#1A1A2E",
        gridstyle="--",
        gridaxis="both",
        y_on_right=True,
        rc={
            "axes.labelcolor": "white",
            "xtick.color":     "#AAAAAA",
            "ytick.color":     "#AAAAAA",
            "font.family":     "DejaVu Sans",
        },
    )


def _add_overlays(ax, df: pd.DataFrame, symbol: str) -> None:
    """Add 20 EMA, 50 EMA lines and watermark to the price axis."""
    close = df["Close"].squeeze()
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()

    ax.plot(range(len(df)), ema20.values, color="#FFD700", linewidth=1.5, label="EMA 20", alpha=0.85)
    ax.plot(range(len(df)), ema50.values, color="#00BFFF", linewidth=1.5, label="EMA 50", alpha=0.85)
    ax.legend(loc="upper left", fontsize=14, facecolor="#1A1A2E", edgecolor="#333355", labelcolor="white")
    ax.text(0.99, 0.02, "NSE • Data via Yahoo Finance",
            transform=ax.transAxes, fontsize=11, color="#444466", ha="right", va="bottom")


def _add_stats_banner(fig, df: pd.DataFrame, symbol: str) -> None:
    """Overlay a top stats bar: Symbol | LTP | Change% | 52W High | 52W Low."""
    from services.script_service import SYMBOL_NAMES

    close      = df["Close"].squeeze()
    ltp        = float(close.iloc[-1])
    prev       = float(close.iloc[-2]) if len(close) > 1 else ltp
    change_pct = (ltp - prev) / prev * 100
    high52     = float(close.max())
    low52      = float(close.min())
    direction  = "▲" if change_pct >= 0 else "▼"
    color      = "#00FF78" if change_pct >= 0 else "#FF5050"
    name       = SYMBOL_NAMES.get(symbol, symbol)

    banner_h = 0.065
    ax_banner = fig.add_axes([0, 1 - banner_h, 1, banner_h])
    ax_banner.set_facecolor("#0A0A14")
    ax_banner.axis("off")

    parts = [
        (f"  {name}", "white",   28, "bold"),
        (f"  ₹{ltp:,.2f}", "white",  32, "bold"),
        (f"  {direction} {abs(change_pct):.2f}%", color, 28, "bold"),
        (f"  |  52W H: ₹{high52:,.0f}", "#AAAAAA", 22, "normal"),
        (f"  52W L: ₹{low52:,.0f}", "#AAAAAA", 22, "normal"),
    ]
    x = 0.01
    for text, col, size, weight in parts:
        ax_banner.text(x, 0.5, text, color=col, fontsize=size,
                       fontweight=weight, va="center", transform=ax_banner.transAxes)
        x += len(text) * 0.012


def _render_chart(symbol: str, df: pd.DataFrame) -> str:
    """Render a single candlestick chart PNG and return its path."""
    os.makedirs(_CHART_DIR, exist_ok=True)
    out_path = os.path.join(_CHART_DIR, f"{symbol}.png")

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=_dark_style(),
        volume=True,
        figsize=(_W / _DPI, _H / _DPI),
        returnfig=True,
        panel_ratios=(3, 1),
    )
    ax_price = axes[0]
    _add_overlays(ax_price, df, symbol)
    _add_stats_banner(fig, df, symbol)

    fig.savefig(out_path, dpi=_DPI, bbox_inches="tight",
                facecolor="#0D0D0D", edgecolor="none")
    plt.close(fig)
    logger.info(f"Chart saved → {out_path}")
    return out_path


def capture_charts(movers: list[str]) -> dict[str, str]:
    """
    Render candlestick charts for NIFTY + each mover symbol.
    Returns { symbol: path } for all successfully rendered charts.
    """
    symbols = ["NIFTY"] + movers
    results: dict[str, str] = {}

    for symbol in symbols:
        try:
            df   = _fetch_ohlcv(symbol)
            path = _render_chart(symbol, df)
            results[symbol] = path
        except Exception as exc:
            logger.warning(f"Chart failed for {symbol}: {exc}")

    return results


def get_stock_snapshot(symbol: str) -> dict | None:
    """
    Fetch live price snapshot for ticker bar and stat cards.
    Returns { price, change_pct } or None on failure.
    """
    try:
        ticker = yf.Ticker(_to_yf_ticker(symbol))
        info   = ticker.fast_info
        price  = round(float(info.last_price), 2)
        prev   = round(float(info.previous_close), 2)
        change = round((price - prev) / prev * 100, 2) if prev else 0.0
        return {"price": price, "change_pct": change}
    except Exception as exc:
        logger.warning(f"Snapshot failed for {symbol}: {exc}")
        return None


def fetch_market_data(movers: list[str]) -> dict:
    """
    Fetch comprehensive OHLCV + 52W data for script generation.
    Returns { symbol: { name, ltp, change_pct, open, high, low, prev_close,
                        week52_high, week52_low } }
    """
    from services.script_service import SYMBOL_NAMES

    symbols = ["NIFTY"] + movers
    result: dict  = {}

    for symbol in symbols:
        try:
            df   = _fetch_ohlcv(symbol, days=365)
            df90 = df.tail(90)
            close = df["Close"].squeeze()

            ltp        = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) > 1 else ltp
            change_pct = round((ltp - prev_close) / prev_close * 100, 2)
            today      = df90.iloc[-1]

            result[symbol] = {
                "name":        SYMBOL_NAMES.get(symbol, symbol),
                "ltp":         round(ltp, 2),
                "change_pct":  change_pct,
                "open":        round(float(today["Open"]), 2),
                "high":        round(float(today["High"]), 2),
                "low":         round(float(today["Low"]),  2),
                "prev_close":  round(prev_close, 2),
                "week52_high": round(float(close.max()), 2),
                "week52_low":  round(float(close.min()), 2),
            }
        except Exception as exc:
            logger.warning(f"Market data failed for {symbol}: {exc}")

    return result
