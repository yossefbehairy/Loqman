"""SQLite rebuild for mission runs; preserves referenced chat runs and events."""
from sqlalchemy import MetaData, inspect
from sqlalchemy.schema import CreateIndex, CreateTable


def upgrade_run_table(engine, metadata) -> None:
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Run schema migration currently supports SQLite only")
    columns = {c["name"]: c for c in inspect(engine).get_columns("conversation_runs")}
    new_fields = {"kind", "mission_id", "mission_task_id", "attempt", "result_text"}
    if columns.get("conversation_id", {}).get("nullable") and new_fields.issubset(columns):
        return
    table = metadata.tables["conversation_runs"]
    if set(columns) - set(table.columns.keys()):
        raise RuntimeError("Run table contains unrecognized columns; automatic rebuild refused")
    temp_metadata = MetaData()
    for existing in metadata.sorted_tables:
        existing.to_metadata(temp_metadata)
    replacement = table.to_metadata(temp_metadata, name="conversation_runs_upgrade")
    preparer = engine.dialect.identifier_preparer
    names = ", ".join(preparer.quote(name) for name in columns)

    with engine.connect() as connection:
        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.execute(CreateTable(replacement))
            connection.exec_driver_sql(
                f"INSERT INTO conversation_runs_upgrade ({names}) SELECT {names} FROM conversation_runs"
            )
            connection.exec_driver_sql("DROP TABLE conversation_runs")
            connection.exec_driver_sql("ALTER TABLE conversation_runs_upgrade RENAME TO conversation_runs")
            for index in table.indexes:
                connection.execute(CreateIndex(index))
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Run schema migration failed foreign-key validation")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql(f"PRAGMA foreign_keys={1 if enabled else 0}")
            connection.commit()
