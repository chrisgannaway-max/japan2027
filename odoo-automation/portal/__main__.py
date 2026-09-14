"""python -m portal serve [--port 8000]     run the site
   python -m portal hash <password>         print a password hash for users.yaml
   python -m portal seed                    PORTAL_STORE=db: load properties/users from the config files"""
import sys

if len(sys.argv) >= 3 and sys.argv[1] == "hash":
    from .auth import hash_password
    print(hash_password(sys.argv[2]))
elif len(sys.argv) >= 2 and sys.argv[1] == "seed":
    import os
    os.environ.setdefault("PORTAL_STORE", "db")
    from .app import CONFIG, USERS, state
    if state.store is None:
        sys.exit("set PORTAL_STORE=db first")
    counts = state.store.import_from_yaml(CONFIG, USERS, overwrite="--overwrite" in sys.argv)
    print(f"imported {counts['properties']} properties and {counts['users']} users into {state.store.db_path}")
elif len(sys.argv) >= 2 and sys.argv[1] == "serve":
    import uvicorn
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    uvicorn.run("portal.app:app", host="0.0.0.0", port=port)
else:
    print(__doc__)
