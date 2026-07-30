# Migrations

Alembic-managed schema migrations for the SQLAlchemy models in `app/db/models.py`.

No migration has been generated yet. Once the models stabilize, generate the
initial one from `server/`:

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```
