"""Application context wiring shared resources without global singletons."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AppContext:
    """Carries shared resources to GUI widgets and orchestrators."""

    settings: Any = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("pa_agent"))
    event_bus: Any = None

    # Data layer
    data_source: Any = None       # DataSource implementation
    buffer: Any = None            # KlineBuffer
    market_data_cache: Any = None # Latest successful chart bars for analysis fallback

    # AI / orchestration layer
    client: Any = None            # DeepSeekClient
    assembler: Any = None         # PromptAssembler
    router: Any = None            # route_strategy_files callable
    validator: Any = None         # JsonValidator
    pending_writer: Any = None    # PendingWriter
    exp_reader: Any = None        # ExperienceReader
    ledger: Any = None            # SessionTokenLedger

    @classmethod
    def bootstrap(cls) -> "AppContext":
        """Wire all real components and return a fully initialised AppContext."""
        from pa_agent.config.paths import (
            SETTINGS_JSON_PATH,
            RECORDS_PENDING_DIR,
            EXPERIENCE_DIR,
            PROMPT_DIR,
        )
        from pa_agent.config.settings import load_settings
        from pa_agent.util.logging import configure_logging, update_api_key
        from pa_agent.util.event_bus import EventBus
        from pa_agent.security.secret_store import mask_secret
        from pa_agent.data.kline_buffer import KlineBuffer
        from pa_agent.data.market_data_cache import MarketDataCache
        from pa_agent.ai.deepseek_client import DeepSeekClient
        from pa_agent.ai.prompt_assembler import PromptAssembler
        from pa_agent.ai.router import route_strategy_files
        from pa_agent.ai.json_validator import JsonValidator
        from pa_agent.ai.session_ledger import SessionTokenLedger
        from pa_agent.records.pending_writer import PendingWriter
        from pa_agent.records.experience_reader import ExperienceReader

        # ── Settings ──────────────────────────────────────────────────────────
        settings = load_settings(SETTINGS_JSON_PATH)

        # ── Logging (with API key masking) ────────────────────────────────────
        configure_logging(api_key=settings.provider.api_key)

        app_logger = logging.getLogger("pa_agent")

        # ── Event bus ─────────────────────────────────────────────────────────
        event_bus = EventBus()

        # ── Data layer ────────────────────────────────────────────────────────
        buffer = KlineBuffer(capacity=1000)
        market_data_cache = MarketDataCache()
        source_kind = getattr(settings.general, "data_source_kind", "akshare_a_share")
        if source_kind == "mt5":
            from pa_agent.data.mt5 import MT5Source

            data_source = MT5Source()
        else:
            from pa_agent.data.a_share import AShareSource

            data_source = AShareSource(
                use_proxy=getattr(settings.general, "market_data_use_proxy", False),
                timeout_sec=getattr(settings.general, "market_data_timeout_sec", 8.0),
                retry=getattr(settings.general, "market_data_retry", 1),
            )

        # Subscribe to the last-used symbol/timeframe from settings
        try:
            symbol = settings.general.last_symbol
            timeframe = settings.general.last_timeframe
            checker = getattr(data_source, "is_symbol_available", None)
            if callable(checker) and not checker(symbol):
                symbol = "600519"
            supported_timeframes = data_source.supported_timeframes()
            if timeframe not in supported_timeframes:
                timeframe = "15m"
            settings.general.last_symbol = symbol
            settings.general.last_timeframe = timeframe
            data_source.connect()
            data_source.subscribe(symbol, timeframe)
            app_logger.info(
                "Subscribed to %s %s",
                symbol,
                timeframe,
            )
        except Exception as exc:  # noqa: BLE001
            app_logger.warning("Initial data source subscription failed: %s", exc)

        # ── AI client ─────────────────────────────────────────────────────────
        client = DeepSeekClient(settings=settings.provider, logger_=app_logger)

        # ── Prompt assembler ──────────────────────────────────────────────────
        exp_reader = ExperienceReader(experience_dir=EXPERIENCE_DIR, logger=app_logger)
        assembler = PromptAssembler(
            prompt_dir=PROMPT_DIR,
            experience_reader=exp_reader,
        )

        # ── Validator & router ────────────────────────────────────────────────
        validator = JsonValidator()
        router = route_strategy_files

        # ── Pending writer ────────────────────────────────────────────────────
        pending_writer = PendingWriter(
            pending_dir=RECORDS_PENDING_DIR,
            event_bus=event_bus,
            api_key=settings.provider.api_key,
        )

        # ── Session ledger ────────────────────────────────────────────────────
        ledger = SessionTokenLedger(
            context_window=settings.provider.context_window,
            warn_pct=settings.general.context_warning_threshold_pct,
        )

        return cls(
            settings=settings,
            logger=app_logger,
            event_bus=event_bus,
            data_source=data_source,
            buffer=buffer,
            market_data_cache=market_data_cache,
            client=client,
            assembler=assembler,
            router=router,
            validator=validator,
            pending_writer=pending_writer,
            exp_reader=exp_reader,
            ledger=ledger,
        )
