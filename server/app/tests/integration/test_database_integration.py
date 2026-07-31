"""Integration tests for the database layer as a whole: initialization,
WAL mode, session management, and ORM relationships working together
end-to-end. See docs/architecture.md sections 5 and 15, and
docs/contracts.md section 8.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import inspect, text

from app.db import repository
from app.db.models import Base, HarnessBatch, Lead, LlmCall, Run
from app.db.session import build_engine
from app.schemas.run import LlmCallUsage, RunResult


class TestDatabaseInitialization:
    def test_init_db_creates_all_four_tables(self, db_engine) -> None:
        table_names = set(inspect(db_engine).get_table_names())
        assert {"runs", "llm_calls", "leads", "harness_batches"} <= table_names

    def test_init_db_is_idempotent(self, db_engine) -> None:
        # Calling create_all a second time against existing tables must not
        # raise (this is what app/main.py relies on at every process boot).
        Base.metadata.create_all(bind=db_engine)
        table_names = set(inspect(db_engine).get_table_names())
        assert {"runs", "llm_calls", "leads", "harness_batches"} <= table_names


class TestWalMode:
    def test_wal_mode_is_enabled_on_a_real_file_database(self, test_settings) -> None:
        """`:memory:` databases cannot run in WAL mode -- this test
        deliberately uses a real file (via `test_settings`, not the
        `db_engine`/`db_session` fixtures) so the assertion is meaningful."""
        engine = build_engine(test_settings)
        with engine.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert journal_mode == "wal"
        engine.dispose()

    def test_foreign_keys_pragma_is_enabled(self, test_settings) -> None:
        engine = build_engine(test_settings)
        with engine.connect() as conn:
            foreign_keys = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert foreign_keys == 1
        engine.dispose()


class TestSessionManagement:
    def test_get_db_closes_the_session_after_use(self, db_engine, monkeypatch) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.db import session as session_module

        factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

        gen = session_module.get_db()
        session = next(gen)
        repository.create_harness_batch(session, n_runs=0)

        with patch.object(session, "close", wraps=session.close) as mock_close:
            # Exhaust the generator to trigger its `finally: session.close()`.
            try:
                next(gen)
            except StopIteration:
                pass
            mock_close.assert_called_once()

    def test_session_scope_commits_on_success(self, db_engine, monkeypatch) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.db import session as session_module

        factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

        with session_module.session_scope() as session:
            repository.create_harness_batch(session, n_runs=1)

        # A fresh session must see the committed row.
        with factory() as verify_session:
            assert repository.get_latest_harness_batch(verify_session) is not None

    def test_session_scope_rolls_back_on_exception(self, db_engine, monkeypatch) -> None:
        """`session_scope` rolls back *uncommitted* work added directly to
        the session. (It cannot roll back an earlier `repository.py` call
        in the same block, since those already commit immediately -- see
        the docstring on `session_scope`.)"""
        from sqlalchemy.orm import sessionmaker

        from app.db import session as session_module

        factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)

        class _Boom(Exception):
            pass

        try:
            with session_module.session_scope() as session:
                session.add(HarnessBatch(n_runs=1))
                raise _Boom("simulated failure mid-block")
        except _Boom:
            pass

        with factory() as verify_session:
            assert repository.get_latest_harness_batch(verify_session) is None


class TestRelationshipsAndCrossEntityRoundTrip:
    def test_run_with_llm_calls_and_lead_round_trips_through_relationships(self, db_session) -> None:
        batch = repository.create_harness_batch(db_session, n_runs=1)

        run_result = RunResult(
            id="run-full",
            enquiry_text="Hi, I'm interested in whisky casks.",
            final_status="completed",
            llm_calls=[
                LlmCallUsage(
                    stage="planner",
                    model_provider="openai",
                    model_name="gpt-4o-mini",
                    prompt_tokens=100,
                    completion_tokens=50,
                    cost_usd=0.001,
                    latency_ms=500.0,
                )
            ],
        )
        run_row = repository.create_run(db_session, run_result, harness_batch_id=batch.id)

        lead = repository.create_lead(
            db_session,
            dedupe_hash="round-trip-hash",
            email="jane@example.com",
            status="accepted",
            source_run_id=run_row.id,
        )

        db_session.expire_all()  # force a re-fetch to prove data was actually persisted, not cached

        reloaded_run = repository.get_run(db_session, "run-full")
        assert reloaded_run is not None
        assert reloaded_run.harness_batch.id == batch.id
        assert len(reloaded_run.llm_calls) == 1
        assert reloaded_run.llm_calls[0].stage == "planner"
        assert reloaded_run.lead is not None
        assert reloaded_run.lead.id == lead.id

        reloaded_batch = repository.get_latest_harness_batch(db_session)
        assert reloaded_batch is not None
        assert reloaded_batch.runs[0].id == run_row.id

    def test_deleting_a_run_cascades_to_its_llm_calls(self, db_session) -> None:
        run_result = RunResult(
            id="run-to-delete",
            enquiry_text="...",
            final_status="completed",
            llm_calls=[
                LlmCallUsage(
                    stage="planner",
                    model_provider="openai",
                    model_name="gpt-4o-mini",
                    prompt_tokens=10,
                    completion_tokens=5,
                    cost_usd=0.0001,
                    latency_ms=10.0,
                )
            ],
        )
        run_row = repository.create_run(db_session, run_result)
        llm_call_id = run_row.llm_calls[0].id

        db_session.delete(run_row)
        db_session.commit()

        assert db_session.get(Run, "run-to-delete") is None
        assert db_session.get(LlmCall, llm_call_id) is None

    def test_all_four_tables_are_reachable_via_the_orm(self, db_session) -> None:
        """Sanity check that every model in docs/architecture.md section 5's
        ERD is mapped and queryable."""
        for model in (Run, LlmCall, Lead, HarnessBatch):
            assert db_session.query(model).count() == 0
