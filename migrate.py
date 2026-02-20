#!/usr/bin/env python3
"""
MySQL → PostgreSQL Migration Tool (mysql2pg)
Thin wrapper — all logic lives in the mysql2pg/ package.
"""
import sys
from mysql2pg.cli import main
from mysql2pg import console

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        console.print("\n[dim]Migration cancelled by user.[/dim]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        console.print("[dim]Please report this issue with the full traceback.[/dim]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
